"""Settings API: /api/settings. A thin HTTP layer over logic.settings."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any, Dict

import yaml
from fastapi import APIRouter, HTTPException
from loguru import logger

from api.deps import _sync_stats_collector_state
from api.pending import _pending_index, _pending_watch_folders
from core.config import get_config
from logic import autoupload
from logic import settings as settings_service
from logic.pending.completion import invalidate_pending_indexer_context


router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("")
@router.get("/")
async def get_current_settings() -> Dict[str, Any]:
    """Retrieve the current active configuration grouped for the UI."""
    return settings_service.settings_view(get_config())

def _invalidate_pending_indexer_context() -> None:
    """Drop the pending tree's cached indexer ticks after an indexer change."""
    invalidate_pending_indexer_context()

@router.post("/reset")
async def reset_settings_route() -> Dict[str, Any]:
    """Reset configuration to defaults from config.defaults.yaml."""
    try:
        new_config = settings_service.reset_to_defaults()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Defaults file not found") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset: {str(e)}") from e
    _invalidate_pending_indexer_context()
    await _sync_stats_collector_state(new_config)
    return {
        "status": "success",
        "message": "Settings reset to defaults from config.defaults.yaml",
    }

@router.get("/config")
async def get_current_config() -> Dict[str, Any]:
    """Retrieve the current active configuration (flat)."""
    return settings_service.flat_config_view(get_config())

@router.put("/{_section}")
async def update_settings(_section: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update a specific section of the configuration."""
    if not settings_service.update_settings(updates, get_config()):
        raise HTTPException(status_code=500, detail="Failed to save settings")
    if settings_service.settings_touch_indexers(_section, updates):
        _invalidate_pending_indexer_context()
    if _section == "ui":
        await _sync_stats_collector_state()
    if _section != "folders":
        return {"status": "success"}

    # Restart folder monitor if folder settings changed (monitor flags may have toggled)
    monitor_status: Dict[str, Any] = {"attempted": True, "ok": True}
    try:
        await autoupload.restart_folder_monitor()
    except Exception as exc:
        logger.warning(f"Folder monitor restart failed after settings update: {exc}")
        monitor_status = {"attempted": True, "ok": False, "error": str(exc)}

    try:
        _pending_index.restart_watched_folders(_pending_watch_folders(get_config()))
    except Exception as exc:
        logger.warning(f"Pending index watcher restart failed after settings update: {exc}")

    if not monitor_status["ok"]:
        return {
            "status": "partial_success",
            "message": "Settings saved, but folder monitor restart failed",
            "monitor_restart": monitor_status,
        }
    return {"status": "success", "monitor_restart": monitor_status}

@router.post("/preview")
async def get_config_preview(updates: Dict[str, Any]) -> Dict[str, str]:
    """Generate a YAML preview of what the config would look like with these updates."""
    try:
        return {"content": settings_service.config_preview(updates, get_config())}
    except Exception as e:
        return {"content": f"# Error generating preview: {str(e)}"}

@router.post("/raw")
async def save_raw_config(req: Dict[str, Any]) -> Dict[str, Any]:
    """Save raw YAML content."""
    content = req.get("content")
    if not isinstance(content, str):
        content = ""
    password = req.get("password")

    # If a web_password is configured, enforce it. Otherwise, access is open
    # (relying on the user to secure the port at the network/host level).
    conf = get_config()
    expected = conf.web_password
    if expected and password != expected:
        raise HTTPException(status_code=403, detail="Invalid password")

    try:
        new_config = settings_service.save_raw_config(content, conf)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}") from e
    _invalidate_pending_indexer_context()
    await _sync_stats_collector_state(new_config)
    return {"status": "success"}

@router.get("/browse")
async def browse_folders(path: str = "/") -> Dict[str, Any]:
    """Browse local directories for the folder picker.

    Returns a list of subdirectories at the given path,
    along with the resolved parent path for navigation.
    """
    target = Path(path) if path and path != "/" else Path("/")

    # On Windows, list drive letters when at root
    if platform.system() == "Windows" and (str(target) == "/" or str(target) == "\\"):
        drives = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append({"name": f"{letter}:", "path": drive})
        return {"path": "/", "parent": None, "dirs": drives}

    try:
        target = target.resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    parent = str(target.parent) if target.parent != target else None

    dirs = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        pass  # Return empty list for inaccessible dirs

    return {"path": str(target), "parent": parent, "dirs": dirs}

@router.get("/raw")
async def get_raw_config() -> Dict[str, Any]:
    """Get the raw YAML content for direct editing, with secrets masked.

    ``save_raw_config`` restores any untouched mask from the live config, so a
    round-trip through the raw editor preserves values the operator did not edit.
    """
    try:
        content, path = settings_service.read_masked_raw_config()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Config file not found") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=500, detail=f"Config file could not be read: {exc}") from exc
    return {"content": content, "path": str(path), "masked": True}

@router.get("/readme")
async def get_readme_file() -> Dict[str, Any]:
    """Get the contents of the readme.txt file included in uploads."""
    content, path = settings_service.read_readme()
    return {"content": content, "path": str(path)}

@router.post("/readme")
async def save_readme_file(req: Dict[str, Any]) -> Dict[str, Any]:
    """Save (or create) the readme.txt file included in uploads."""
    content = req.get("content", "")
    path = settings_service.save_readme(content if isinstance(content, str) else "")
    return {"status": "success", "path": str(path)}
