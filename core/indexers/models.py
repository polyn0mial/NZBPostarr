"""Indexer definition models and the one submission outcome type.

Loaded from indexers/*.yaml by core.indexers.registry; no I/O here.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

from core.media import normalize_category

SubmitStatus = Literal[
    "success", "duplicate", "misconfigured", "rejected", "network_error", "error"
]


@dataclass(frozen=True, slots=True)
class SubmitResult:
    """Canonical outcome returned by every indexer submission path."""

    success: bool
    status: SubmitStatus
    reason: str


# Closed sets documented in indexers/_template.yaml. Kept here so both the
# submission HTTP method and the auth method fail fast at load time (naming
# the offending YAML file) instead of silently falling through to a no-op
# branch on a typo.
_VALID_SUBMIT_METHODS: Tuple[str, ...] = ("POST", "PUT", "CURL")
_VALID_AUTH_METHODS: Tuple[str, ...] = (
    "query_param",
    "header",
    "form_field",
    "curl_url",
    "none",
)


_YAML_CREDENTIAL_KEYS = frozenset({"api_key", "username"})

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
        cleaned = category.strip().lower()
        if cleaned in _INDEXER_CATEGORY_ALIASES:
            return _INDEXER_CATEGORY_ALIASES[cleaned]
        normalized = normalize_category(cleaned)
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
            raise ValueError(
                f"auth.method must be one of {_VALID_AUTH_METHODS}, got {value!r}"
            )
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
    """Complete definition of an indexer plugin.

    Strict: an unknown key is a load error naming the YAML file, not a silently ignored typo.
    """

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _drop_credential_placeholders(cls, data: Any) -> Any:
        """Shipped YAMLs carry blank api_key/username slots; credentials come from config only."""
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if key not in _YAML_CREDENTIAL_KEYS
            }
        return data

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
            raise ValueError(
                f"method must be one of {_VALID_SUBMIT_METHODS} (case-insensitive), got {value!r}"
            )
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
        return (
            (getter(f"{indexer.id}_api_key") or "").strip()
            or (getter(indexer.id) or "").strip()
            or None
        )

    # Fallback (dict-like configs)
    if isinstance(config, dict):
        for key in (f"{indexer.id}_api_key", indexer.id):
            val = config.get(key)
            if val:
                return str(val).strip() or None

    return None


def _resolve_indexer_bool_field(
    indexer: IndexerDefinition, config: Any, field_prefix: str, default_val: bool
) -> bool:
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
        return (
            (getter(f"{indexer.id}_username") or "").strip()
            or (getter(indexer.id) or "").strip()
            or None
        )

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
