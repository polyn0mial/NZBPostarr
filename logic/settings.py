"""Settings service: the settings view, secret masking and every config write.

api/settings.py stays a thin HTTP layer over these functions. All writes go
through core.config's validated atomic writer.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

import yaml

from core import config as config_mod
from core.config import APP_ROOT, DEFAULT_TV_PACK_IGNORE, Config
from core.fs import atomic_write_text
from core.indexers import categories as indexer_categories
from core.indexers import models as indexer_models
from core.indexers import registry as indexer_registry
from core.redaction import SECRET_MASK

_SECRET_KEYS = frozenset({"api_keys", "usernames", "web_password", "password", "pass", "user", "username"})


def mask_config_secrets(value: Any, *, key: str = "") -> Any:
    """Replace every configured credential with SECRET_MASK; blanks stay blank."""
    normalized_key = key.strip().lower()
    if normalized_key in _SECRET_KEYS or normalized_key.endswith("_api_key"):
        if isinstance(value, dict):
            return {str(child_key): SECRET_MASK if child_value not in (None, "") else child_value for child_key, child_value in value.items()}
        return SECRET_MASK if value not in (None, "") else value
    if isinstance(value, dict):
        return {
            str(child_key): mask_config_secrets(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [mask_config_secrets(item) for item in value]
    return value


def merge_masked_secret_updates(updates: Dict[str, Any], conf: Config) -> Dict[str, Any]:
    """Replace unchanged UI mask markers with the current private values."""
    merged = copy.deepcopy(updates)
    if merged.get("web_password") == SECRET_MASK:
        merged["web_password"] = conf.web_password or ""

    for field_name in ("api_keys", "usernames"):
        incoming = merged.get(field_name)
        if not isinstance(incoming, dict):
            continue
        current: Dict[str, str] = conf.api_keys if field_name == "api_keys" else conf.usernames
        for item_key, item_value in list(incoming.items()):
            if item_value == SECRET_MASK:
                incoming[item_key] = current.get(item_key, "")

    incoming_servers = merged.get("nntp_servers")
    if isinstance(incoming_servers, list):
        current_servers = list(conf.nntp_servers)
        current_by_name = {server.name: server for server in current_servers if server.name}
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
                server["user"] = current_server.user

            incoming_password = server.pop("password", server.get("pass"))
            if incoming_password == SECRET_MASK and current_server is not None:
                incoming_password = current_server.password
            if incoming_password is not None:
                server["pass"] = incoming_password
            normalized_servers.append(server)
        merged["nntp_servers"] = normalized_servers
    return merged


def settings_view(conf: Config) -> Dict[str, Any]:
    """The active configuration grouped for the Settings page, secrets masked.

    The indexer helpers are looked up on their modules at call time so test patches apply.
    """
    destinations = {
        "enable_backfill": conf.enable_backfill,
        "enable_duplicate_bypass": conf.enable_duplicate_bypass,
    }
    for idx in indexer_registry.get_all_indexers():
        destinations[f"enable_{idx.id}"] = indexer_models.resolve_indexer_enabled(idx, conf)
        destinations[f"backfill_{idx.id}"] = indexer_models.resolve_indexer_backfill(idx, conf)
        destinations[f"priority_{idx.id}"] = indexer_models.resolve_indexer_priority(idx, conf)

    return {
        "destinations": destinations,
        "processing": {
            "verbose": conf.verbose,
            # Configs that predate these keys still get the defaults (client fixes app-routes-01/03/04).
            "process_tv_episodes": getattr(conf, "process_tv_episodes", True),
            "enable_duplicate_checking": conf.enable_duplicate_checking,
            "enable_anime_checking": conf.enable_anime_checking,
            "item_limit_per_category": conf.item_limit_per_category,
            "folder_size_limit_gb": conf.folder_size_limit_gb,
            "folder_size_limit_enabled": conf.folder_size_limit_enabled,
            "file_size_limit_gb": conf.file_size_limit_gb,
            "file_size_limit_enabled": conf.file_size_limit_enabled,
            "dynamic_packs": conf.dynamic_packs,
            "tv_pack_ignore": getattr(conf, "tv_pack_ignore", dict(DEFAULT_TV_PACK_IGNORE)),
        },
        "upload": {
            "poster_name": conf.poster_name,
            "poster_email": conf.poster_email,
            "rar_size": conf.rar_size,
            "article_size": conf.article_size,
            "include_readme": conf.include_readme,
            "upload_max_retries": conf.upload_max_retries,
            "upload_retry_delay_seconds": conf.upload_retry_delay_seconds,
            "alt_bins": conf.alt_bins,
        },
        "folders": {
            "base_folder": str(conf.base_folder),
            "folder_paths": conf.get_folder_path_entries(),
            "backup_folder": str(getattr(conf, "backup_folder", APP_ROOT / "backups")),
        },
        "nntp_servers": [mask_config_secrets(s.model_dump()) for s in conf.nntp_servers],
        "api_keys": mask_config_secrets(conf.api_keys, key="api_keys"),
        "usernames": mask_config_secrets(conf.usernames, key="usernames"),
        "ui": {
            "dashboard_stats_enabled": conf.dashboard_stats_enabled,
            "ui_refresh_seconds": conf.ui_refresh_seconds,
            "dashboard_stats_modules": conf.dashboard_stats_modules,
            "stats_page_enabled": conf.stats_page_enabled,
            "category_appearance_profiles": getattr(conf, "category_appearance_profiles", {}),
        },
        # skip_files is not a declared Config field; it arrives through model_extra.
        "skip_files": getattr(
            conf,
            "skip_files",
            {"enabled": False, "display_mode": "disabled", "patterns": []},
        ),
        "auth": {
            "enable_password": conf.enable_password,
            "web_username": conf.web_username,
            # web_password intentionally NOT exposed
        },
        "categories": indexer_categories.get_available_categories(),
        "indexers": [idx.to_ui_dict(conf) for idx in indexer_registry.get_all_indexers()],
    }


def flat_config_view(conf: Config) -> Dict[str, Any]:
    """The active configuration as one flat mapping, secrets masked."""
    return mask_config_secrets(conf.model_dump(by_alias=True))


def settings_touch_indexers(section: str, updates: Dict[str, Any]) -> bool:
    """True when a settings save enables, disables or re-keys an indexer."""
    return section in ("destinations", "credentials") or any(str(key).startswith("enable_") for key in updates)


def update_settings(updates: Dict[str, Any], conf: Config) -> bool:
    """Save a settings section; a masked secret left untouched keeps its value."""
    return config_mod.save_config(merge_masked_secret_updates(updates, conf))


def config_preview(updates: Dict[str, Any], conf: Config) -> str:
    """The YAML the active config would become with these updates applied."""
    path = config_mod.get_config_path()
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    for key, value in merge_masked_secret_updates(updates, conf).items():
        data[key] = value
    return yaml.dump(data, default_flow_style=False, sort_keys=False)


def read_masked_raw_config() -> tuple[str, Path]:
    """The raw YAML with credentials masked. Raises FileNotFoundError when absent.

    Never falls back to echoing the file verbatim: that is the leak.
    """
    path = config_mod.get_config_path()
    if not path.exists():
        raise FileNotFoundError(str(path))
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    masked = mask_config_secrets(data)
    # allow_unicode keeps SECRET_MASK readable as bullets in the editor.
    return yaml.safe_dump(masked, default_flow_style=False, sort_keys=False, allow_unicode=True), path


def save_raw_config(content: str, conf: Config) -> Config:
    """Validate and install raw YAML, restoring any mask the operator left untouched.

    Raises yaml.YAMLError for unparseable content.
    """
    parsed = yaml.safe_load(content) or {}
    if isinstance(parsed, dict):
        restored = merge_masked_secret_updates(parsed, conf)
        content = yaml.safe_dump(restored, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return config_mod.replace_config_content(content)


def reset_to_defaults() -> Config:
    """Replace the active config with the bundled defaults. Raises FileNotFoundError."""
    defaults_path = config_mod.get_defaults_config_path()
    if not defaults_path.exists():
        raise FileNotFoundError(str(defaults_path))
    return config_mod.replace_config_content(defaults_path.read_text(encoding="utf-8"))


def _readme_path() -> Path:
    return APP_ROOT / "indexers" / "readme" / "readme.txt"


def read_readme() -> tuple[str, Path]:
    """The upload readme, falling back to the published example."""
    path = _readme_path()
    content_path = path if path.exists() else path.with_name("readme.example.txt")
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    return content, path


def save_readme(content: str) -> Path:
    """Write (or create) the upload readme atomically."""
    path = _readme_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)
    return path
