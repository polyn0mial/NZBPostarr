"""System API: revision, updates, backup, restart, stop-all and health checks."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Set

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

from core import database
from core.config import get_config
from logic import updater
from logic.pending.roots import get_configured_folders
from logic.runtime import ensure_engine_started


dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

tests_router = APIRouter(prefix="/api/tests", tags=["tests"])

router = APIRouter(prefix="/api/system", tags=["system"])

@dashboard_router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Check the health of tools and directory structure."""
    import shutil

    conf = get_config()
    tools = {t: bool(shutil.which(t)) for t in ["rar", "parpar", "nyuu"]}
    configured_folders = get_configured_folders(conf)
    folders = {
        "configured": any(folder.exists() for folder in configured_folders),
        "missing": sum(1 for folder in configured_folders if not folder.exists()),
        "nzb_output": conf.get_nzb_path("test").parent.exists(),
    }
    return {
        "status": "Healthy" if all(tools.values()) and any(folders.values()) else "Warning",
        "tools": tools,
        "folders": folders,
        "nntp": bool(conf.nntp_servers),
    }

class UpdateInstallRequest(BaseModel):
    """API request model for installing an update from GitHub."""

    version: Optional[str] = None
    restart: bool = True

class UpdateRollbackRequest(BaseModel):
    """API request model for rolling back to a backup snapshot."""

    backup_id: str
    restart: bool = True

class RestartRequest(BaseModel):
    """API request model for scheduling a process restart."""

    delay_seconds: float = 2.0
    stop_before_restart: bool = True
    clear_staged_items: bool = True
    wait_timeout_seconds: float = 15.0

class CreateBackupRequest(BaseModel):
    """API request model for generating a full NZBPostarr backup archive."""

    skip_tmp_contents: bool = True

class StopAllRequest(BaseModel):
    """API request model for stopping all work and waiting for quiescence."""

    clear_staged_items: bool = True
    wait_timeout_seconds: float = 15.0

@dashboard_router.get("/database-health")
async def database_health_check() -> Dict[str, Any]:
    """Check database connection, tables, and recent activity."""
    return await asyncio.to_thread(database.get_database_health)

def _runtime_revision() -> Dict[str, Any]:
    revision_path = Path(__file__).resolve().parent.parent / "data" / "deployed_revision.json"
    if revision_path.exists():
        try:
            payload = json.loads(revision_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("revision"):
                return {
                    "revision": str(payload["revision"]),
                    "dirty": bool(payload.get("dirty", False)),
                    "deployed_at": payload.get("deployed_at"),
                }
        except (OSError, ValueError, TypeError):
            pass
    env_revision = str(os.getenv("NZBPOSTARR_REVISION") or "").strip()
    return {"revision": env_revision or "unknown", "dirty": False, "deployed_at": None}

@tests_router.get("/health")
async def health() -> Dict[str, Any]:
    """Basic health check."""
    return {"status": "ok", "time": time.time(), **_runtime_revision()}

@router.get("/revision")
async def get_runtime_revision() -> Dict[str, Any]:
    """Return the exact source revision recorded by the deployment workflow."""
    return _runtime_revision()

@router.get("/update/status")
async def get_update_status(force: bool = False) -> Dict[str, Any]:
    """Return current updater status and latest known version info."""
    return await asyncio.to_thread(updater.get_update_status, force=force)

@router.post("/update/check")
async def check_for_updates_now() -> Dict[str, Any]:
    """Force an immediate GitHub update check."""
    return await asyncio.to_thread(updater.check_for_updates)

@router.get("/update/releases")
async def get_update_releases(limit: int = 20) -> Dict[str, Any]:
    """List available GitHub releases/tags for install selection."""
    try:
        releases = await asyncio.to_thread(updater.get_releases, limit=limit)
        return {"releases": releases}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch releases: {exc}") from exc

@router.get("/update/backups")
async def get_update_backups(limit: int = 20) -> Dict[str, Any]:
    """List local update snapshots available for rollback."""
    backups = await asyncio.to_thread(updater.list_backups, limit=limit)
    return {"backups": backups}

def _create_full_backup_archive(skip_tmp_contents: bool = True) -> Dict[str, Any]:
    """Create a thorough tar.gz backup of the important NZBPostarr state.

    The archive holds .env and the config file, so it is written owner-only (0600).
    """
    import io
    import socket
    import tarfile
    from datetime import datetime

    from core.config import APP_ROOT, get_config

    conf = get_config()
    source_root = APP_ROOT
    backup_root = Path(getattr(conf, "backup_folder", APP_ROOT / "backups")).expanduser()
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
    # Create the file owner-only before any secret is written into it.
    fd = os.open(str(archive_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as raw_archive, tarfile.open(fileobj=raw_archive, mode="w:gz") as tar:
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
    try:
        os.chmod(archive_path, 0o600)
    except OSError:
        pass

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

@router.post("/backup/create")
async def create_full_backup(req: CreateBackupRequest) -> Dict[str, Any]:
    """Create a full NZBPostarr backup archive in the configured backup folder."""
    try:
        return await asyncio.to_thread(
            _create_full_backup_archive,
            skip_tmp_contents=req.skip_tmp_contents,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create backup: {exc}") from exc

@router.post("/update/install/github")
async def install_update_from_github(req: UpdateInstallRequest) -> Dict[str, Any]:
    """Install the latest (or selected) update directly from GitHub."""
    try:
        return await asyncio.to_thread(
            updater.install_from_github,
            version=req.version,
            restart=req.restart,
        )
    except updater.UpdateError as exc:
        message = str(exc)
        status = 409 if "already in progress" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception(f"GitHub update failed: {exc}")
        raise HTTPException(status_code=500, detail=f"GitHub update failed: {exc}") from exc

@router.post("/update/install/upload")
async def install_update_from_upload(
    file: UploadFile = File(...),
    restart: bool = Form(True),
) -> Dict[str, Any]:
    """Install an update from a user-provided ZIP archive."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are supported")

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = Path(tmp.name)

        return await asyncio.to_thread(updater.install_from_uploaded_zip, tmp_path, restart=restart)
    except updater.UpdateError as exc:
        message = str(exc)
        status = 409 if "already in progress" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception(f"Uploaded update failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Uploaded update failed: {exc}") from exc
    finally:
        await file.close()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)

@router.post("/update/rollback")
async def rollback_update(req: UpdateRollbackRequest) -> Dict[str, Any]:
    """Restore files from a previous updater snapshot."""
    try:
        return await asyncio.to_thread(
            updater.rollback_to_backup,
            backup_id=req.backup_id,
            restart=req.restart,
        )
    except updater.UpdateError as exc:
        message = str(exc)
        status = 409 if "already in progress" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception(f"Rollback failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Rollback failed: {exc}") from exc

@router.post("/restart")
async def restart_service(req: RestartRequest) -> Dict[str, Any]:
    """Schedule a process restart without changing files."""
    service = ensure_engine_started()
    stop_result: Optional[Dict[str, Any]] = None

    if req.stop_before_restart:
        stop_result = await asyncio.to_thread(
            service.stop_all_jobs_and_wait,
            clear_staged_items=req.clear_staged_items,
            wait_timeout_s=req.wait_timeout_seconds,
        )

    updater.schedule_restart(delay_seconds=req.delay_seconds)
    return {
        "status": "scheduled",
        "delay_seconds": req.delay_seconds,
        "stop": stop_result,
    }

@router.post("/stop-all")
async def stop_all_service_activity(req: StopAllRequest) -> Dict[str, Any]:
    """Stop active jobs, clear waiting work, and wait until quiet."""
    service = ensure_engine_started()
    stop_result = await asyncio.to_thread(
        service.stop_all_jobs_and_wait,
        clear_staged_items=req.clear_staged_items,
        wait_timeout_s=req.wait_timeout_seconds,
    )
    status = "stopped"
    message = "All uploads stopped and queue cleared."
    if stop_result.get("timed_out"):
        status = "partial"
        message = "Stop requested, but some work was still shutting down when the timeout expired."

    return {
        "status": status,
        "message": message,
        "stop": stop_result,
    }
