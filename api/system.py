"""System API: revision, updates, backup, restart, stop-all and health checks."""

from __future__ import annotations

import asyncio
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

from core import database
from core.config import get_config
from core.tools import check_tools
from logic.system import backup, lifecycle, updater
from logic.pending.roots import get_configured_folders


dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

tests_router = APIRouter(prefix="/api/tests", tags=["tests"])

router = APIRouter(prefix="/api/system", tags=["system"])

@dashboard_router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Check the health of tools and directory structure."""
    conf = get_config()
    tools = {name: bool(executable) for name, executable in check_tools(conf).items()}
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

@tests_router.get("/health")
async def health() -> Dict[str, Any]:
    """Basic health check."""
    return {"status": "ok", "time": time.time(), **lifecycle.runtime_revision()}

@router.get("/revision")
async def get_runtime_revision() -> Dict[str, Any]:
    """Return the exact source revision recorded by the deployment workflow."""
    return lifecycle.runtime_revision()

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
    backups = await asyncio.to_thread(backup.list_backups, limit=limit)
    return {"backups": backups}

@router.post("/backup/create")
async def create_full_backup(req: CreateBackupRequest) -> Dict[str, Any]:
    """Create a full NZBPostarr backup archive in the configured backup folder."""
    try:
        return await asyncio.to_thread(
            backup.create_full_backup_archive,
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
            lifecycle.rollback_update,
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
    return await asyncio.to_thread(
        lifecycle.restart_service,
        delay_seconds=req.delay_seconds,
        stop_before_restart=req.stop_before_restart,
        clear_staged_items=req.clear_staged_items,
        wait_timeout_seconds=req.wait_timeout_seconds,
    )

@router.post("/stop-all")
async def stop_all_service_activity(req: StopAllRequest) -> Dict[str, Any]:
    """Stop active jobs, clear waiting work, and wait until quiet."""
    return await asyncio.to_thread(
        lifecycle.stop_all_service_activity,
        clear_staged_items=req.clear_staged_items,
        wait_timeout_seconds=req.wait_timeout_seconds,
    )
