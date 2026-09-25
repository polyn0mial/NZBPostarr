"""Backups: updater snapshots, their restore, and the full state archive.

Every archive is created through ``_open_private_archive`` so it is owner-only
(0600) before any byte is written: the full archive holds .env and the config.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import socket
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Set

from loguru import logger

from core.config import APP_ROOT
from version import __version__

STATE_DIR = APP_ROOT / "data" / "updater"
BACKUP_DIR = STATE_DIR / "backups"

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

_SNAPSHOT_META_NAME = ".nzbpostarr-backup-meta.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())


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


def _open_private_archive(path: Path) -> BinaryIO:
    """The one archive writer entry point: create ``path`` owner-only (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return os.fdopen(fd, "wb")


def _create_backup_snapshot(target_version: str, source: str) -> Dict[str, Any]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_id = f"{stamp}-{_sanitize_name(__version__)}"
    zip_path = BACKUP_DIR / f"{backup_id}.zip"

    copied = 0
    with _open_private_archive(zip_path) as raw, zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as zf:
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
        zf.writestr(_SNAPSHOT_META_NAME, json.dumps(meta, indent=2))

    meta_path = BACKUP_DIR / f"{backup_id}.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def _restore_from_backup_zip(zip_path: Path) -> Dict[str, int]:
    if not zip_path.exists():
        raise FileNotFoundError(f"Backup archive not found: {zip_path.name}")

    restored = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            if member.is_dir() or member.filename == _SNAPSHOT_META_NAME:
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


def create_full_backup_archive(skip_tmp_contents: bool = True) -> Dict[str, Any]:
    """Create a thorough tar.gz backup of the important NZBPostarr state.

    The archive holds .env and the config file, so it is written owner-only (0600).
    """
    from core import config as config_mod

    conf = config_mod.get_config()
    source_root = config_mod.APP_ROOT
    backup_root = Path(getattr(conf, "backup_folder", source_root / "backups")).expanduser()
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"nzbpostarr_full_backup_{timestamp}.tar.gz"
    archive_path = backup_root / archive_name

    log_db = getattr(conf, "log_db", None)
    important_targets = [
        source_root,
        source_root / ".config" / "nzbpostarr",
        source_root / "data",
        source_root / "anime_cache.json",
        *([Path(log_db)] if log_db else []),
        source_root / ".env",
        Path("/etc/systemd/system/nzbpostarr.service"),
    ]
    excluded_roots = [
        source_root / ".git",
        source_root / ".venv",
        source_root / "venv",
        source_root / "tmp_vt",
        source_root / ".local",
        source_root / "backups",
        backup_root,
    ]
    tmp_root = (source_root / "data" / "tmp").resolve()
    skip_named_dirs: Set[str] = set()
    stateful_tmp_suffixes = {
        ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".log", ".db", ".sqlite", ".sqlite3",
    }

    def is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def should_skip(path: Path) -> bool:
        resolved = path.resolve() if path.exists() else path
        if any(part in skip_named_dirs for part in resolved.parts):
            return True
        for prefix in excluded_roots:
            if prefix.exists() and is_within(resolved, prefix):
                return True
        if skip_tmp_contents and is_within(resolved, tmp_root):
            if resolved == tmp_root or path.is_dir():
                return False
            return path.suffix.lower() not in stateful_tmp_suffixes
        return False

    def iter_backup_paths(target: Path) -> Iterator[Path]:
        if not target.exists():
            return
        if target.is_file():
            if not should_skip(target):
                yield target
            return
        yield target
        for path in sorted(target.rglob("*")):
            if should_skip(path):
                continue
            yield path

    def archive_name_for(path: Path) -> str:
        if is_within(path, source_root):
            return str(Path(source_root.name) / path.resolve().relative_to(source_root.resolve()))
        return str(Path("system") / path.relative_to(path.anchor))

    file_count = 0
    added_names: set[str] = set()
    with _open_private_archive(archive_path) as raw_archive, tarfile.open(fileobj=raw_archive, mode="w:gz") as tar:
        for target in important_targets:
            for path in iter_backup_paths(target):
                arcname = archive_name_for(path)
                if arcname in added_names:
                    continue
                tar.add(path, arcname=arcname, recursive=False)
                added_names.add(arcname)
                file_count += 1

        manifest = {
            "created_at": datetime.now().isoformat(),
            "hostname": socket.gethostname(),
            "source_root": str(source_root),
            "backup_root": str(backup_root),
            "archive_name": archive_name,
            "skip_tmp_contents": skip_tmp_contents,
            "included_targets": [str(path) for path in important_targets if path.exists()],
            "excluded_roots": [str(path) for path in excluded_roots],
            "tmp_stateful_suffixes": sorted(stateful_tmp_suffixes),
        }
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name=f"{source_root.name}/backup-manifest.json")
        info.size = len(payload)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(payload))
        file_count += 1

    size_bytes = archive_path.stat().st_size if archive_path.exists() else 0
    return {
        "status": "success",
        "archive_path": str(archive_path),
        "archive_name": archive_name,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "skip_tmp_contents": skip_tmp_contents,
        "backup_root": str(backup_root),
        "included_targets": [str(path) for path in important_targets if path.exists()],
    }
