"""Application self-update and rollback helpers.

This module provides a conservative updater for source-based installs:
- checks GitHub tags/releases for newer versions
- installs updates from GitHub ZIP archives or uploaded ZIPs
- creates pre-update snapshots for rollback
- restores snapshots and schedules a self-restart
"""

from __future__ import annotations

import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from loguru import logger
from packaging.version import InvalidVersion, Version

from version import __version__
from core.config import APP_ROOT, get_config

GITHUB_OWNER = "polyn0mial"
GITHUB_REPO = "nzbpostarr"
_GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

STATE_DIR = APP_ROOT / "data" / "updater"
STATE_FILE = STATE_DIR / "state.json"
BACKUP_DIR = STATE_DIR / "backups"

_CHECK_TTL_SECONDS = 1800
_OP_LOCK = threading.Lock()

# Paths that must survive updates and rollbacks.
_PROTECTED_REL_PREFIXES = (
    Path(".git"),
    Path(".venv"),
    Path(".config"),
    Path("backups"),
    Path("logs"),
    Path("config.yaml"),
    Path("data"),
    Path("indexers") / "readme" / "readme.txt",
    Path("webui") / "node_modules",
)
_PROTECTED_FILENAMES = {"anime_cache.json", "queue_cache.json", "log.txt"}
_SKIP_PARTS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class UpdateError(RuntimeError):
    """Updater-specific operational error."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())


def _safe_member_path(member_name: str) -> Optional[Path]:
    p = Path(member_name)
    if p.is_absolute() or ".." in p.parts:
        return None
    return p


def _is_protected(rel_path: Path) -> bool:
    if not rel_path.parts:
        return True

    # Extra safety belt: never overwrite any SQLite/DB files during update/rollback.
    if rel_path.suffix.lower() == ".db":
        return True

    if rel_path.name in _PROTECTED_FILENAMES:
        return True

    for part in rel_path.parts:
        if part in _SKIP_PARTS:
            return True

    for prefix in _PROTECTED_REL_PREFIXES:
        if rel_path == prefix or prefix in rel_path.parents:
            return True

    return False


def _read_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception as exc:
        logger.debug(f"Updater: unable to read {path}: {exc}")
    return dict(default)


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
    return (0, Version("0"), str(version or "").lower())


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
            v = str(rel.get("version") or "")
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


def _create_backup_snapshot(target_version: str, source: str) -> Dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_id = f"{stamp}-{_sanitize_name(__version__)}"
    zip_path = BACKUP_DIR / f"{backup_id}.zip"

    copied = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in APP_ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(APP_ROOT)
            if _is_protected(rel):
                continue
            zf.write(path, arcname=rel.as_posix())
            copied += 1

        meta = {
            "backup_id": backup_id,
            "created_at": _now_iso(),
            "current_version": __version__,
            "target_version": target_version,
            "source": source,
            "file_count": copied,
        }
        zf.writestr(".nzbpostarr-backup-meta.json", json.dumps(meta, indent=2))

    meta_path = BACKUP_DIR / f"{backup_id}.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _apply_project_tree(project_root: Path) -> Dict[str, int]:
    """Copy changed files from extracted update tree into APP_ROOT.

    Intentionally does not delete files that are not present in the update tree.
    """
    updated = 0
    skipped = 0

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

    return {"updated": updated, "skipped": skipped}


def _restore_from_backup_zip(zip_path: Path) -> Dict[str, int]:
    if not zip_path.exists():
        raise UpdateError(f"Backup archive not found: {zip_path.name}")

    restored = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir() or member.filename == ".nzbpostarr-backup-meta.json":
                continue

            rel = _safe_member_path(member.filename)
            if rel is None:
                continue

            dst = APP_ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(dst, "wb") as out:
                shutil.copyfileobj(src, out)
            restored += 1

    return {"restored": restored}


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


def list_backups(limit: int = 20) -> List[Dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []

    backups: List[Dict[str, Any]] = []
    for zf in sorted(BACKUP_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
        backup_id = zf.stem
        sidecar = BACKUP_DIR / f"{backup_id}.json"
        meta = _read_json_file(sidecar, {}) if sidecar.exists() else {}

        backups.append(
            {
                "backup_id": backup_id,
                "file": zf.name,
                "size_bytes": zf.stat().st_size,
                "created_at": meta.get("created_at")
                or datetime.fromtimestamp(zf.stat().st_mtime, tz=timezone.utc).isoformat(),
                "current_version": meta.get("current_version"),
                "target_version": meta.get("target_version"),
                "source": meta.get("source"),
            }
        )

    return backups[: max(1, min(limit, 100))]


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
    except Exception as exc:
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
        backup_zip = BACKUP_DIR / f"{backup_id}.zip"
        backup_meta_path = BACKUP_DIR / f"{backup_id}.json"
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


def schedule_restart(delay_seconds: float = 2.0) -> None:
    """Spawn a fresh process and terminate the current one after a short delay."""

    cmd = [sys.executable, *sys.argv]
    try:
        restart_port = int(getattr(get_config(), "port", 8000))
    except Exception:
        restart_port = 8000

    def _restart_worker() -> None:
        time.sleep(max(0.1, float(delay_seconds)))
        spawned = False
        try:
            kwargs: Dict[str, Any] = {
                "cwd": str(APP_ROOT),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }

            if os.name == "nt":
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(cmd, **kwargs)
            else:
                kwargs["start_new_session"] = True
                supervisor = r"""
import os
import signal
import subprocess
import sys
import time

cmd = sys.argv[1:]
time.sleep(float(os.environ.get("NZBPOSTARR_RESTART_DELAY", "1.0") or "1.0"))
try:
    import psutil
    port = int(os.environ.get("NZBPOSTARR_RESTART_PORT", "8000") or "8000")
    listeners = []
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == psutil.CONN_LISTEN and getattr(conn.laddr, "port", None) == port and conn.pid:
            if conn.pid == os.getpid():
                continue
            try:
                proc = psutil.Process(conn.pid)
                cmdline = " ".join(proc.cmdline()).lower()
            except Exception:
                cmdline = ""
            if "main.py" in cmdline or "nzbpostarr" in cmdline:
                listeners.append(conn.pid)
    for pid in sorted(set(listeners)):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if listeners:
        time.sleep(1.5)
    for pid in sorted(set(listeners)):
        try:
            proc = psutil.Process(pid)
            if proc.is_running():
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
except Exception:
    pass
subprocess.Popen(cmd, cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
"""
                env = os.environ.copy()
                env["NZBPOSTARR_RESTART_DELAY"] = "1.0"
                env["NZBPOSTARR_RESTART_PORT"] = str(restart_port)
                subprocess.Popen([sys.executable, "-c", supervisor, *cmd], env=env, **kwargs)
            spawned = True
            logger.info("Updater: restart process spawned successfully")
        except Exception as exc:
            logger.error(f"Updater: failed to spawn restart process: {exc}")
        if spawned:
            os._exit(0)

    threading.Thread(target=_restart_worker, daemon=True, name="nzbpostarr-restart").start()
