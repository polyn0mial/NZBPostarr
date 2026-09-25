"""Application self-update and rollback helpers.

This module provides a conservative updater for source-based installs:
- checks GitHub tags/releases for newer versions
- installs updates from GitHub ZIP archives or uploaded ZIPs
- creates pre-update snapshots for rollback
- restores snapshots and schedules a self-restart
- deletes files a release dropped (release manifest) and the startup removal list
"""

from __future__ import annotations

import filecmp
import json
import re
import shutil
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests
from loguru import logger
from packaging.version import InvalidVersion, Version

from version import __version__
from core.config import APP_ROOT
from logic.system import backup
from logic.system.backup import (
    STATE_DIR,
    _create_backup_snapshot,
    _is_protected,
    _now_iso,
    _read_json_file,
    _restore_from_backup_zip,
    _safe_member_path,
)
from logic.system.lifecycle import schedule_restart

GITHUB_OWNER = "polyn0mial"
GITHUB_REPO = "nzbpostarr"
_GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

STATE_FILE = STATE_DIR / "state.json"

_CHECK_TTL_SECONDS = 1800
_OP_LOCK = threading.Lock()

RELEASE_MANIFEST_NAME = "release-manifest.json"
REMOVED_PATHS_FILE = Path(__file__).with_name("removed_paths.txt")

class UpdateError(RuntimeError):
    """Updater-specific operational error."""


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _default_state() -> Dict[str, Any]:
    return {
        "current_version": __version__,
        "latest_version": None,
        "update_available": False,
        "last_checked_at": None,
        "last_error": None,
        "last_installed_from": None,
    }


def _load_state() -> Dict[str, Any]:
    state = _read_json_file(STATE_FILE, _default_state())
    state.setdefault("current_version", __version__)
    state.setdefault("latest_version", None)
    state.setdefault("update_available", False)
    state.setdefault("last_checked_at", None)
    state.setdefault("last_error", None)
    state.setdefault("last_installed_from", None)
    return state


def _save_state(state: Dict[str, Any]) -> None:
    _write_json_file(STATE_FILE, state)


def _coerce_version(version: str) -> Optional[Version]:
    raw = (version or "").strip()
    if not raw:
        return None

    normalized = raw[1:] if raw.lower().startswith("v") else raw
    try:
        return Version(normalized)
    except InvalidVersion:
        return None


def _version_sort_key(version: str) -> tuple[int, Version, str]:
    parsed = _coerce_version(version)
    if parsed is not None:
        return (1, parsed, "")
    return (0, Version("0"), version.lower())


def _is_newer_version(candidate: str, current: str) -> bool:
    candidate_version = _coerce_version(candidate)
    current_version = _coerce_version(current)

    if candidate_version is None:
        return False
    if current_version is None:
        return True
    return candidate_version > current_version


def _format_version_label(version: Optional[str]) -> str:
    if not version:
        return "unknown"
    version = str(version).strip()
    return version if version.startswith("v") else f"v{version}"


def _headers() -> Dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"NZBPostarr/{__version__}",
    }


def _fetch_github_releases(limit: int = 20) -> List[Dict[str, Any]]:
    max_items = max(1, min(limit, 50))
    releases: List[Dict[str, Any]] = []

    rel_url = f"{_GITHUB_API_BASE}/releases?per_page={max_items}"
    resp = requests.get(rel_url, headers=_headers(), timeout=(5, 15))
    if resp.ok:
        payload = resp.json()
        if isinstance(payload, list):
            for item in payload:
                if item.get("draft"):
                    continue
                tag = str(item.get("tag_name") or "").strip()
                if not tag:
                    continue
                releases.append(
                    {
                        "version": tag,
                        "title": item.get("name") or tag,
                        "prerelease": bool(item.get("prerelease", False)),
                        "published_at": item.get("published_at"),
                        "html_url": item.get("html_url"),
                        "zip_url": item.get("zipball_url"),
                    }
                )

    if releases:
        releases.sort(key=lambda r: _version_sort_key(str(r.get("version", ""))), reverse=True)
        return releases[:max_items]

    tags_url = f"{_GITHUB_API_BASE}/tags?per_page={max_items}"
    tags_resp = requests.get(tags_url, headers=_headers(), timeout=(5, 15))
    tags_resp.raise_for_status()
    tags = tags_resp.json()
    if not isinstance(tags, list):
        return []

    for item in tags:
        tag = str(item.get("name") or "").strip()
        if not tag:
            continue
        releases.append(
            {
                "version": tag,
                "title": tag,
                "prerelease": False,
                "published_at": None,
                "html_url": f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/{tag}",
                "zip_url": f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/archive/refs/tags/{tag}.zip",
            }
        )

    releases.sort(key=lambda r: _version_sort_key(str(r.get("version", ""))), reverse=True)
    return releases[:max_items]


def _pick_release(version: Optional[str] = None) -> Dict[str, Any]:
    releases = _fetch_github_releases(limit=30)
    if not releases:
        raise UpdateError("No GitHub releases/tags were found.")

    if version:
        target = version.strip().lower()
        for rel in releases:
            v = rel["version"]
            if v.lower() == target or v.lower().lstrip("v") == target.lstrip("v"):
                return rel
        raise UpdateError(f"Version '{version}' was not found on GitHub.")

    for rel in releases:
        if not rel.get("prerelease"):
            return rel
    return releases[0]


def _download_zip(url: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(prefix="nzbp-update-", suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        with requests.get(url, headers=_headers(), stream=True, timeout=(8, 60)) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)
        return tmp_path
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _extract_zip(zip_path: Path) -> Path:
    if not zip_path.exists():
        raise UpdateError(f"ZIP not found: {zip_path}")

    tmp_dir = Path(tempfile.mkdtemp(prefix="nzbp-extract-"))

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.infolist():
                rel = _safe_member_path(member.filename)
                if rel is None:
                    continue

                dst = tmp_dir / rel
                if member.is_dir():
                    dst.mkdir(parents=True, exist_ok=True)
                    continue

                dst.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as src, open(dst, "wb") as out:
                    shutil.copyfileobj(src, out)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise UpdateError(f"Invalid ZIP archive: {exc}") from exc

    return tmp_dir


def _locate_project_root(extracted_root: Path) -> Path:
    # Direct layout.
    if (extracted_root / "app.py").exists() and (extracted_root / "version.py").exists():
        return extracted_root

    # Typical GitHub layout: single top-level folder.
    for child in extracted_root.iterdir():
        if child.is_dir() and (child / "app.py").exists() and (child / "version.py").exists():
            return child

    raise UpdateError("Could not locate a valid NZBPostarr project root in the ZIP.")


def _read_version_from_tree(project_root: Path) -> Optional[str]:
    version_file = project_root / "version.py"
    if not version_file.exists():
        return None
    try:
        content = version_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if not m:
        return None
    return m.group(1).strip() or None


def _read_release_manifest(path: Path) -> Optional[Set[str]]:
    data = _read_json_file(path, {})
    files = data.get("files")
    if not isinstance(files, list):
        return None
    return {str(name) for name in files if isinstance(name, str)}


def _remove_app_file(rel_path: Path, reason: str) -> bool:
    """Delete one regular file inside APP_ROOT; never a protected path, directory or link."""
    safe = _safe_member_path(rel_path.as_posix())
    if safe is None or _is_protected(safe):
        return False
    target = APP_ROOT / safe
    try:
        target.resolve().relative_to(APP_ROOT.resolve())
    except (OSError, ValueError):
        return False
    if target.is_symlink() or not target.is_file():
        return False
    try:
        target.unlink()
    except OSError as exc:
        logger.warning(f"Updater: could not delete {safe.as_posix()} ({reason}): {exc}")
        return False
    logger.info(f"Updater: deleted {safe.as_posix()} ({reason})")
    return True


def _apply_project_tree(project_root: Path) -> Dict[str, int]:
    """Copy changed files from extracted update tree into APP_ROOT.

    When both the installed and the new release carry a release manifest, files
    listed in the old one and absent from the new one are deleted (never a
    protected path). Callers take the pre-apply backup first.
    """
    updated = 0
    skipped = 0
    removed = 0
    previous = _read_release_manifest(APP_ROOT / RELEASE_MANIFEST_NAME)
    incoming = _read_release_manifest(project_root / RELEASE_MANIFEST_NAME)

    for src in project_root.rglob("*"):
        if not src.is_file():
            continue

        rel = src.relative_to(project_root)
        if _is_protected(rel):
            skipped += 1
            continue

        dst = APP_ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)

        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue

        shutil.copy2(src, dst)
        updated += 1

    if previous is not None and incoming is not None:
        for name in sorted(previous - incoming):
            if _remove_app_file(Path(name), "dropped by the new release"):
                removed += 1

    return {"updated": updated, "skipped": skipped, "removed": removed}


def cleanup_removed_paths() -> List[str]:
    """Delete the files listed in removed_paths.txt; run once at startup, idempotent.

    Needed because a release applied by an older updater never deletes files.
    Only regular files inside the app root are touched, never a protected path.
    """
    try:
        lines = REMOVED_PATHS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    deleted: List[str] = []
    for line in lines:
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if _remove_app_file(Path(name), "listed in removed_paths.txt"):
            deleted.append(name)
    return deleted


def _set_install_state(target_version: Optional[str], source: str, check_error: Optional[str] = None) -> None:
    state = _load_state()
    if target_version:
        state["current_version"] = target_version
        state["latest_version"] = target_version
        state["update_available"] = False
        state["last_error"] = None
    else:
        state["last_error"] = check_error

    state["last_checked_at"] = _now_iso()
    state["last_installed_from"] = source
    _save_state(state)


def get_releases(limit: int = 20) -> List[Dict[str, Any]]:
    releases = _fetch_github_releases(limit=limit)
    for rel in releases:
        rel["version_label"] = _format_version_label(rel.get("version"))
    return releases


def _maybe_refresh_status(force: bool = False) -> Dict[str, Any]:
    state = _load_state()
    now = time.time()

    should_refresh = force
    if not should_refresh:
        last_checked = state.get("last_checked_at")
        if not last_checked:
            should_refresh = True
        else:
            try:
                last_ts = datetime.fromisoformat(str(last_checked)).timestamp()
                should_refresh = (now - last_ts) >= _CHECK_TTL_SECONDS
            except ValueError:
                should_refresh = True

    if not should_refresh:
        return state

    try:
        releases = _fetch_github_releases(limit=30)
        latest = None
        for rel in releases:
            if not rel.get("prerelease"):
                latest = rel
                break
        if latest is None and releases:
            latest = releases[0]

        latest_version = latest.get("version") if latest else None
        current = str(state.get("current_version") or __version__)
        update_available = bool(latest_version and _is_newer_version(str(latest_version), current))

        state["current_version"] = current
        state["latest_version"] = latest_version
        state["update_available"] = update_available
        state["last_checked_at"] = _now_iso()
        state["last_error"] = None
        _save_state(state)
    except Exception as exc:  # any failed check (network, GitHub, parsing) is shown to the user as last_error
        state["last_checked_at"] = _now_iso()
        state["last_error"] = str(exc)
        _save_state(state)

    return state


def get_update_status(force: bool = False) -> Dict[str, Any]:
    state = _maybe_refresh_status(force=force)
    return {
        "current_version": state.get("current_version") or __version__,
        "current_version_label": _format_version_label(state.get("current_version") or __version__),
        "latest_version": state.get("latest_version"),
        "latest_version_label": _format_version_label(state.get("latest_version")),
        "update_available": bool(state.get("update_available", False)),
        "last_checked_at": state.get("last_checked_at"),
        "check_error": state.get("last_error"),
        "is_busy": _OP_LOCK.locked(),
    }


def check_for_updates() -> Dict[str, Any]:
    return get_update_status(force=True)


def install_from_github(version: Optional[str] = None, restart: bool = True) -> Dict[str, Any]:
    if not _OP_LOCK.acquire(blocking=False):
        raise UpdateError("An update or rollback operation is already in progress.")

    zip_path: Optional[Path] = None
    extracted: Optional[Path] = None

    try:
        target = _pick_release(version=version)
        target_version = str(target.get("version") or "unknown")
        zip_url = str(target.get("zip_url") or "").strip()
        if not zip_url:
            raise UpdateError(f"Release {target_version} does not expose a ZIP URL.")

        logger.info(f"Updater: downloading {target_version} from GitHub")
        zip_path = _download_zip(zip_url)
        extracted = _extract_zip(zip_path)
        project_root = _locate_project_root(extracted)

        tree_version = _read_version_from_tree(project_root)
        final_target = tree_version or target_version

        backup = _create_backup_snapshot(target_version=final_target, source="github")
        result = _apply_project_tree(project_root)

        _set_install_state(target_version=final_target, source="github")

        out = {
            "status": "ok",
            "source": "github",
            "installed_version": final_target,
            "installed_version_label": _format_version_label(final_target),
            "backup": backup,
            "files_updated": result["updated"],
            "files_removed": result["removed"],
            "files_skipped": result["skipped"],
            "restart_scheduled": bool(restart),
        }

        if restart:
            schedule_restart(delay_seconds=2.0)

        return out
    finally:
        _OP_LOCK.release()
        if zip_path is not None:
            zip_path.unlink(missing_ok=True)
        if extracted is not None:
            shutil.rmtree(extracted, ignore_errors=True)


def install_from_uploaded_zip(zip_path: Path, restart: bool = True) -> Dict[str, Any]:
    if not _OP_LOCK.acquire(blocking=False):
        raise UpdateError("An update or rollback operation is already in progress.")

    extracted: Optional[Path] = None

    try:
        extracted = _extract_zip(zip_path)
        project_root = _locate_project_root(extracted)
        target_version = _read_version_from_tree(project_root) or "uploaded-zip"

        backup = _create_backup_snapshot(target_version=target_version, source="upload")
        result = _apply_project_tree(project_root)

        _set_install_state(target_version=target_version, source="upload")

        out = {
            "status": "ok",
            "source": "upload",
            "installed_version": target_version,
            "installed_version_label": _format_version_label(target_version),
            "backup": backup,
            "files_updated": result["updated"],
            "files_removed": result["removed"],
            "files_skipped": result["skipped"],
            "restart_scheduled": bool(restart),
        }

        if restart:
            schedule_restart(delay_seconds=2.0)

        return out
    finally:
        _OP_LOCK.release()
        if extracted is not None:
            shutil.rmtree(extracted, ignore_errors=True)


def rollback_to_backup(backup_id: str, restart: bool = True) -> Dict[str, Any]:
    if not backup_id:
        raise UpdateError("backup_id is required for rollback.")

    if not _OP_LOCK.acquire(blocking=False):
        raise UpdateError("An update or rollback operation is already in progress.")

    try:
        backup_zip = backup.BACKUP_DIR / f"{backup_id}.zip"
        backup_meta_path = backup.BACKUP_DIR / f"{backup_id}.json"
        if not backup_zip.exists():
            raise UpdateError(f"Backup '{backup_id}' does not exist.")

        backup_meta = _read_json_file(backup_meta_path, {})
        restored_version = str(backup_meta.get("current_version") or backup_id)

        # Backup current state before applying rollback so roll-forward is still possible.
        pre_backup = _create_backup_snapshot(target_version=f"rollback-{backup_id}", source="pre-rollback")
        restored = _restore_from_backup_zip(backup_zip)

        _set_install_state(target_version=restored_version, source="rollback")

        out = {
            "status": "ok",
            "source": "rollback",
            "restored_from": backup_id,
            "restored_version": restored_version,
            "restored_version_label": _format_version_label(restored_version),
            "safety_backup": pre_backup,
            "files_restored": restored["restored"],
            "restart_scheduled": bool(restart),
        }

        if restart:
            schedule_restart(delay_seconds=2.0)

        return out
    finally:
        _OP_LOCK.release()


