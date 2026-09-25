"""Settings API: /api/settings."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from loguru import logger

from api.deps import _sync_stats_collector_state
from api.pending import _pending_index, _pending_watch_folders
from core.config import APP_ROOT, get_config
from core.redaction import SECRET_MASK


router = APIRouter(prefix="/api/settings", tags=["settings"])

@router.get("")
@router.get("/")
async def get_current_settings() -> Dict[str, Any]:
    """Retrieve the current active configuration grouped for the UI."""
    from core.registry import (
        get_all_indexers,
        get_available_categories,
        resolve_indexer_backfill,
        resolve_indexer_enabled,
        resolve_indexer_priority,
    )

    conf = get_config()

    # Build destinations dynamically from loaded indexers
    destinations = {
        "enable_backfill": conf.enable_backfill,
        "enable_duplicate_bypass": getattr(conf, "enable_duplicate_bypass", False),
    }

    for idx in get_all_indexers():
        destinations[f"enable_{idx.id}"] = resolve_indexer_enabled(idx, conf)
        destinations[f"backfill_{idx.id}"] = resolve_indexer_backfill(idx, conf)
        destinations[f"priority_{idx.id}"] = resolve_indexer_priority(idx, conf)

    return {
        "destinations": destinations,
        "processing": {
            "verbose": conf.verbose,
            "process_tv_episodes": getattr(conf, "process_tv_episodes", True),
            "enable_duplicate_checking": conf.enable_duplicate_checking,
            "enable_anime_checking": getattr(conf, "enable_anime_checking", False),
            "item_limit_per_category": getattr(conf, "item_limit_per_category", None),
            "folder_size_limit_gb": getattr(conf, "folder_size_limit_gb", 99),
            "folder_size_limit_enabled": getattr(conf, "folder_size_limit_enabled", True),
            "file_size_limit_gb": getattr(conf, "file_size_limit_gb", 0),
            "file_size_limit_enabled": getattr(conf, "file_size_limit_enabled", True),
            "dynamic_packs": getattr(conf, "dynamic_packs", True),
            "tv_pack_ignore": getattr(
                conf,
                "tv_pack_ignore",
                {
                    "enabled": True,
                    "ignore_non_episode": True,
                    "require_episode": True,
                    "require_resolution": False,
                    "require_source": True,
                },
            ),
        },
        "upload": {
            "poster_name": conf.poster_name,
            "poster_email": conf.poster_email,
            "rar_size": conf.rar_size,
            "article_size": conf.article_size,
            "include_readme": getattr(conf, "include_readme", True),
            "upload_max_retries": getattr(conf, "upload_max_retries", 3),
            "upload_retry_delay_seconds": getattr(conf, "upload_retry_delay_seconds", 5),
            "alt_bins": getattr(conf, "alt_bins", []),
        },
        "folders": {
            "base_folder": str(conf.base_folder),
            "folder_paths": conf.get_folder_path_entries(),
            "backup_folder": str(getattr(conf, "backup_folder", APP_ROOT / "backups")),
        },
        "nntp_servers": [_mask_config_secrets(s.model_dump()) for s in conf.nntp_servers],
        "api_keys": _mask_config_secrets(conf.api_keys, key="api_keys"),
        "usernames": _mask_config_secrets(conf.usernames, key="usernames"),
        "ui": {
            "dashboard_stats_enabled": getattr(conf, "dashboard_stats_enabled", True),
            "ui_refresh_seconds": getattr(conf, "ui_refresh_seconds", 2),
            "dashboard_stats_modules": getattr(
                conf,
                "dashboard_stats_modules",
                ["cpu", "memory", "disk", "free_space", "upload", "download"],
            ),
            "stats_page_enabled": getattr(conf, "stats_page_enabled", True),
            "category_appearance_profiles": getattr(conf, "category_appearance_profiles", {}),
        },
        "skip_files": getattr(
            conf,
            "skip_files",
            {"enabled": False, "display_mode": "disabled", "patterns": []},
        ),
        "auth": {
            "enable_password": getattr(conf, "enable_password", False),
            "web_username": getattr(conf, "web_username", "admin"),
            # web_password intentionally NOT exposed
        },
        "categories": get_available_categories(),
        # Include list of indexers for the UI to render
        "indexers": [idx.to_ui_dict(conf) for idx in get_all_indexers()],
    }

def _invalidate_pending_indexer_context() -> None:
    """Drop the pending tree's cached indexer ticks after an indexer change."""
    from logic.pending_snapshot import invalidate_pending_indexer_context

    invalidate_pending_indexer_context()

def _settings_touch_indexers(section: str, updates: Dict[str, Any]) -> bool:
    """True when a settings save enables, disables or re-keys an indexer."""
    return section in ("destinations", "credentials") or any(str(key).startswith("enable_") for key in updates)

@router.post("/reset")
async def reset_settings_route() -> Dict[str, Any]:
    """Reset configuration to defaults from config.defaults.yaml."""
    from core.config import get_defaults_config_path, replace_config_content

    defaults_path = get_defaults_config_path()

    try:
        if defaults_path.exists():
            new_config = replace_config_content(defaults_path.read_text(encoding="utf-8"))
            _invalidate_pending_indexer_context()
            await _sync_stats_collector_state(new_config)
            return {
                "status": "success",
                "message": "Settings reset to defaults from config.defaults.yaml",
            }
        raise HTTPException(status_code=404, detail="Defaults file not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset: {str(e)}") from e

@router.get("/config")
async def get_current_config() -> Dict[str, Any]:
    """Retrieve the current active configuration (flat)."""
    return _mask_config_secrets(get_config().model_dump(by_alias=True))

@router.put("/{_section}")
async def update_settings(_section: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update a specific section of the configuration."""
    from core.config import save_config

    updates = _merge_masked_secret_updates(updates, get_config())
    if save_config(updates):
        if _settings_touch_indexers(_section, updates):
            _invalidate_pending_indexer_context()
        if _section == "ui":
            await _sync_stats_collector_state()

        # Restart folder monitor if folder settings changed (monitor flags may have toggled)
        if _section == "folders":
            monitor_status: Dict[str, Any] = {"attempted": True, "ok": True}
            try:
                from logic.folder_monitor import restart_folder_monitor

                await restart_folder_monitor()
            except Exception as exc:
                logger.warning(f"Folder monitor restart failed after settings update: {exc}")
                monitor_status = {
                    "attempted": True,
                    "ok": False,
                    "error": str(exc),
                }

            try:
                conf = get_config()
                _pending_index.restart_watched_folders(_pending_watch_folders(conf))
            except Exception as exc:
                logger.warning(f"Pending index watcher restart failed after settings update: {exc}")

            if not monitor_status["ok"]:
                return {
                    "status": "partial_success",
                    "message": "Settings saved, but folder monitor restart failed",
                    "monitor_restart": monitor_status,
                }

            return {
                "status": "success",
                "monitor_restart": monitor_status,
            }

        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to save settings")

@router.post("/preview")
async def get_config_preview(updates: Dict[str, Any]) -> Dict[str, str]:
    """Generate a YAML preview of what the config would look like with these updates."""
    import yaml

    from core.config import get_config_path

    path = get_config_path()
    try:
        # Load current data
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        safe_updates = _merge_masked_secret_updates(updates, get_config())
        for k, v in safe_updates.items():
            data[k] = v

        return {"content": yaml.dump(data, default_flow_style=False, sort_keys=False)}
    except Exception as e:
        return {"content": f"# Error generating preview: {str(e)}"}

@router.post("/raw")
async def save_raw_config(req: Dict[str, Any]) -> Dict[str, Any]:
    """Save raw YAML content."""
    from core.config import replace_config_content

    content = req.get("content")
    if not isinstance(content, str):
        content = ""
    password = req.get("password")

    # If a web_password is configured, enforce it. Otherwise, access is open
    # (relying on the user to secure the port at the network/host level).
    conf = get_config()
    expected = getattr(conf, "web_password", None)
    if expected:
        if password != expected:
            raise HTTPException(status_code=403, detail="Invalid password")

    # get_raw_config masks credentials, so restore any mask the operator left
    # untouched before writing; otherwise saving would overwrite real secrets
    # with the mask string.
    import yaml

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}") from e

    if isinstance(parsed, dict):
        restored = _merge_masked_secret_updates(parsed, conf)
        content = yaml.safe_dump(restored, default_flow_style=False, sort_keys=False, allow_unicode=True)

    try:
        new_config = replace_config_content(content)
        _invalidate_pending_indexer_context()
        await _sync_stats_collector_state(new_config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}") from e

def _mask_config_secrets(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower()
    secret_key = (
        normalized_key in {"api_keys", "usernames", "web_password", "password", "pass", "user", "username"}
        or normalized_key.endswith("_api_key")
    )
    if secret_key:
        if isinstance(value, dict):
            return {str(child_key): SECRET_MASK if child_value not in (None, "") else child_value for child_key, child_value in value.items()}
        return SECRET_MASK if value not in (None, "") else value
    if isinstance(value, dict):
        return {
            str(child_key): _mask_config_secrets(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_mask_config_secrets(item) for item in value]
    return value

def _merge_masked_secret_updates(updates: Dict[str, Any], conf: Any) -> Dict[str, Any]:
    """Replace unchanged UI mask markers with the current private values."""
    merged = copy.deepcopy(updates)
    if merged.get("web_password") == SECRET_MASK:
        merged["web_password"] = str(getattr(conf, "web_password", "") or "")

    for field_name in ("api_keys", "usernames"):
        incoming = merged.get(field_name)
        current = getattr(conf, field_name, {}) or {}
        if not isinstance(incoming, dict) or not isinstance(current, dict):
            continue
        for item_key, item_value in list(incoming.items()):
            if item_value == SECRET_MASK:
                incoming[item_key] = current.get(item_key, "")

    incoming_servers = merged.get("nntp_servers")
    current_servers = list(getattr(conf, "nntp_servers", []) or [])
    if isinstance(incoming_servers, list):
        current_by_name = {
            str(getattr(server, "name", "")): server
            for server in current_servers
            if str(getattr(server, "name", ""))
        }
        normalized_servers: list[Any] = []
        for index, incoming_server in enumerate(incoming_servers):
            if not isinstance(incoming_server, dict):
                normalized_servers.append(incoming_server)
                continue
            server = dict(incoming_server)
            current_server = current_by_name.get(str(server.get("name") or ""))
            if current_server is None and index < len(current_servers):
                current_server = current_servers[index]

            incoming_user = server.get("user")
            if incoming_user == SECRET_MASK and current_server is not None:
                server["user"] = str(getattr(current_server, "user", ""))

            incoming_password = server.pop("password", server.get("pass"))
            if incoming_password == SECRET_MASK and current_server is not None:
                incoming_password = str(getattr(current_server, "password", ""))
            if incoming_password is not None:
                server["pass"] = incoming_password
            normalized_servers.append(server)
        merged["nntp_servers"] = normalized_servers
    return merged

@router.get("/browse")
async def browse_folders(path: str = "/") -> Dict[str, Any]:
    """Browse local directories for the folder picker.

    Returns a list of subdirectories at the given path,
    along with the resolved parent path for navigation.
    """
    import platform

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

    Credentials are replaced with ``SECRET_MASK`` exactly as the structured
    settings endpoints do. ``save_raw_config`` restores any untouched mask from
    the live config, so a round-trip through the raw editor preserves values the
    operator did not edit.
    """
    import yaml

    from core.config import get_config_path

    path = get_config_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        # Never fall back to echoing the file verbatim: that is the leak.
        raise HTTPException(status_code=500, detail=f"Config file could not be read: {exc}") from exc

    masked = _mask_config_secrets(data)
    # allow_unicode keeps SECRET_MASK readable as bullets in the editor instead
    # of an escaped "•..." sequence.
    content = yaml.safe_dump(masked, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return {"content": content, "path": str(path), "masked": True}

@router.get("/readme")
async def get_readme_file() -> Dict[str, Any]:
    """Get the contents of the readme.txt file included in uploads."""
    from core.config import APP_ROOT

    path = APP_ROOT / "indexers" / "readme" / "readme.txt"
    example_path = path.with_name("readme.example.txt")
    content_path = path if path.exists() else example_path
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    return {"content": content, "path": str(path)}

@router.post("/readme")
async def save_readme_file(req: Dict[str, Any]) -> Dict[str, Any]:
    """Save (or create) the readme.txt file included in uploads."""
    from core.config import APP_ROOT

    content = req.get("content", "")
    if not isinstance(content, str):
        content = ""

    path = APP_ROOT / "indexers" / "readme" / "readme.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"status": "success", "path": str(path)}
