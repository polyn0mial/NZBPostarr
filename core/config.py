"""
📦 NZBPostarr - Configuration Module
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Simplified configuration management with Pydantic
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.utils import atomic_write_text, normalize_submission_category

# core/ lives directly under the project root.
APP_ROOT = Path(__file__).resolve().parent.parent

_AUTO_ROOT_CATEGORY = "auto"

_LEGACY_FOLDER_FIELDS: tuple[str, ...] = (
    "movies_folder",
    "tv_folder",
    "misc_folder",
)

DEFAULT_TV_PACK_IGNORE: Dict[str, bool] = {
    "enabled": True,
    "ignore_non_episode": True,
    "require_episode": True,
    "require_resolution": False,
    "require_source": True,
}

# Rule keys used before non-video + extras merged into ignore_non_episode and
# S##E## became the broader require_episode check.
_LEGACY_TV_PACK_IGNORE_KEYS: Dict[str, str] = {
    "ignore_non_video": "ignore_non_episode",
    "ignore_extras": "ignore_non_episode",
    "require_sxxexx": "require_episode",
}


def _normalize_tv_pack_ignore(raw: Any) -> Any:
    """Map legacy tv_pack_ignore rule keys onto the current key set.

    A current key always wins over its legacy spelling; legacy keys only fill
    a current key the config does not set, and are then dropped.
    """
    if not isinstance(raw, dict):
        return raw
    rules = {k: v for k, v in raw.items() if k not in _LEGACY_TV_PACK_IGNORE_KEYS}
    legacy_values: Dict[str, Any] = {}
    for key, value in raw.items():
        current_key = _LEGACY_TV_PACK_IGNORE_KEYS.get(key)
        if current_key:
            legacy_values[current_key] = value
    for key, value in legacy_values.items():
        rules.setdefault(key, bool(value))
    return rules

def _normalize_folder_entry_category(raw: Any) -> str:
    """Normalize saved folder-path categories while preserving explicit intent."""
    cleaned = str(raw or "").strip().lower()
    if cleaned in {"", "auto", "external"}:
        return _AUTO_ROOT_CATEGORY
    return normalize_submission_category(cleaned)


def _is_auto_root_category(raw: Any) -> bool:
    return _normalize_folder_entry_category(raw) == _AUTO_ROOT_CATEGORY


def _normalize_folder_paths_payload(data: Any) -> Any:
    if not isinstance(data, dict):
        return data

    external_values = data.get("external_folders") or []
    if isinstance(external_values, str):
        external_values = [external_values]
    elif not isinstance(external_values, list):
        external_values = []

    old_external = data.get("external_folder")
    if old_external and str(old_external).strip():
        old_external_path = str(old_external).strip()
        if old_external_path not in external_values:
            external_values.append(old_external_path)

    legacy_entries: List[Dict[str, Any]] = []
    for key in _LEGACY_FOLDER_FIELDS:
        value = data.get(key)
        if value and str(value).strip():
            legacy_entries.append({"path": str(value).strip()})
    for value in external_values:
        if value and str(value).strip():
            legacy_entries.append({"path": str(value).strip()})

    raw_folder_paths = data.get("folder_paths") or []
    if isinstance(raw_folder_paths, dict):
        raw_folder_paths = [raw_folder_paths]
    elif isinstance(raw_folder_paths, str):
        raw_folder_paths = [{"path": raw_folder_paths}]
    elif not isinstance(raw_folder_paths, list):
        raw_folder_paths = []

    if not raw_folder_paths:
        raw_folder_paths = legacy_entries

    normalized_folder_paths: List[Dict[str, Any]] = []
    seen_paths: set[str] = set()
    for fp in raw_folder_paths:
        if isinstance(fp, str):
            fp = {"path": fp}
        if not isinstance(fp, dict):
            continue
        path = str(fp.get("path", "") or "").strip()
        if not path:
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)
        entry: Dict[str, Any] = {
            "path": path,
            "category": _normalize_folder_entry_category(fp.get("category")),
            "monitor": bool(fp.get("monitor", False)),
            "allow_bulk_selection": bool(fp.get("allow_bulk_selection", True)),
        }
        normalized_folder_paths.append(entry)

    data["folder_paths"] = normalized_folder_paths
    data["movies_folder"] = None
    data["tv_folder"] = None
    data["misc_folder"] = None
    data["external_folders"] = [
        fp["path"] for fp in normalized_folder_paths if _is_auto_root_category(fp.get("category"))
    ]
    data.pop("external_folder", None)
    return data


class NNTPServer(BaseModel):
    """NNTP Server configuration model."""

    name: str
    host: str
    user: str
    password: str = Field(alias="pass")
    port: int
    ssl: bool
    enabled: bool
    max_connections: int
    backbone: List[str]
    model_config = {"populate_by_name": True}


class Config(BaseSettings):
    """Main application configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        env_prefix="NZBP_",
        extra="allow",  # Allow extra fields for dynamic indexers
        populate_by_name=True,
    )

    base_folder: Path
    # Full-backup archive destination (Settings > Folders). Relative paths
    # resolve against the app root.
    backup_folder: Path = Field(default_factory=lambda: APP_ROOT / "backups")
    folder_paths: List[Dict[str, Any]] = Field(default_factory=list)
    nntp_servers: List[NNTPServer]

    # Upload Settings
    rar_size: str
    article_size: str
    poster_name: str
    poster_email: str
    include_readme: bool

    @property
    def poster(self) -> str:
        """Combined identity for Nyuu's -f flag."""
        return f"{self.poster_name} <{self.poster_email}>"

    alt_bins: List[str]

    # Secrets / credentials
    api_keys: dict[str, str]
    usernames: dict[str, str]

    @model_validator(mode="before")
    @classmethod
    def migrate_poster(cls, data: Any) -> Any:
        if isinstance(data, dict) and "poster" in data:
            import re

            poster = data.pop("poster")
            if poster and isinstance(poster, str):
                match = re.search(r"(.*)\s+<(.*)>", poster)
                if match:
                    data["poster_name"] = match.group(1).strip()
                    data["poster_email"] = match.group(2).strip()
                else:
                    data["poster_name"] = poster

        return _normalize_folder_paths_payload(data)

    @field_validator("backup_folder", mode="before")
    @classmethod
    def resolve_backup_folder(cls, value: Any) -> Any:
        if value is None or not str(value).strip():
            return APP_ROOT / "backups"
        path = Path(str(value).strip()).expanduser()
        return path if path.is_absolute() else APP_ROOT / path

    @field_validator("tv_pack_ignore", mode="before")
    @classmethod
    def migrate_tv_pack_ignore(cls, value: Any) -> Any:
        return _normalize_tv_pack_ignore(value)

    rar_path: str = "rar"
    nyuu_path: str
    parpar_path: str
    verbose: bool
    quiet: bool
    test_run: bool
    enable_duplicate_checking: bool

    # Anime Identification (Jikan)
    enable_anime_checking: bool = False

    # Processing controls
    process_tv_episodes: bool
    dynamic_packs: bool = True
    tv_pack_ignore: Dict[str, Any] = Field(default_factory=lambda: dict(DEFAULT_TV_PACK_IGNORE))

    # Operation limits
    upload_max_retries: int
    upload_retry_delay_seconds: int
    item_limit_per_category: Optional[int]
    folder_size_limit_gb: Optional[int]
    folder_size_limit_enabled: bool
    file_size_limit_gb: Optional[int]
    file_size_limit_enabled: bool

    # Global Backfill Control
    enable_backfill: bool

    # Duplicate Bypass
    enable_duplicate_bypass: bool

    # Mediainfo (can be nil)
    nfolder: Optional[Path]

    # Web Settings
    debug: bool
    log_level: str
    host: str
    port: int
    ui_refresh_seconds: int

    # Optional password gate for the raw config editor.
    # If None, the editor is unrestricted (trust network/host-level access).
    web_password: Optional[str] = None

    # Web access authentication (HTTP Basic Auth for all pages/APIs).
    enable_password: bool = False
    web_username: str = "admin"

    # Pending-page UI persistence shared across browsers/users.
    pending_external_group_order: List[str] = Field(default_factory=list)
    pending_external_group_order_locked: bool = False

    # Model Context Protocol endpoint, mounted at /mcp when enabled.
    # Off by default: it needs the optional `mcp` package, and it exposes queue
    # control to any client holding mcp_token. The token is required; without
    # one the endpoint stays unmounted even if mcp_enabled is true.
    mcp_enabled: bool = False
    mcp_token: Optional[str] = None
    mcp_allow_mutations: bool = False
    # Host header allow-list for the MCP endpoint. ["*"] disables the SDK's
    # DNS-rebinding check, which is the workable default for a self-hosted app
    # reached under a LAN IP, reverse proxy or container alias.
    mcp_allowed_hosts: List[str] = ["*"]

    # Dashboard Settings
    dashboard_stats_enabled: bool
    dashboard_stats_modules: List[str]
    stats_page_enabled: bool = True
    category_appearance_profiles: Dict[str, Any] = Field(default_factory=dict)

    def get_api_key(self, field_name: str) -> Optional[str]:
        """Get an API key by name from config fields, api_keys mapping, or model_extra."""
        val = self.get_secret(field_name)
        return str(val) if val is not None else None

    def get_username(self, field_name: str) -> Optional[str]:
        """Get a username by name from config fields, usernames mapping, or model_extra."""
        val = self.get_secret(field_name, namespaces=("usernames",))
        return str(val) if val is not None else None

    def get_secret(
        self,
        field_name: str,
        namespaces: tuple[str, ...] = ("api_keys", "usernames"),
    ) -> Optional[str]:
        """Get a secret value from config in a few common locations.

        Supported locations:
        - Direct attribute (including extra fields, e.g. `geek_api_key`)
        - Mappings: `api_keys[field_name]`, `usernames[field_name]`
        - model_extra direct key and nested mappings (backward compatible)
        """
        # 1. Try direct attribute (defined fields or top-level extras)
        try:
            val = getattr(self, field_name, None)
            if val not in (None, ""):
                return str(val)
        except (AttributeError, TypeError):
            pass

        # 2. Try explicit mappings (preferred)
        for ns in namespaces:
            mapping = getattr(self, ns, None)
            if isinstance(mapping, dict):
                v = mapping.get(field_name)
                if v not in (None, ""):
                    return str(v)

        # 3. Try model_extra (backward compatible for older configs)
        extra = getattr(self, "model_extra", None) or {}
        if isinstance(extra, dict):
            direct = extra.get(field_name)
            if direct not in (None, ""):
                return str(direct)

            for ns in namespaces:
                mapping = extra.get(ns, {})
                if isinstance(mapping, dict):
                    v = mapping.get(field_name)
                    if v not in (None, ""):
                        return str(v)

        return None

    @property
    def script_dir(self) -> Path:
        return APP_ROOT

    @property
    def log_db(self) -> Path:
        path = APP_ROOT / "data" / "history" / "usenet_uploads.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def tmp_sub(self) -> Path:
        path = APP_ROOT / "data" / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def nzb_sub(self) -> Path:
        path = APP_ROOT / "data" / "nzbs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def mediainfo_sub(self) -> Path:
        path = APP_ROOT / "data" / "mediainfo"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_nzb_path(self, name: str) -> Path:
        """Get the output path for a generated NZB file."""
        return self.nzb_sub / f"{name}.nzb"

    def get_folder_path_entries(self) -> List[Dict[str, Any]]:
        """Return normalized folder-path entries for UI and scan consumers."""
        entries: List[Dict[str, Any]] = []
        for fp in self.folder_paths:
            if not isinstance(fp, dict):
                continue
            path = str(fp.get("path", "") or "").strip()
            if not path:
                continue
            category = _normalize_folder_entry_category(fp.get("category"))
            entries.append(
                {
                    "path": path,
                    "category": category,
                    "monitor": bool(fp.get("monitor", False)),
                    "allow_bulk_selection": bool(fp.get("allow_bulk_selection", True)),
                }
            )
        return entries

    def get_configured_folders(self) -> List[Path]:
        """Return configured scan folders in saved order."""
        folders: List[Path] = []
        for fp in self.get_folder_path_entries():
            folders.append(Path(fp["path"]))
        return folders

    def get_folders_for_category(self, category: str) -> List[Path]:
        """Return configured folder paths for a normalized category."""
        folders: List[Path] = []
        wanted = _normalize_folder_entry_category(category)
        for fp in self.get_folder_path_entries():
            if wanted and fp["category"] != wanted:
                continue
            folders.append(Path(fp["path"]))
        return folders

def get_config_path() -> Path:
    """Return the active Nzbpostarr config file path.

    The live config is kept in the user's XDG config area so it is clearly
    labeled as Nzbpostarr-owned state instead of sitting loose in the app root.
    """
    env_path = os.getenv("NZBPOSTARR_CONFIG")
    if env_path:
        return Path(env_path).expanduser()
    return APP_ROOT / ".config" / "nzbpostarr" / "config.yaml"


def get_defaults_config_path() -> Path:
    """Return the bundled reference/default configuration path."""
    return Path(__file__).with_name("config.defaults.yaml")


_GLOBAL_CONFIG: Optional[Config] = None
_CONFIG_MTIME_NS: Optional[int] = None
_CONFIG_LOCK = threading.Lock()


def load_config() -> Config:
    """Load configuration from YAML and environment variables."""
    path = get_config_path()
    if not path.exists():
        defaults_path = get_defaults_config_path()
        if defaults_path.exists():
            print(
                f"Configuration file not found at: {path}; loading defaults from {defaults_path}",
                file=sys.stderr,
            )
            with open(defaults_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            # Last-resort fallback keeps startup errors explicit via pydantic validation.
            print(f"Configuration file not found at: {path}", file=sys.stderr)
            data = {}
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

    # Ensure required mapping fields are always present.
    data.setdefault("api_keys", {})
    data.setdefault("usernames", {})
    data.setdefault("external_folders", [])
    # Expand variables manually for YAML data before passing to Pydantic
    base = str(data.get("base_folder", APP_ROOT))
    nfolder_raw = str(data.get("nfolder", "data/mediainfo"))
    nfolder = nfolder_raw.replace("${base_folder}", base)

    def expand_vars(val: Any) -> Any:
        if isinstance(val, str):
            val = val.replace("${base_folder}", base)
            val = val.replace("${nfolder}", nfolder)
            return val
        if isinstance(val, list):
            return [expand_vars(v) for v in val]
        if isinstance(val, dict):
            return {k: expand_vars(v) for k, v in val.items()}
        return val

    data = expand_vars(data)

    # BaseSettings handles environment variables automatically
    return Config(**data)


def _config_mtime_ns() -> Optional[int]:
    try:
        return get_config_path().stat().st_mtime_ns
    except OSError:
        return None


def get_config() -> Config:
    """Get or reload the global configuration instance.

    Manual edits to the active config file are picked up on the next config
    access, which means web auth changes apply on the next request without a
    full process restart.
    """
    global _GLOBAL_CONFIG, _CONFIG_MTIME_NS
    with _CONFIG_LOCK:
        current_mtime = _config_mtime_ns()
        if _GLOBAL_CONFIG is None or current_mtime != _CONFIG_MTIME_NS:
            _GLOBAL_CONFIG = load_config()
            _CONFIG_MTIME_NS = _config_mtime_ns()
    return _GLOBAL_CONFIG


def _validate_config_yaml(content: str) -> Dict[str, Any]:
    parsed = yaml.safe_load(content) or {}
    if not isinstance(parsed, dict):
        raise ValueError("Configuration YAML must contain a mapping at the document root")
    return parsed


def _replace_config_content_locked(content: str) -> Config:
    """Atomically install validated YAML and restore the prior file on failure."""
    global _GLOBAL_CONFIG, _CONFIG_MTIME_NS

    _validate_config_yaml(content)
    dest_path = get_config_path()
    previous_content = dest_path.read_text(encoding="utf-8") if dest_path.exists() else None
    backup_path = dest_path.with_name(f"{dest_path.name}.bak")

    if previous_content is not None:
        atomic_write_text(backup_path, previous_content)

    atomic_write_text(dest_path, content)
    try:
        new_config = load_config()
    except Exception:
        if previous_content is not None:
            atomic_write_text(dest_path, previous_content)
        else:
            dest_path.unlink(missing_ok=True)
        raise

    _GLOBAL_CONFIG = new_config
    _CONFIG_MTIME_NS = _config_mtime_ns()
    return new_config


def replace_config_content(content: str) -> Config:
    """Replace the complete active config through the synchronized writer."""
    with _CONFIG_LOCK:
        return _replace_config_content_locked(content)


def save_config(updates: dict[str, Any]) -> bool:
    """Save configuration updates back to the active YAML file."""
    with _CONFIG_LOCK:
        # Save back to the file we are currently using
        dest_path = get_config_path()

        try:
            # Load current data
            data: Dict[str, Any] = {}
            if dest_path.exists():
                with open(dest_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}

            # Process updates
            string_fields = {"rar_size", "article_size"}

            for k, v in updates.items():
                if k in string_fields and not isinstance(v, str):
                    data[k] = str(v) if v is not None else ""
                else:
                    data[k] = v

            data = _normalize_folder_paths_payload(data)

            content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False)
            _replace_config_content_locked(content)
            return True
        except (OSError, TypeError, ValueError, yaml.YAMLError) as e:
            print(f"Error saving config: {e}")
            return False
