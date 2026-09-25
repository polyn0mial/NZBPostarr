"""
📦 NZBPostarr - Indexer Registry
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Core registry that loads and manages indexer plugins.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import re
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Literal, Optional, Tuple

import requests
import yaml
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from core.redaction import redact_mapping, redact_text, redact_url
from core.utils import log_success, log_verbose, normalize_submission_category

# Singleton instance and lock
_REGISTRY = None
_REGISTRY_LOCK = threading.RLock()

SubmitStatus = Literal["success", "duplicate", "misconfigured", "rejected", "network_error"]
SubmitResult = Tuple[bool, SubmitStatus, str]
_FileSource = Path | bytes
_FileSpec = Tuple[str, str, _FileSource, str]

# Closed sets documented in indexers/_template.yaml. Kept here so both the
# submission HTTP method and the auth method fail fast at load time (naming
# the offending YAML file) instead of silently falling through to a no-op
# branch on a typo.
_VALID_SUBMIT_METHODS: Tuple[str, ...] = ("POST", "PUT", "CURL")
_VALID_AUTH_METHODS: Tuple[str, ...] = ("query_param", "header", "form_field", "curl_url", "none")


@dataclass(frozen=True, slots=True)
class _SubmissionRequest:
    """Fully planned HTTP request, separate from network and response effects."""

    method: str
    url: str
    params: Dict[str, str]
    data: Dict[str, str]
    headers: Dict[str, str]
    file_specs: Tuple[_FileSpec, ...]
    is_curl: bool
    submission_filename: str

_INDEXER_CATEGORY_ALIASES = {
    "movie_pack": "movie",
    "tv_episode": "tv",
    "tv_pack": "tv",
}


class CategoryMapping(BaseModel):
    """Maps content types to indexer-specific category codes.

    Standard keys: tv, movie, misc, default.
    Extra keys (e.g. anime, music) are supported via extra='allow'.
    """

    model_config = {"extra": "allow"}

    tv: str = ""
    movie: str = ""
    movie_sd: str = ""
    movie_hd: str = ""
    movie_uhd: str = ""
    movie_dvd: str = ""
    movie_full_br: str = ""
    anime: str = ""
    disc: str = ""
    music: str = ""
    audiobooks: str = ""
    books: str = ""
    apps: str = ""
    misc: str = ""
    default: str = ""

    @staticmethod
    def normalize_key(category: str) -> str:
        """Normalize internal category ids into YAML mapping keys."""
        cleaned = str(category or "").strip().lower()
        if cleaned in _INDEXER_CATEGORY_ALIASES:
            return _INDEXER_CATEGORY_ALIASES[cleaned]
        normalized = normalize_submission_category(cleaned)
        return "movie" if normalized == "movies" else normalized

    def resolve_code(self, category: str) -> tuple[Optional[str], Optional[str], bool]:
        """Resolve a category to an indexer code without silently using `default`."""
        requested = self.normalize_key(category)
        if not requested:
            return None, None, False

        candidates = [requested]
        if requested == "movie_dvd":
            candidates.extend(["movie_sd", "movie"])
        elif requested == "movie_sd":
            candidates.append("movie")
        elif requested == "movie_hd":
            candidates.append("movie")
        elif requested == "movie_uhd":
            candidates.extend(["movie_hd", "movie"])
        elif requested == "movie_full_br":
            candidates.extend(["movie_hd", "movie"])
        elif requested == "audiobooks":
            candidates.append("books")

        seen: set[str] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            code = getattr(self, candidate, None)
            if code not in (None, ""):
                return str(code), candidate, candidate == requested

        return None, None, False

    def uses_submission_category(self) -> bool:
        """Return True when the indexer definition has any non-empty category codes."""
        dump = self.model_dump()
        return any(bool(value) for key, value in dump.items() if key != "default")

    def supported_categories(self) -> list[str]:
        """Return list of category keys this mapping supports (excluding 'default')."""
        return [k for k in self.model_dump() if k != "default" and self.model_dump()[k]]


class AuthConfig(BaseModel):
    """Authentication configuration for an indexer."""

    method: str = "query_param"  # query_param, header, form_field, curl_url, or none
    api_key_param: str = "apikey"  # Parameter name for API key
    username_param: Optional[str] = None  # For indexers requiring username
    header_name: Optional[str] = None  # For header-based auth
    header_template: Optional[str] = None  # e.g., "Bearer {api_key}"

    @field_validator("method")
    @classmethod
    def _validate_auth_method(cls, value: str) -> str:
        if value not in _VALID_AUTH_METHODS:
            raise ValueError(f"auth.method must be one of {_VALID_AUTH_METHODS}, got {value!r}")
        return value


class FileFields(BaseModel):
    """File field names for multipart upload."""

    nzb: str = "nzb"
    nfo: Optional[str] = None  # Only set if indexer explicitly supports NFO uploads
    mediainfo: Optional[str] = None
    nzb_mime: str = "application/x-nzb"


class SuccessPatterns(BaseModel):
    """Patterns to detect successful submission."""

    json_path: Optional[str] = None  # e.g., "response.@attributes.API"
    json_value: Optional[str] = None  # e.g., "OK"
    text_patterns: List[str] = []  # e.g., ["OK", "SUCCESS", "<SUCCESS"]
    duplicate_patterns: List[str] = []  # e.g., ["duplicate", "already exists"]


class IndexerDefinition(BaseModel):
    """Complete definition of an indexer plugin."""

    # Identity
    id: str  # Internal key (e.g., "geek", "planet")
    name: str  # Display name (e.g., "NZBGeek")
    description: Optional[str] = None
    website: Optional[str] = None
    favicon_url: Optional[str] = None
    search_url: Optional[str] = None

    # API Configuration
    submit_url: str
    method: str = "POST"  # POST, PUT, or special "CURL" for curl-based
    curl_template: Optional[str] = None  # For curl-based indexers like OMG

    # Optional opt-in shortcut for well-known request shapes. Currently only
    # "newznab" is supported: it explicitly requests the same auto-populated
    # t/apikey/category/name query params that the URL-suffix/extra_params
    # heuristic below already infers for plain Newznab-API sites (NZBGeek,
    # NZBPlanet, NZB.su). Leave unset to keep relying on that heuristic.
    profile: Optional[Literal["newznab"]] = None

    # Authentication
    auth: AuthConfig = Field(default_factory=AuthConfig)

    # Category Mapping
    categories: CategoryMapping = Field(default_factory=CategoryMapping)
    category_param: str = "cat"  # Parameter name for category

    # File Upload
    files: FileFields = Field(default_factory=FileFields)
    name_param: Optional[str] = None  # Parameter for release name

    # Extra parameters (static values to always include)
    extra_params: Dict[str, str] = {}
    extra_form_data: Dict[str, str] = {}

    # Success Detection
    success: SuccessPatterns = Field(default_factory=SuccessPatterns)

    # Behavior
    timeout: int = 30
    requires_nfo: bool = False  # If True, creates empty NFO if missing
    enabled: bool = True  # Default state if not in config.yaml
    backfill: bool = True  # Default backfill state
    priority: bool = False  # Default priority status

    # UI Configuration
    icon: Optional[str] = None  # Lucide icon name
    color: Optional[str] = "#808080"  # Hex color code (e.g. #FF0000)

    @field_validator("method")
    @classmethod
    def _validate_method(cls, value: str) -> str:
        # Consuming code always compares indexer.method.upper(), so accept any
        # case but canonicalize to the uppercase form here; a genuinely
        # unrecognized value (a typo, not just wrong case) fails at load.
        normalized = str(value).strip().upper()
        if normalized not in _VALID_SUBMIT_METHODS:
            raise ValueError(f"method must be one of {_VALID_SUBMIT_METHODS} (case-insensitive), got {value!r}")
        return normalized

    @property
    def log_name(self) -> str:
        """Returns the indexer name wrapped in Loguru color tags if hex color is set."""
        if self.color and self.color.startswith("#"):
            return f"<fg {self.color}>{self.name}</fg>"
        return self.name

    def to_ui_dict(self, conf: Any = None) -> Dict[str, Any]:
        """Convert to a dictionary optimized for frontend display."""
        # Resolve dynamic states from config
        enabled = resolve_indexer_enabled(self, conf)
        backfill = resolve_indexer_backfill(self, conf)
        priority = resolve_indexer_priority(self, conf)

        data = {
            "id": str(self.id),
            "name": str(self.name),
            "description": self.description,
            "website": self.website,
            "favicon_url": self.favicon_url,
            "search_url": self.search_url,
            "icon": self.icon,
            "color": self.color,
            "default_priority": bool(priority),
            "priority": bool(priority),
            "enabled": bool(enabled),
            "backfill": bool(backfill),
        }

        # Status information
        data["has_api_key"] = bool(resolve_indexer_api_key(self, conf))
        data["requires_username"] = _requires_username(self)

        # Ordinary indexer metadata never returns credential values. The
        # Settings API exposes masked placeholders for configured credentials.
        data["api_key"] = ""
        data["username"] = ""

        return data


def _requires_username(indexer: IndexerDefinition) -> bool:
    """Return True if an indexer needs a username in addition to an API key."""
    if indexer.auth.username_param:
        return True

    if indexer.method.upper() == "CURL" and indexer.curl_template:
        tmpl = indexer.curl_template
        return ("{username}" in tmpl) or ("{user}" in tmpl)

    return False


def resolve_indexer_api_key(indexer: IndexerDefinition, config: Any) -> Optional[str]:
    """Resolve an indexer's API key from config/env ONLY."""
    if not config:
        return None

    getter = getattr(config, "get_api_key", None)
    if callable(getter):
        # Prefer explicit per-indexer field, then api_keys mapping keyed by indexer id.
        return (getter(f"{indexer.id}_api_key") or "").strip() or (getter(indexer.id) or "").strip() or None

    # Fallback (dict-like configs)
    if isinstance(config, dict):
        for key in (f"{indexer.id}_api_key", indexer.id):
            val = config.get(key)
            if val:
                return str(val).strip() or None

    return None


def _resolve_indexer_bool_field(indexer: IndexerDefinition, config: Any, field_prefix: str, default_val: bool) -> bool:
    """Generic helper to resolve boolean fields from config."""
    if not config:
        return default_val

    # Try attribute access (e.g., enable_geek, backfill_geek)
    attr_name = f"{field_prefix}_{indexer.id}"
    val = getattr(config, attr_name, None)
    if val is not None:
        return bool(val)

    # Try model_extra
    extra = getattr(config, "model_extra", {})
    if isinstance(extra, dict):
        val = extra.get(attr_name)
        if val is not None:
            return bool(val)

    return default_val


def resolve_indexer_enabled(indexer: IndexerDefinition, config: Any) -> bool:
    """Resolve whether an indexer is enabled from config (enable_indexerid)."""
    return _resolve_indexer_bool_field(indexer, config, "enable", indexer.enabled)


def resolve_indexer_backfill(indexer: IndexerDefinition, config: Any) -> bool:
    """Resolve whether an indexer has backfill enabled from config."""
    return _resolve_indexer_bool_field(indexer, config, "backfill", indexer.backfill)


def resolve_indexer_priority(indexer: IndexerDefinition, config: Any) -> bool:
    """Resolve whether an indexer has priority enabled from config."""
    return _resolve_indexer_bool_field(indexer, config, "priority", indexer.priority)


def resolve_indexer_username(indexer: IndexerDefinition, config: Any) -> Optional[str]:
    """Resolve an indexer's username from config/env ONLY."""
    if not config:
        return None

    getter = getattr(config, "get_username", None)
    if callable(getter):
        return (getter(f"{indexer.id}_username") or "").strip() or (getter(indexer.id) or "").strip() or None

    # Backward compatible fallback: allow `get_api_key("omg_username")` style configs
    getter2 = getattr(config, "get_api_key", None)
    if callable(getter2):
        return (getter2(f"{indexer.id}_username") or "").strip() or None

    if isinstance(config, dict):
        key = f"{indexer.id}_username"
        val = config.get(key)
        if val:
            return str(val).strip() or None

    return None


class IndexerRegistry:
    """
    Central registry for all loaded indexer definitions.
    Indexers are loaded from YAML files in the indexers/ directory.
    """

    _instance: Optional["IndexerRegistry"] = None

    def __new__(cls) -> "IndexerRegistry":
        if cls._instance is None:
            with _REGISTRY_LOCK:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        with _REGISTRY_LOCK:
            if getattr(self, "_initialized", False):
                return
            self._indexers: Dict[str, IndexerDefinition] = {}
            self._indexer_files: Dict[str, Path] = {}
            self._load_indexers()
            self._initialized = True

    @property
    def indexers_dir(self) -> Path:
        return Path(__file__).parent.parent / "indexers"

    def _load_indexers(self) -> None:
        """Load all YAML indexer definitions from the indexers directory."""
        self._indexers.clear()
        self._indexer_files.clear()

        for yaml_file in sorted(self.indexers_dir.glob("*.yaml"), key=lambda path: path.name.lower()):
            stem_lower = yaml_file.stem.lower()
            if yaml_file.name.startswith("_") or stem_lower.endswith(".example") or stem_lower.endswith(".template"):
                continue  # Skip files starting with underscore, or *.example.yaml/*.template.yaml

            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                if not data:
                    continue

                # Support multiple indexers in one file (or single)
                indexers_data = data if isinstance(data, list) else [data]

                for idx_data in indexers_data:
                    try:
                        indexer = IndexerDefinition(**idx_data)
                        self._indexers[indexer.id] = indexer
                        self._indexer_files[indexer.id] = yaml_file

                        # Only log individual indexers on first load (VERBOSE) or error
                        # Suppress during reloads unless VERBOSE is on to avoid clutter
                        if not getattr(self, "_initialized", False):
                            log_verbose(f"Loaded indexer: {indexer.log_name} ({indexer.id})")
                        else:
                            log_verbose(f"Reloaded indexer: {indexer.log_name} ({indexer.id})")
                    except Exception as e:
                        logger.warning(f"Failed to parse indexer in {yaml_file.name}: {e}")

            except Exception as e:
                logger.warning(f"Failed to load indexer file {yaml_file.name}: {e}")

        # Summary Logging
        if not getattr(self, "_initialized", False):
            # First boot (Summary only, individual items were logged as VERBOSE above)
            log_verbose(f"Indexer Registry Initialized: {len(self._indexers)} indexer(s) ready")
        else:
            # Subsequent reloads (Single line to avoid console clutter)
            log_verbose(f"Indexer Registry Reloaded: {len(self._indexers)} indexer(s) updated")

    def reload(self) -> None:
        """Reload all indexer definitions."""
        start = time.time()
        self._load_indexers()
        elapsed = time.time() - start
        if elapsed > 0.1:
            logger.debug(f"Indexer Registry loaded in {elapsed:.3f}s")

    def get(self, indexer_id: str) -> Optional[IndexerDefinition]:
        """Get an indexer by ID."""
        return self._indexers.get(indexer_id)

    def all(self) -> List[IndexerDefinition]:
        """Get all loaded indexers in configured file order."""
        return list(self._indexers.values())

    def ids(self) -> List[str]:
        """Get all indexer IDs."""
        return list(self._indexers.keys())

    def enabled(self, config: Any) -> List[IndexerDefinition]:
        """Get indexers that are enabled and configured (YAML + config/env secrets)."""
        enabled_list = []
        for indexer in self.all():
            # Resolve enabled state from config (fallback to YAML default)
            is_enabled = resolve_indexer_enabled(indexer, config)

            if is_enabled:
                if indexer.auth.method == "none":
                    enabled_list.append(indexer)
                    continue

                # Check if API key is configured (YAML or config/env)
                if not resolve_indexer_api_key(indexer, config):
                    continue

                # Some indexers require a username as well (e.g. CURL templates)
                if _requires_username(indexer) and not resolve_indexer_username(indexer, config):
                    continue

                enabled_list.append(indexer)
        return enabled_list


def get_registry() -> IndexerRegistry:
    """Get the global indexer registry instance (singleton)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = IndexerRegistry()
    return _REGISTRY


def reload_indexers() -> None:
    """Reload all indexer definitions."""
    get_registry().reload()


def get_indexer(indexer_id: str) -> Optional[IndexerDefinition]:
    """Get an indexer by ID."""
    return get_registry().get(indexer_id)


def get_all_indexers() -> List[IndexerDefinition]:
    """Get all loaded indexers."""
    return get_registry().all()


def get_enabled_indexers(config: Any) -> List[IndexerDefinition]:
    """Get indexers that are enabled and configured."""
    return get_registry().enabled(config)


# ── Category Discovery ──────────────────────────────────────────────

# Known system categories: maps indexer YAML key → internal ID + display metadata
_KNOWN_CATEGORIES: Dict[str, Dict[str, str]] = {
    "movie": {"id": "movies", "label": "Movies", "icon": "film", "color": "purple-400"},
    "tv": {"id": "tv", "label": "TV Shows", "icon": "tv", "color": "cyan-400"},
    "anime": {"id": "anime", "label": "Anime", "icon": "swords", "color": "pink-400"},
    "disc": {"id": "disc", "label": "DISC", "icon": "disc-3", "color": "slate-400"},
    "audiobooks": {
        "id": "audiobooks",
        "label": "Audiobooks",
        "icon": "headphones",
        "color": "teal-400",
    },
    "books": {
        "id": "books",
        "label": "Books",
        "icon": "book-open",
        "color": "emerald-400",
    },
    "music": {"id": "music", "label": "Music", "icon": "music", "color": "blue-400"},
    "apps": {"id": "apps", "label": "Apps", "icon": "app-window", "color": "red-400"},
    "misc": {"id": "misc", "label": "Misc", "icon": "package", "color": "orange-400"},
}


def _cat_key_to_id(key: str) -> str:
    """Convert an indexer YAML category key to the internal system category ID."""
    if key in _KNOWN_CATEGORIES:
        return _KNOWN_CATEGORIES[key]["id"]
    return key


def get_available_categories() -> List[Dict[str, Any]]:
    """Discover available categories from all loaded indexer YAML definitions.

    Returns a list of category dicts, each with:
        id       – internal system name (e.g. 'movies', 'tv', 'misc')
        key      – indexer YAML key (e.g. 'movie', 'tv', 'misc')
        label    – human-readable label
        icon     – Lucide icon name
        color    – Tailwind color class
        indexers – list of indexer info dicts that support this category
    """
    registry = get_registry()
    cats: Dict[str, Dict[str, Any]] = {}

    for indexer in registry.all():
        mapping = indexer.categories.model_dump()
        for yaml_key, code in mapping.items():
            if yaml_key == "default" or not code:
                continue

            cat_id = _cat_key_to_id(yaml_key)
            known = _KNOWN_CATEGORIES.get(yaml_key, {})

            if cat_id not in cats:
                cats[cat_id] = {
                    "id": cat_id,
                    "key": yaml_key,
                    "label": known.get("label", yaml_key.title()),
                    "icon": known.get("icon", "box"),
                    "color": known.get("color", "gray-400"),
                    "indexers": [],
                }

            cats[cat_id]["indexers"].append(
                {
                    "id": indexer.id,
                    "name": indexer.name,
                    "favicon_url": indexer.favicon_url,
                    "color": indexer.color,
                }
            )

    # Stable sort: known categories first (movie, tv, misc), then custom alphabetically
    known_order = [
        "movies",
        "tv",
        "anime",
        "disc",
        "music",
        "audiobooks",
        "books",
        "apps",
        "misc",
    ]
    result = []
    for cid in known_order:
        if cid in cats:
            result.append(cats.pop(cid))
    result.extend(sorted(cats.values(), key=lambda c: c["label"]))
    return result


def _resolve_category(indexer: IndexerDefinition, cat: str) -> tuple[Optional[str], Optional[str], bool]:
    """Resolve a submission category without silently falling back to `default`."""
    return indexer.categories.resolve_code(cat)


def _resolve_movie_submission_key(cat: str, rls_name: str) -> str:
    """Refine generic movie categories into movie sub-types based on release metadata."""
    normalized = CategoryMapping.normalize_key(cat)
    if normalized != "movie":
        return cat

    upper_name = str(rls_name or "").upper()
    dvd_tokens = ("DVD", "DVD5", "DVD9", "DVDR", "DVDRIP", "NTSC", "PAL", "VIDEO_TS")
    if any(token in upper_name for token in dvd_tokens):
        return "movie_dvd"
    if any(token in upper_name for token in ("2160P", "UHD", "4K")):
        return "movie_uhd"
    if any(token in upper_name for token in ("FULLBR", "FULL.BR", "BDISO", "BD25", "BD50")):
        return "movie_full_br"
    if any(token in upper_name for token in ("1080P", "720P", "BLURAY", "WEB-DL", "WEBRIP", "HDTV", "REMUX")):
        return "movie_hd"
    return "movie_sd"


def _check_success(indexer: IndexerDefinition, response: requests.Response) -> tuple[bool, bool]:
    """
    Check if submission was successful.
    Returns (success, is_duplicate).
    """
    text = response.text
    text_upper = text.upper()
    text_lower = text.lower()

    # OMG can include generic success-looking words in duplicate/error pages.
    # Treat duplicate text as a non-accepted state unless a later bypass attempt
    # returns a clean success response.
    # Check duplicate patterns first
    for pattern in indexer.success.duplicate_patterns:
        if pattern.lower() in text_lower:
            return True, True

    # Check JSON response if configured
    if indexer.success.json_path:
        try:
            data = response.json()
            # Navigate the JSON path
            parts = indexer.success.json_path.split(".")
            value = data
            for part in parts:
                if isinstance(value, dict):
                    value = value.get(part, {})
                else:
                    value = None
                    break

            # If json_value is set, check for exact match
            if indexer.success.json_value:
                if str(value).upper() == indexer.success.json_value.upper():
                    return True, False
            # Otherwise, just check if the value is truthy (exists and not empty)
            elif value:
                return True, False
        except (TypeError, ValueError):
            pass

    # Check text patterns - use word-boundary matching for short patterns
    # to avoid false positives (e.g. "OK" matching inside "TOKEN" or "BROKEN").
    for pattern in indexer.success.text_patterns:
        pat_upper = pattern.upper()
        if len(pat_upper) <= 3:
            # Short patterns: require word-boundary match
            if re.search(r"(?<![A-Z])" + re.escape(pat_upper) + r"(?![A-Z])", text_upper):
                return True, False
        else:
            if pat_upper in text_upper:
                return True, False

    return False, False


def _add_category_and_name(
    fields: Dict[str, str],
    indexer: IndexerDefinition,
    category: Optional[str],
    rls_name: str,
) -> None:
    if category:
        fields[indexer.category_param] = category
    if indexer.name_param:
        fields[indexer.name_param] = rls_name


def _build_submission_fields(
    indexer: IndexerDefinition,
    rls_name: str,
    api_key: Optional[str],
    username: Optional[str],
    category: Optional[str],
    *,
    is_curl: bool,
) -> tuple[Dict[str, str], Dict[str, str]]:
    params: Dict[str, str] = {}
    data: Dict[str, str] = dict(indexer.extra_form_data)

    if indexer.auth.method == "query_param" and api_key:
        params[indexer.auth.api_key_param] = api_key
        if username and indexer.auth.username_param:
            params[indexer.auth.username_param] = username

    if is_curl:
        if category:
            data["catid"] = category
        data.update(upload="upload", rlsname=rls_name.replace(",", "."))
    else:
        is_newznab = (
            indexer.profile == "newznab"
            or "t" in indexer.extra_params
            or indexer.submit_url.endswith("/api")
        )
        if is_newznab:
            params["t"] = indexer.extra_params.get("t", "nzbadd")
            if api_key:
                params[indexer.auth.api_key_param] = api_key
            _add_category_and_name(params, indexer, category, rls_name)
            _add_category_and_name(data, indexer, category, rls_name)
        elif indexer.method.upper() == "POST":
            _add_category_and_name(data, indexer, category, rls_name)
        else:
            _add_category_and_name(params, indexer, category, rls_name)

        if indexer.auth.method == "form_field" and api_key:
            data[indexer.auth.api_key_param] = api_key

    params.update(indexer.extra_params)
    return params, data


def _build_submission_headers(indexer: IndexerDefinition, api_key: Optional[str]) -> Dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    if indexer.auth.method == "header" and api_key:
        if indexer.auth.header_template:
            headers[indexer.auth.header_name or "Authorization"] = indexer.auth.header_template.format(api_key=api_key)
        else:
            headers[indexer.auth.header_name or "Authorization"] = f"Bearer {api_key}"
    if indexer.auth.method == "header":
        headers["Accept"] = "application/json"
    return headers


def _build_submission_file_specs(
    indexer: IndexerDefinition,
    nzb_path: Path,
    submission_filename: str,
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
    *,
    is_curl: bool,
) -> Tuple[_FileSpec, ...]:
    file_specs: List[_FileSpec] = [
        (indexer.files.nzb, submission_filename, nzb_path, indexer.files.nzb_mime),
    ]
    if indexer.files.nfo or is_curl:
        nfo_field = indexer.files.nfo or "nfo"
        if nfo_path and nfo_path.exists():
            file_specs.append((nfo_field, nfo_path.name, nfo_path, "text/plain"))
        elif mediainfo_path and mediainfo_path.exists():
            file_specs.append((nfo_field, "mediainfo.nfo", mediainfo_path, "text/plain"))
        elif indexer.requires_nfo or is_curl:
            file_specs.append((nfo_field, f"{indexer.id}_empty.nfo", b"", "text/plain"))
    if indexer.files.mediainfo and mediainfo_path and mediainfo_path.exists():
        file_specs.append((indexer.files.mediainfo, mediainfo_path.name, mediainfo_path, "text/plain"))
    return tuple(file_specs)


def _build_submission_request(
    *,
    indexer: IndexerDefinition,
    rls_name: str,
    nzb_path: Path,
    api_key: Optional[str],
    username: Optional[str],
    category: Optional[str],
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
) -> _SubmissionRequest:
    """Plan request metadata and file sources without opening files or performing I/O."""
    is_curl = indexer.method.upper() == "CURL"
    submit_url = indexer.submit_url
    if is_curl and indexer.curl_template:
        submit_url = indexer.curl_template.format(
            api_key=api_key or "",
            username=username or "",
            user=username or "",
            api=api_key or "",
        )

    submission_filename = f"{rls_name}.nzb"
    params, data = _build_submission_fields(
        indexer,
        rls_name,
        api_key,
        username,
        category,
        is_curl=is_curl,
    )

    method = "POST" if is_curl or indexer.method.upper() == "POST" else indexer.method.upper()
    return _SubmissionRequest(
        method=method,
        url=submit_url,
        params=params,
        data=data,
        headers=_build_submission_headers(indexer, api_key),
        file_specs=_build_submission_file_specs(
            indexer,
            nzb_path,
            submission_filename,
            nfo_path,
            mediainfo_path,
            is_curl=is_curl,
        ),
        is_curl=is_curl,
        submission_filename=submission_filename,
    )


@contextmanager
def _open_multipart_files(file_specs: Tuple[_FileSpec, ...]) -> Iterator[Dict[str, tuple]]:
    """Open request streams for exactly one attempt and close every handle together."""
    with ExitStack() as stack:
        payload: Dict[str, tuple] = {}
        for field_name, filename, source, mime in file_specs:
            stream = stack.enter_context(source.open("rb")) if isinstance(source, Path) else source
            payload[field_name] = (filename, stream, mime)
        yield payload


def _estimate_payload_size(file_specs: Tuple[_FileSpec, ...]) -> int:
    total = 0
    for _field_name, _filename, source, _mime in file_specs:
        if isinstance(source, Path):
            try:
                total += int(source.stat().st_size)
            except OSError:
                continue
        else:
            total += len(source)
    return total


def _request_with_cloudflare_retry(
    indexer: IndexerDefinition,
    submission: _SubmissionRequest,
) -> Optional[requests.Response]:
    """Perform a submission, rebuilding consumed streams for each bounded retry."""
    max_retries = 3
    backoff_seconds = 15
    response: Optional[requests.Response] = None

    for attempt in range(max_retries):
        with _open_multipart_files(submission.file_specs) as files_payload:
            response = requests.request(
                method=submission.method,
                url=submission.url,
                params=submission.params or None,
                headers=submission.headers or None,
                data=submission.data or None,
                files=files_payload,
                timeout=indexer.timeout,
                verify=not submission.is_curl,
            )

        is_cloudflare_challenge = response.status_code == 403 and "just a moment" in response.text[:500].lower()
        if not is_cloudflare_challenge:
            break

        if attempt < max_retries - 1:
            wait = backoff_seconds * (2**attempt)
            logger.info(
                f"{indexer.log_name} Cloudflare challenge "
                f"(attempt {attempt + 1}/{max_retries}). Retrying in {wait}s..."
            )
            time.sleep(wait)
            continue

        logger.warning(f"{indexer.log_name} Cloudflare blocked after {max_retries} attempts")
        break

    return response


def _duplicate_bypass_name(rls_name: str) -> str:
    if "." not in rls_name:
        return f"{rls_name}.."
    return "..".join(rls_name.rsplit(".", 1))


def submit_to_indexer(
    indexer: IndexerDefinition,
    rls_name: str,
    nzb_path: Path,
    config: Any,
    cat: str = "tv",
    nfo_path: Optional[Path] = None,
    mediainfo_path: Optional[Path] = None,
    attempt_suffix: str = "",
) -> SubmitResult:
    """
    Submit an NZB to an indexer using its definition.
    Returns (ok, status, reason) where status conveys failure/success class.
    """

    # Resolve API key (YAML first, then config/env)
    api_key = resolve_indexer_api_key(indexer, config)

    if not api_key and indexer.auth.method != "none":
        reason = f"Missing required API key for indexer '{indexer.id}'"
        logger.warning(reason)
        return False, "misconfigured", reason

    # Resolve username if needed (YAML first, then config/env)
    username = resolve_indexer_username(indexer, config)
    if _requires_username(indexer) and not username:
        reason = f"Missing required username for indexer '{indexer.id}'"
        logger.warning(reason)
        return False, "misconfigured", reason

    # Prepare category
    requested_submission_key = _resolve_movie_submission_key(cat, rls_name)
    category, matched_key, direct_match = _resolve_category(indexer, requested_submission_key)
    uses_submission_category = indexer.categories.uses_submission_category()
    requested_category = CategoryMapping.normalize_key(requested_submission_key)

    if uses_submission_category and not category:
        reason = (
            f"Category mapping mismatch for indexer '{indexer.id}': "
            f"detected '{requested_submission_key}' has no valid category_id"
        )
        logger.error(reason)
        return False, "misconfigured", reason

    if uses_submission_category:
        logger.info(
            f"[CATEGORY] {(requested_category or cat).upper()} -> Indexer: {indexer.id} -> Category ID: {category}"
        )
        logger.info(
            f"Detected: {(requested_category or cat).upper()} -> category_id: {category} -> Final: {category}"
        )
        if matched_key and not direct_match:
            logger.warning(
                f"{indexer.log_name} category '{requested_category or cat}' not defined explicitly; "
                f"using '{matched_key}' mapping instead"
            )
    else:
        logger.info(f"[CATEGORY] {(requested_category or cat).upper()} -> Indexer: {indexer.id} -> Category ID: [not used]")

    submission = _build_submission_request(
        indexer=indexer,
        rls_name=rls_name,
        nzb_path=nzb_path,
        api_key=api_key,
        username=username,
        category=category,
        nfo_path=nfo_path,
        mediainfo_path=mediainfo_path,
    )

    # Standard HTTP submission
    try:
        log_verbose(
            f"Submitting to {indexer.log_name}: "
            f"{submission.submission_filename} (Cat: {category}){attempt_suffix}"
        )
        response = _request_with_cloudflare_retry(indexer, submission)

        if response is None:
            logger.error(f"{indexer.log_name} Failed to get any response from indexer.")
            return False, "network_error", "No response from indexer"

        response.raise_for_status()

        success, is_duplicate = _check_success(indexer, response)

        if is_duplicate:
            # DUPLICATE BYPASS LOGIC
            if getattr(config, "enable_duplicate_bypass", False) and "Duplicate-Bypass" not in attempt_suffix:
                new_rls_name = _duplicate_bypass_name(rls_name)
                logger.info(f"{indexer.log_name} Duplicate detected. Retrying with tweaked name: {new_rls_name}")
                return submit_to_indexer(
                    indexer=indexer,
                    rls_name=new_rls_name,
                    nzb_path=nzb_path,
                    config=config,
                    cat=cat,
                    nfo_path=nfo_path,
                    mediainfo_path=mediainfo_path,
                    attempt_suffix=" (Duplicate-Bypass)",
                )

            logger.warning(f"{indexer.log_name} Duplicate response, not marking posted: {rls_name}")
            return False, "duplicate", "Indexer reported duplicate; no confirmed new post"

        if success:
            log_success(f"{indexer.log_name} Accepted: {rls_name}")
            resp_body = redact_text(response.text[:300].replace(chr(10), " ").strip(), secrets=(api_key, username))
            logger.info(f"{indexer.log_name} Response: HTTP {response.status_code} | {resp_body}")
            return True, "success", "Indexer accepted submission"

        # Failed submission - log response for debugging
        response_secrets = (api_key, username)
        resp_trunc = redact_text(response.text[:500].replace("\n", " ").strip(), secrets=response_secrets)
        logger.warning(f"{indexer.log_name} Submission Rejected: {rls_name}")
        logger.warning(f"{indexer.log_name} Error Sample: {resp_trunc}")

        # Extra debug for "Missing parameter" errors
        if "missing parameter" in resp_trunc.lower():
            file_sizes = _estimate_payload_size(submission.file_specs)
            file_keys = [field_name for field_name, *_rest in submission.file_specs]
            logger.warning(
                f"{indexer.log_name} DEBUG: URL={redact_url(submission.url)} | "
                f"Params={redact_mapping(submission.params)} | "
                f"DataKeys={list(submission.data.keys())} | FileKeys={file_keys} | "
                f"FileSize={file_sizes} bytes"
            )

        return False, "rejected", f"Indexer rejected submission: {resp_trunc or 'unknown rejection'}"

    except requests.RequestException as e:
        # Keep the status terse so we do not leak full URLs with API keys.
        response_secrets = (api_key, username)
        if hasattr(e, "response") and e.response is not None:
            err_msg = f"HTTP {e.response.status_code} {e.response.reason or 'Error'}"
        else:
            # Connection-level errors (SSL, timeout, connection reset, etc.)
            # have no HTTP response at all. The bare exception class name
            # ("SSLError") gives no way to distinguish a cert failure from a
            # reset connection from a handshake timeout, so include the
            # exception's own message too -- redacted, since urllib3's error
            # text can embed the full request URL including the API key.
            detail = redact_text(e, secrets=response_secrets)[:300].replace("\n", " ").strip()
            err_msg = f"{type(e).__name__}: {detail}" if detail else type(e).__name__
        if hasattr(e, "response") and e.response is not None:
            body = e.response.text[:3000]
            meta = re.search(
                r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{1,300})',
                body,
                re.IGNORECASE,
            ) or re.search(
                r'<meta[^>]+content=["\']([^"\']{1,300})["\'][^>]+name=["\']description["\']',
                body,
                re.IGNORECASE,
            )
            if meta:
                resp_body = meta.group(1).strip()
            elif body.strip().startswith("<"):
                resp_body = " ".join(re.sub(r"<[^>]+>", " ", body).split())[:200]
            else:
                resp_body = body[:200].replace("\n", " ").strip()
            err_msg += f" | Body: {redact_text(resp_body, secrets=response_secrets)}"

        logger.warning(f"{indexer.log_name} submission failed: {err_msg}")

        return False, "network_error", err_msg


__all__ = [
    "IndexerDefinition",
    "IndexerRegistry",
    "get_registry",
    "reload_indexers",
    "get_indexer",
    "get_all_indexers",
    "get_available_categories",
    "get_enabled_indexers",
    "resolve_indexer_api_key",
    "resolve_indexer_backfill",
    "resolve_indexer_enabled",
    "resolve_indexer_priority",
    "resolve_indexer_username",
    "submit_to_indexer",
]
