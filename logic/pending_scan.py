"""Shared pending-scan helpers used by API, dashboard, and headless flows."""

from __future__ import annotations

import contextvars
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Set, Tuple

from core.utils import (
    AUDIOBOOK_EXTENSIONS,
    EBOOK_EXTENSIONS,
    MUSIC_EXTENSIONS,
    VIDEO_EXTENSIONS,
    has_multi_file_episode_pattern,
)

_AUTO_TV_PATTERNS = re.compile(
    r"(?:"
    r"S\d{1,2}[.\s-]?E\d{1,3}"
    r"|S\d{1,2}[.\s-]?(?:Complete|COMPLETE|Full)"
    r"|Season[.\s-]?\d{1,2}"
    r"|Series[.\s-]?\d{1,2}"
    r"|Vol(?:ume)?[.\s_-]*\d{1,3}"
    r"|(?:^|[.\s_(-])\d{1,2}x\d{2,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_-])S\d{2}(?:[.\s_-]|$)"
    r"|(?:19|20)\d{2}[.\s-]\d{2}[.\s-]\d{2}"
    r")",
    re.IGNORECASE,
)

_AUTO_MOVIE_PATTERNS = re.compile(
    r"(?:^|[.\s_(-])(?:19|20)\d{2}(?:[.\s_\])-]|$)",
    re.IGNORECASE,
)

_AUTO_YEAR_TOKEN_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_AUTO_YEAR_RANGE_RE = re.compile(r"(?:19|20)\d{2}\s*[-/]\s*(?:19|20)\d{2}")
_AUTO_SEASON_TOKEN_RE = re.compile(r"\b(?:season|series)\b", re.IGNORECASE)
_AUTO_COMPLETE_SERIES_RANGE_RE = re.compile(
    r"(?:19|20)\d{2}\s*[-/–—]\s*(?:19|20)\d{2}.*\b(?:complete|season|series|s\d{1,2}|dvd|remux)\b",
    re.IGNORECASE,
)
_TRAILING_RELEASE_GROUP_YEAR_RE = re.compile(r"[-._\s]+[A-Za-z][A-Za-z0-9]{1,20}(?:19|20)\d{2}$")
_GENERIC_TV_SEASON_FOLDER_RE = re.compile(
    r"^(?:"
    r"s\d{1,2}(?:[.\s_-]+\d{1,2})?"
    r"|season[.\s_-]?\d{1,2}(?:[.\s_-]+\d{1,2})?"
    r"|series[.\s_-]?\d{1,2}(?:[.\s_-]+\d{1,2})?"
    r"|s\d{1,2}(?:[.\s_-]?(?:part|vol|volume)[.\s_-]?\d{1,2})?"
    r"|season[.\s_-]?\d{1,2}[.\s_-]*(?:complete|full)?"
    r"|s\d{1,2}[.\s_-]*(?:complete|full)?"
    r")$",
    re.IGNORECASE,
)

_AUTO_ANIME_FANSUB_RE = re.compile(r"^\[[^\]]{1,40}\]\s*", re.IGNORECASE)
_ANIME_SEASONAL_FOLDER_RE = re.compile(r"\b(?:winter|spring|summer|fall|autumn)\s+(?:19|20)\d{2}\b", re.IGNORECASE)
_GENERIC_ANIME_FOLDER_RE = re.compile(r"\b(?:anime|seasonal)\b", re.IGNORECASE)
_EXPLICIT_EPISODE_RE = re.compile(
    r"(?:"
    r"S\d{1,2}[.\s_-]*E\d{1,3}"
    r"|(?:^|[.\s_(-])\d{1,2}x\d{2,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_(-])E\d{1,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_(-])(?:EP?|Episode)[.\s_-]?\d{1,3}(?:[.\s_)-]|$)"
    r")",
    re.IGNORECASE,
)
_ANIME_ABSOLUTE_EPISODE_RE = re.compile(
    r"(?:"
    r"(?:^|[\s._-])-\s?\d{1,3}(?:v\d+)?(?:[\s._(\[]|$)"
    r"|(?:^|[\s._-])\d{1,3}(?:v\d+)?(?:[\s._(\[]|$)"
    r"|\[\d{1,3}(?:v\d+)?\]"
    r")",
    re.IGNORECASE,
)
_ANIME_EXTRA_RE = re.compile(
    r"(?:"
    r"(?:^|[\s._-])(NCOP|NCED|OP|ED)(?:[\s._-]|$)"
    r"|(?:^|[\s._-])(preview|pv|trailer|teaser|special|specials)(?:[\s._-]|$)"
    r")",
    re.IGNORECASE,
)
_TV_EPISODE_HINT_RE = re.compile(
    r"(?:"
    r"(?:^|[.\s_-])(?:S\d{1,2}[.\s_-]*)?(?:E\d{1,3}|EP?\d{1,3}|Episode[.\s_-]?\d{1,3})(?:[.\s_([+-]|$)"
    r"|(?:^|[.\s_-])\d{1,2}x\d{1,3}(?:[.\s_)-]|$)"
    r"|(?:^|[.\s_-])(?:Part|Pt|Chapter)[.\s_-]?\d{1,3}(?:[.\s_]|$)"
    r"|(?:^|[.\s_-])(?:Pilot|Finale|Final|Special|OVA|OAD)(?:[.\s_]|$)"
    r"|(?:^|[.\s_-])(?:1of\d{1,2}|Part\d{1,2}of\d{1,2})(?:[.\s_]|$)"
    r"|(?:^|[.\s_-])(?:\d{1,3}(?:[.\s_-]\d{2,4}){1,2})(?:[.\s_]|$)"
    r")",
    re.IGNORECASE,
)

_TV_PACK_EPISODE_RE = re.compile(r"S\d{1,2}[.\s_-]*E\d{1,3}", re.IGNORECASE)
_TV_PACK_RESOLUTION_RE = re.compile(
    r"(?:^|[.\s_-])(?:2160p|1080p|1080i|720p|576p|576i|480p|480i|ntsc|pal)(?:[.\s_-]|$)",
    re.IGNORECASE,
)
_TV_PACK_SOURCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"WEB(?:[.\s_-]?DL|[.\s_-]?Rip|[.\s_-]?HD)?|WEBDL|WEBRip|WEBHD|BluRay|BDRip|BRRip|REMUX|HDRip|PDRip|HDTV|PDTV|"
    r"SDTV|TV|TVRip|SATRip|DSR|DVB|DVDRip|DVD|VHS(?:Rip)?|DV|UHD|AMZN|NF|NFLX|DSNP|PCOK|HMAX|MAX|HULU|ATVP|AUBC|iT|iP|STAN|CR|"
    r"PMTP|PMNT|CTV|CBC|BBC|PBS|TBS|TNT|NBC|ABC|CBS|FOX|HBO|SHOWTIME|SHO|MIXED"
    r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_TV_PACK_EXTRA_RE = re.compile(
    r"(?:^|[.\s_\[-])(?:sample|samples|nced|ncop|proof|screens?|subs?|subtitles?|extras?|featurettes?|trailer)(?:[.\s_\]-]|$)",
    re.IGNORECASE,
)
_TV_PACK_HARD_EXTRA_RE = re.compile(
    r"(?:^|[.\s_\[-])(?:sample|samples|nced|ncop|proof|screens?|subs?|subtitles?|featurettes?|trailer)(?:[.\s_\]-]|$)",
    re.IGNORECASE,
)
_TV_PACK_EXTRAS_WORD_RE = re.compile(r"(?:^|[.\s_\[-])extras?(?:[.\s_\]-]|$)", re.IGNORECASE)
DEFAULT_TV_PACK_IGNORE_RULES: dict[str, bool] = {
    "enabled": True,
    "ignore_non_video": True,
    "ignore_extras": True,
    "require_sxxexx": True,
    "require_resolution": False,
    "require_source": True,
}
TV_PACK_IGNORE_RULE_LABELS: dict[str, str] = {
    "ignore_non_video": "Non-video files",
    "ignore_extras": "Samples, NFO, proof, screens, subtitles, extras, featurettes, trailers, NCOP, NCED",
    "require_sxxexx": "Files without S##E## episode numbering",
    "require_resolution": "Files without quality/format such as NTSC/PAL/480i/480p/576i/576p/720p/1080p/2160p",
    "require_source": "Files without a media source token such as WEB, WEB-DL, WEBRip, BluRay, BRRip, HDRip, PDRip, REMUX, HDTV, SDTV, PDTV, TV, DVD, DVDRip, VHS",
}
TV_PACK_IGNORED_FILE_TYPES: tuple[str, ...] = (
    ".nfo",
    ".txt",
    ".srt",
    ".ass",
    ".ssa",
    ".sub",
    ".idx",
    ".sup",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".sfv",
    ".md5",
    ".par2",
    ".url",
)
TV_PACK_IGNORED_NAME_PATTERNS: tuple[str, ...] = (
    "sample",
    "samples",
    "nfo",
    "proof",
    "screen",
    "screens",
    "subs",
    "subtitles",
    "extras",
    "featurette",
    "featurettes",
    "trailer",
    "ncop",
    "nced",
)
_ANIME_LOOKUP_CLEAN_RE = re.compile(
    r"\b(?:"
    r"S\d{1,2}E\d{1,3}|S\d{1,2}|E\d{1,3}|\d{1,2}x\d{2,3}|Episode\s?\d{1,3}|Season\s?\d{1,2}|Series\s?\d{1,2}"
    r"|(?:19|20)\d{2}"
    r"|\d{3,4}p|1080i|WEB(?:-DL)?|BluRay|BDRip|BRRip|Remux|Hybrid|DVD|DVDRip|HDTV|UHD|FullBR"
    r"|HEVC|AVC|x26[45]|H\.?26[45]|10bit|AAC\d?(?:\.\d)?|DTS(?:-HD)?|FLAC|MULTI|PROPER|REPACK|LIMITED|INTERNAL"
    r")\b",
    re.IGNORECASE,
)
_SERIES_SIGNATURE_STRIP_RE = re.compile(
    r"\b(?:"
    r"S\d{1,2}[.\s_-]*E\d{1,3}"
    r"|\d{1,2}x\d{2,3}"
    r"|E\d{1,3}"
    r"|(?:19|20)\d{2}"
    r"|\d{3,4}p"
    r"|WEB(?:-DL)?|BluRay|BDRip|BRRip|Remux|Hybrid|DVD|DVDRip"
    r"|HEVC|AVC|x26[45]|H\.?26[45]|10bit|AAC\d?(?:\.\d)?|DTS(?:-HD)?|FLAC"
    r"|PROPER|REPACK|LIMITED|INTERNAL|MULTI"
    r")\b",
    re.IGNORECASE,
)

_AUTO_ROOT_CATEGORIES = {"", "external", "auto"}
_DISC_IMAGE_EXTENSIONS = {".iso", ".img", ".mdf", ".nrg"}
_DISC_STRUCTURE_EXTENSIONS = {".ifo", ".bup", ".vob", ".m2ts", ".mpls", ".clpi", ".ssif"}
_DISC_STRUCTURE_DIRS = {"bdmv", "certificate", "video_ts"}
_DISC_NAME_RE = re.compile(
    r"(?:^|[\s._\-[\]()])(?:disc|disk|bdmv|video_ts|full[.\s_-]?bd|full[.\s_-]?blu[.\s_-]?ray)(?:[\s._\-[\]()]|$)",
    re.IGNORECASE,
)
_FOLDER_CATEGORY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("audiobooks", ("audiobook", "audiobooks", "audio book", "audio books", "podcast", "podcasts", "m4b")),
    ("books", ("ebook", "ebooks", "book", "books", "comic", "comics", "manga", "pdf", "epub")),
    ("music", ("music", "album", "albums", "flac", "mp3", "discography")),
    ("apps", ("app", "apps", "game", "games", "software", "program", "programs")),
    ("anime", ("anime", "animes", "seasonal")),
    ("tv", ("tv", "show", "shows", "series", "season", "television")),
    ("movies", ("movie", "movies", "film", "films")),
)
_AUDIOBOOK_HINT_RE = re.compile(
    r"\b(?:audio[.\s_-]?books?|audiobooks?|podcasts?|m4b|lecture|lectures)\b", re.IGNORECASE
)
_EBOOK_HINT_RE = re.compile(r"\b(?:e[.\s_-]?books?|ebooks?|books?|comics?|manga|pdfs?|epubs?)\b", re.IGNORECASE)
_MUSIC_HINT_RE = re.compile(r"\b(?:music|albums?|discography|soundtracks?|ost|flac|mp3)\b", re.IGNORECASE)


@dataclass(frozen=True)
class ScanPathItem:
    """Normalized pending-scan item for category-aware directory traversals."""

    path: Path
    name: str
    rel_key: str
    is_episode: bool


@dataclass(frozen=True)
class PendingScanItem:
    """Canonical top-level scan result shared by queueing, monitoring, and dashboard flows."""

    category: str
    configured_category: str
    folder: Path
    path: Path
    name: str
    rel_key: str
    is_dir: bool
    episode_paths: Tuple[Path, ...] = ()
    episode_rel_keys: Tuple[str, ...] = ()


@dataclass(frozen=True)
class IgnoredScanPath:
    """A discovered path that was intentionally excluded from processing."""

    path: Path
    reason: str


@dataclass(frozen=True)
class ExplicitPathResolution:
    """Resolved category/type metadata for one explicit processing path."""

    source_path: Path
    category: str
    itype: str
    detection_method: str
    queue_paths: Tuple[Path, ...]
    ignored_paths: Tuple[IgnoredScanPath, ...] = ()
    content_flags: Tuple[str, ...] = ()
    override_note: str = ""

    def __post_init__(self) -> None:
        queue_paths = self.queue_paths
        if isinstance(queue_paths, Path):
            object.__setattr__(self, "queue_paths", (queue_paths,))
        elif not isinstance(queue_paths, tuple):
            try:
                object.__setattr__(self, "queue_paths", tuple(queue_paths))
            except TypeError:
                object.__setattr__(self, "queue_paths", ())

        ignored_paths = self.ignored_paths
        if isinstance(ignored_paths, IgnoredScanPath):
            object.__setattr__(self, "ignored_paths", (ignored_paths,))
        elif ignored_paths is None:
            object.__setattr__(self, "ignored_paths", ())
        elif not isinstance(ignored_paths, tuple):
            try:
                object.__setattr__(self, "ignored_paths", tuple(ignored_paths))
            except TypeError:
                object.__setattr__(self, "ignored_paths", ())


def get_configured_category_folders(
    conf: Any,
    *,
    filter_category: Optional[str] = None,
    include_external: bool = False,
    must_exist: bool = False,
) -> List[Tuple[str, Path]]:
    """Return configured folders as generic external scan roots."""
    categories: List[Tuple[str, Path]] = []
    seen: Set[tuple[str, str]] = set()
    wanted = str(filter_category or "").strip().lower() or None
    if wanted == "auto":
        wanted = "external"

    get_folder_path_entries = getattr(conf, "get_folder_path_entries", None)
    folder_entries = (
        get_folder_path_entries() if callable(get_folder_path_entries) else getattr(conf, "folder_paths", [])
    )
    saw_folder_entry = False

    for fp in folder_entries:
        if isinstance(fp, str):
            fp = {"path": fp}
        if not isinstance(fp, dict):
            continue
        saw_folder_entry = True

        raw_category = str(fp.get("category", "") or "").strip().lower()
        category = "external" if raw_category in _AUTO_ROOT_CATEGORIES else raw_category
        path_str = str(fp.get("path", "") or "").strip()
        if not category or not path_str:
            continue
        if not include_external and category == "external":
            continue
        if wanted and category != wanted:
            continue

        folder = Path(path_str)
        if must_exist and not folder.exists():
            continue
        key = (category, str(folder))
        if key in seen:
            continue
        seen.add(key)
        categories.append((category, folder))

    if categories or saw_folder_entry:
        return categories

    legacy_fields = ("movies_folder", "tv_folder", "misc_folder")
    for attr_name in legacy_fields:
        if wanted and wanted != "external":
            continue
        folder = getattr(conf, attr_name, None)
        if not folder:
            continue
        folder_path = Path(folder)
        if must_exist and not folder_path.exists():
            continue
        key = ("external", str(folder_path))
        if key in seen:
            continue
        seen.add(key)
        if include_external:
            categories.append(("external", folder_path))

    if include_external and (wanted in (None, "external")):
        raw_external = getattr(conf, "external_folders", None)
        if isinstance(raw_external, (list, tuple)):
            external_values = list(raw_external)
        elif raw_external:
            external_values = [raw_external]
        else:
            single_external = getattr(conf, "external_folder", None)
            external_values = [single_external] if single_external else []

        for folder in external_values:
            if not folder:
                continue
            folder_path = Path(folder)
            if must_exist and not folder_path.exists():
                continue
            key = ("external", str(folder_path))
            if key in seen:
                continue
            seen.add(key)
            categories.append(("external", folder_path))

    return categories


def get_configured_folders(conf: Any, *, must_exist: bool = False) -> List[Path]:
    """Return all configured folder paths regardless of their legacy category."""
    folders: List[Path] = []
    seen: Set[str] = set()
    for _category, folder in get_configured_category_folders(conf, include_external=True, must_exist=must_exist):
        key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        folders.append(folder)

    return folders


def looks_like_tv_name(name: str) -> bool:
    """Return True when a release name clearly looks episodic/TV-like."""
    if _AUTO_COMPLETE_SERIES_RANGE_RE.search(name):
        return True
    if has_clear_movie_year(name):
        return False
    return bool(_AUTO_TV_PATTERNS.search(name))


def _has_tv_context(entry: Path) -> bool:
    """Return True when an entry sits under a TV-like ancestor path."""
    try:
        parents = list(entry.parents)
    except TypeError:
        return False

    for ancestor in parents[:4]:
        normalized = re.sub(r"[^a-z0-9]+", " ", ancestor.stem.lower()).strip()
        if not normalized:
            continue
        if looks_like_tv_name(ancestor.name):
            return True
    return False


def looks_like_generic_tv_season_folder(name: str) -> bool:
    """Return True for season-only folder names like ``Season 1`` or ``S01``."""
    stem = re.sub(r"[^a-z0-9]+", " ", Path(str(name)).stem.lower()).strip()
    if not stem:
        return False

    if _GENERIC_TV_SEASON_FOLDER_RE.fullmatch(stem):
        return True

    cleaned = re.sub(r"\b(?:season|series|complete|full|tv|pack|folder)\b", " ", stem)
    cleaned = re.sub(r"\b(?:s\d{1,2}|season\d{1,2}|series\d{1,2}|\d{1,2}x\d{2,3}|\d{1,2})\b", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return not bool(re.search(r"[a-z]", cleaned))


def _is_multi_season_container_dir(entry: Path) -> bool:
    """True when a folder is mainly a container of season-like subfolders."""
    if not entry.is_dir():
        return False
    try:
        child_dirs = [child for child in entry.iterdir() if child.is_dir() and not child.name.startswith(".")]
    except OSError:
        return False
    if len(child_dirs) < 2:
        return False

    season_like_count = 0
    for child in child_dirs:
        name = child.name
        if looks_like_generic_tv_season_folder(name):
            season_like_count += 1
            continue
        if re.search(r"(?:^|[.\s_-])S\d{1,2}(?:[.\s_-]\d{1,2})?(?:$|[.\s_-])", name, re.IGNORECASE):
            season_like_count += 1
            continue
    return season_like_count >= 2


def has_clear_movie_year(name: str) -> bool:
    """Return True when a release name has exactly one plausible movie year token."""
    comparable_name = Path(str(name)).stem
    comparable_name = _TRAILING_RELEASE_GROUP_YEAR_RE.sub("", comparable_name)
    if not _AUTO_MOVIE_PATTERNS.search(comparable_name):
        return False
    if _AUTO_YEAR_RANGE_RE.search(comparable_name):
        return False
    return len(set(_AUTO_YEAR_TOKEN_RE.findall(comparable_name))) == 1


def classify_video_name(
    name: str,
    folder_category: str = "",
    assume_movie_if_unknown: bool = True,
    *,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
) -> str:
    """Classify a video release name as TV Show, Anime, Movie, or Misc."""
    folder_hint = str(folder_category or "").strip().lower()
    anime_status: Optional[bool] = None
    if anime_lookup is not None:
        try:
            anime_status = anime_lookup(_normalize_lookup_title(name) or name)
        except Exception:
            anime_status = None

    if anime_status is True:
        return "Anime"
    if anime_status is None and folder_hint == "anime":
        return "Anime"
    if folder_hint in {"movie", "movies"} and not _matches_episode_pattern(name, anime_mode=True):
        return "Movie"
    if _matches_episode_pattern(name):
        return "TV Show"
    if has_clear_movie_year(name):
        return "Movie"
    if looks_like_tv_name(name):
        return "TV Show"
    if _TV_EPISODE_HINT_RE.search(Path(str(name)).stem):
        return "TV Show"
    return "Movie" if assume_movie_if_unknown else "Misc"


def infer_folder_category_hint(folder: Path | str | None) -> str:
    """Infer a weak category bias from a configured root folder name."""
    if not folder:
        return ""

    try:
        parts = Path(str(folder)).parts[-3:]
    except (TypeError, ValueError):
        parts = ()

    for raw_part in reversed(parts):
        normalized = re.sub(r"[^a-z0-9]+", " ", str(raw_part).lower()).strip()
        if not normalized:
            continue
        tokens = set(normalized.split())
        for category, keywords in _FOLDER_CATEGORY_HINTS:
            if any(
                keyword in tokens
                or bool(re.search(rf"(?:^|\s){re.escape(keyword)}(?:$|\s)", normalized))
                for keyword in keywords
            ):
                return category
        if _ANIME_SEASONAL_FOLDER_RE.search(normalized):
            return "anime"
    return ""


def _coerce_category_hint(raw: str) -> str:
    normalized = str(raw or "").strip().lower()
    return {
        "movie": "movies",
        "movies": "movies",
        "tv": "tv",
        "anime": "anime",
        "books": "books",
        "ebooks": "books",
        "ebook": "books",
        "audiobook": "audiobooks",
        "audiobooks": "audiobooks",
        "music": "music",
        "apps": "apps",
    }.get(normalized, "")


def _hint_category_from_itype(raw: str) -> str:
    normalized = str(raw or "").strip().lower()
    return {
        "movie": "movies",
        "movies": "movies",
        "tv show": "tv",
        "tv episode": "tv",
        "anime": "anime",
        "music": "music",
        "ebook": "books",
        "audiobook": "audiobooks",
        "app": "apps",
    }.get(normalized, "")


def _entry_hint_text(entry: Path) -> str:
    try:
        parts = entry.parts[-6:]
    except (TypeError, ValueError):
        parts = (str(entry),)
    return " ".join(str(part) for part in parts)


def _non_video_media_category(entry: Path, leaf_files: Optional[Tuple[Path, ...]] = None) -> str:
    """Return a specific non-video media category when the content is clear."""
    leaves = leaf_files if leaf_files is not None else _iter_leaf_files(entry)
    if not leaves:
        return ""

    ebook_count = 0
    audiobook_count = 0
    music_count = 0
    app_count = 0
    video_count = 0
    known_count = 0
    for path in leaves:
        ext = path.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            video_count += 1
            known_count += 1
        elif ext in EBOOK_EXTENSIONS:
            ebook_count += 1
            known_count += 1
        elif ext in AUDIOBOOK_EXTENSIONS:
            audiobook_count += 1
            known_count += 1
        elif ext in MUSIC_EXTENSIONS:
            music_count += 1
            known_count += 1
        elif ext in {".exe", ".msi", ".apk", ".dmg", ".pkg", ".deb", ".rpm", ".zip", ".rar", ".7z"}:
            app_count += 1
            known_count += 1

    if video_count:
        return ""

    hint_text = _entry_hint_text(entry)
    audio_count = audiobook_count + music_count
    if audio_count:
        if audiobook_count or _AUDIOBOOK_HINT_RE.search(hint_text):
            return "audiobooks"
        if _MUSIC_HINT_RE.search(hint_text) or music_count >= max(1, ebook_count):
            return "music"

    if ebook_count:
        if _EBOOK_HINT_RE.search(hint_text) or ebook_count >= max(1, audio_count):
            return "books"

    if app_count and known_count and app_count / known_count >= 0.5:
        return "apps"

    return ""


def infer_entry_category_hint(entry: Path, explicit_hint: str = "", itype_hint: str = "") -> str:
    """Infer the weakest-possible category hint from path structure and UI metadata."""
    for hinted in (_hint_category_from_itype(itype_hint), _coerce_category_hint(explicit_hint)):
        if hinted:
            return hinted

    parts = []
    try:
        base = entry.parent
        parts = list(base.parts[-5:])
    except OSError:
        parts = []

    for raw_part in reversed(parts):
        hinted = infer_folder_category_hint(raw_part)
        if hinted:
            return hinted
    return ""


def _default_cached_anime_lookup(name: str) -> Optional[bool]:
    try:
        from logic.anime_cache import get_cached as anime_cached

        return anime_cached(name)
    except Exception:
        return None


# Per-scan cache for filesystem walks. ``resolve_explicit_path`` and
# ``detect_content_itype`` each walk the same tree multiple times; with
# four-to-five top-level entries × N episodes that is the dominant cost
# of building the pending snapshot. The cache lives only for the
# duration of a scan -- ``begin_scan_cache()`` resets it at the start of
# every snapshot build so we never leak stale data across requests.
_SCAN_CACHE_VAR: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_pending_scan_walk_cache",
    default=None,
)


def begin_scan_cache() -> contextvars.Token:
    """Start a fresh per-scan walk cache. Caller must release the token."""
    return _SCAN_CACHE_VAR.set({})


def end_scan_cache(token: contextvars.Token) -> None:
    """Tear down the per-scan walk cache."""
    try:
        _SCAN_CACHE_VAR.reset(token)
    except (LookupError, ValueError):
        pass


def _scan_cache_get(kind: str, entry: Path) -> Optional[Tuple[Path, ...]]:
    cache = _SCAN_CACHE_VAR.get()
    if cache is None:
        return None
    return cache.get((kind, str(entry)))


def _scan_cache_put(kind: str, entry: Path, value: Tuple[Path, ...]) -> Tuple[Path, ...]:
    cache = _SCAN_CACHE_VAR.get()
    if cache is not None:
        cache[(kind, str(entry))] = value
    return value


def _iter_video_candidates(entry: Path, video_extensions: Set[str]) -> Tuple[Path, ...]:
    if entry.is_file():
        return (entry,) if entry.suffix.lower() in video_extensions else ()

    cached = _scan_cache_get("video", entry)
    if cached is not None:
        return cached

    videos: list[Path] = []
    for root, dirs, files in os.walk(str(entry)):
        dirs[:] = sorted(dirname for dirname in dirs if not dirname.startswith("."))
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            candidate = Path(root) / filename
            if candidate.suffix.lower() in video_extensions:
                videos.append(candidate)
    return _scan_cache_put("video", entry, tuple(videos))


def _iter_leaf_files(entry: Path) -> Tuple[Path, ...]:
    if entry.is_file():
        return (entry,)

    cached = _scan_cache_get("leaf", entry)
    if cached is not None:
        return cached

    files: list[Path] = []
    for root, dirs, filenames in os.walk(str(entry)):
        dirs[:] = sorted(dirname for dirname in dirs if not dirname.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            files.append(Path(root) / filename)
    return _scan_cache_put("leaf", entry, tuple(files))


def _matches_episode_pattern(name: str, *, anime_mode: bool = False) -> bool:
    stem = Path(name).stem
    if _EXPLICIT_EPISODE_RE.search(stem):
        return True
    if not anime_mode:
        return False
    if _ANIME_EXTRA_RE.search(stem):
        return False
    return bool(_ANIME_ABSOLUTE_EPISODE_RE.search(stem))


def _is_non_episode_anime_extra(name: str) -> bool:
    stem = Path(name).stem
    return bool(_ANIME_EXTRA_RE.search(stem)) and not _EXPLICIT_EPISODE_RE.search(stem)


def _looks_like_tv_episode_name(name: str, *, anime_mode: bool = False) -> bool:
    stem = Path(str(name)).stem
    if _matches_episode_pattern(name, anime_mode=anime_mode):
        return True
    if has_clear_movie_year(stem):
        return False
    if looks_like_tv_name(stem):
        return True
    return bool(_TV_EPISODE_HINT_RE.search(stem))


def _looks_like_source_bearing_tv_episode(name: str) -> bool:
    text = str(name or "")
    return bool(_TV_PACK_SOURCE_RE.search(text) and (_looks_like_tv_episode_name(text) or looks_like_tv_name(text)))


def _is_tv_pack_extra_name(name: str) -> bool:
    """True for sidecar/extras names, false for episode titles like S03E08.The.Extras.WEB-DL."""
    text = str(name or "")
    stem = Path(text).stem
    if _TV_PACK_HARD_EXTRA_RE.search(text):
        return True
    if _TV_PACK_EXTRAS_WORD_RE.search(text):
        # Keep legitimate episode titles such as "...S03E08.The.Extras....WEB-DL..."
        # from being treated as sidecar extras.
        if (
            _TV_PACK_EPISODE_RE.search(text) or _looks_like_tv_episode_name(text, anime_mode=True)
        ) and _TV_PACK_SOURCE_RE.search(text):
            return False
        return not bool(_TV_PACK_EPISODE_RE.search(stem) and _TV_PACK_SOURCE_RE.search(stem))
    return False


def _source_less_video_category(
    name: str, entry_path: Path, lookup: Callable[[str], Optional[bool]]
) -> tuple[str, str]:
    """Classify a video filename that does not contain an explicit source token."""
    try:
        anime_status = _lookup_anime_status(entry_path, _iter_video_candidates(entry_path, VIDEO_EXTENSIONS), lookup)
    except Exception:
        anime_status = None
    if anime_status is True:
        return "anime", "Anime"

    if has_clear_movie_year(name):
        return "movies", "Movie"

    if _looks_like_tv_episode_name(name) or looks_like_tv_name(name) or _has_tv_context(entry_path):
        return "tv", "TV Episode"

    return "misc", "Misc"


def _tv_pack_episode_rejection_reason(path: Path, video_extensions: Set[str]) -> str:
    """Return a reason when a TV season-pack leaf should not be uploaded."""
    rules = dict(DEFAULT_TV_PACK_IGNORE_RULES)
    try:
        from core.config import get_config

        configured = getattr(get_config(), "tv_pack_ignore", {}) or {}
        if isinstance(configured, dict):
            rules.update({key: bool(value) for key, value in configured.items() if key in rules})
    except Exception:
        pass

    if not rules.get("enabled", True):
        return ""

    name = path.name
    stem = path.stem
    has_sxxexx_pattern = bool(_TV_PACK_EPISODE_RE.search(stem))
    has_episode_pattern = bool(has_sxxexx_pattern or _looks_like_tv_episode_name(stem, anime_mode=True))
    has_source_token = bool(_TV_PACK_SOURCE_RE.search(stem))
    if rules.get("ignore_non_video", True) and path.suffix.lower() not in video_extensions:
        return "TV season pack extra/non-video content"
    # Allow legit episodic releases whose episode title includes words like "Extras".
    if (
        rules.get("ignore_extras", True)
        and _is_tv_pack_extra_name(name)
        and not (has_episode_pattern and has_source_token)
    ):
        return "TV season pack extra/sample"
    if rules.get("require_source", True) and not has_source_token:
        return "TV episode missing media source token"
    # Resolution is descriptive metadata, not evidence that an upload should
    # be ignored. Valid SD and source-native releases may omit this token.
    if rules.get("require_sxxexx", True) and not has_sxxexx_pattern:
        return "No S##E## episode pattern"
    return ""


def _split_tv_video_files(
    video_files: Tuple[Path, ...],
    video_extensions: Set[str],
) -> tuple[Tuple[Path, ...], Tuple[IgnoredScanPath, ...]]:
    """Split TV-like video leaves into source-bearing queue items and ignored items."""
    queue_paths: list[Path] = []
    ignored_paths: list[IgnoredScanPath] = []
    for path in video_files:
        reason = _tv_pack_episode_rejection_reason(path, video_extensions)
        if reason:
            ignored_paths.append(IgnoredScanPath(path=path, reason=reason))
        else:
            queue_paths.append(path)
    return tuple(queue_paths), tuple(ignored_paths)


def _series_signature(name: str) -> str:
    stem = Path(name).stem
    stem = _AUTO_ANIME_FANSUB_RE.sub("", stem)
    metadata_match = _SERIES_SIGNATURE_STRIP_RE.search(stem)
    if metadata_match:
        stem = stem[: metadata_match.start()]
    decomposed = unicodedata.normalize("NFKD", stem)
    stem = "".join(char for char in decomposed if not unicodedata.combining(char))
    stem = re.sub(r"[^a-zA-Z0-9]+", " ", stem)
    stem = re.sub(r"\b\d{1,3}(?:v\d+)?\b", " ", stem)
    cleaned = re.sub(r"\s+", " ", stem).strip().lower()
    if not cleaned:
        return ""
    return " ".join(cleaned.split()[:5])


def _normalize_lookup_title(value: str) -> str:
    stem = Path(str(value or "")).stem
    stem = _AUTO_ANIME_FANSUB_RE.sub("", stem)
    metadata_match = _ANIME_LOOKUP_CLEAN_RE.search(stem)
    if metadata_match:
        stem = stem[: metadata_match.start()]
    stem = re.sub(r"\b\d{1,3}(?:v\d+)?\b", " ", stem)
    decomposed = unicodedata.normalize("NFKD", stem)
    stem = "".join(char for char in decomposed if not unicodedata.combining(char))
    stem = re.sub(r"[^a-zA-Z0-9]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", stem).strip().lower()
    return cleaned


def _has_related_video_files(video_names: list[str]) -> bool:
    if len(video_names) < 2:
        return False
    if has_multi_file_episode_pattern(video_names):
        return True

    signatures: dict[str, int] = {}
    for name in video_names:
        signature = _series_signature(name)
        if len(signature) < 4:
            continue
        signatures[signature] = signatures.get(signature, 0) + 1
        if signatures[signature] >= 2:
            return True
    return False


def _has_tv_episode_like_video(video_names: list[str]) -> bool:
    """Return True if any single video leaf already looks like a TV episode."""
    return any(_looks_like_tv_episode_name(name) or _matches_episode_pattern(name) for name in video_names)


def _best_series_lookup_name(video_files: Tuple[Path, ...]) -> str:
    signatures: dict[str, int] = {}
    for path in video_files:
        signature = _series_signature(path.name)
        if len(signature) < 4:
            continue
        signatures[signature] = signatures.get(signature, 0) + 1
    if not signatures:
        return ""
    return max(signatures.items(), key=lambda item: (item[1], len(item[0])))[0]


def _anime_lookup_candidates(entry: Path, video_files: Tuple[Path, ...]) -> Tuple[str, ...]:
    candidates: list[str] = []

    def add(value: str) -> None:
        text = _normalize_lookup_title(str(value or ""))
        if not text:
            return
        for variant in (text, text.title()):
            if variant and variant not in candidates:
                candidates.append(variant)

    if entry.is_file():
        add(entry.name)
        add(entry.stem)
    else:
        folder_name = entry.name
        add(folder_name)
        if re.fullmatch(r"(?:S\d{1,2}|Season[ ._-]?\d{1,2}|Series[ ._-]?\d{1,2})", folder_name, re.IGNORECASE):
            add(entry.parent.name)
        if _ANIME_SEASONAL_FOLDER_RE.search(folder_name) or _GENERIC_ANIME_FOLDER_RE.search(folder_name):
            add(entry.parent.name)

    series_name = _best_series_lookup_name(video_files)
    if series_name:
        add(series_name)

    return tuple(candidates)


def anime_lookup_candidates(
    entry: Path,
    *,
    video_extensions: Optional[Set[str]] = None,
) -> Tuple[str, ...]:
    """Return normalized detector queries derived from a release and its video leaves."""
    video_files = _iter_video_candidates(entry, video_extensions or VIDEO_EXTENSIONS)
    if not video_files:
        return ()
    return _anime_lookup_candidates(entry, video_files)


def _lookup_anime_status(
    entry: Path,
    video_files: Tuple[Path, ...],
    lookup: Callable[[str], Optional[bool]],
) -> Optional[bool]:
    saw_false = False
    for candidate in _anime_lookup_candidates(entry, video_files):
        try:
            status = lookup(candidate)
        except Exception:
            continue
        if status is True:
            return True
        if status is False:
            saw_false = True
    return False if saw_false else None


def _detect_disc_leaf_files(entry: Path) -> Tuple[Path, ...]:
    leaf_files = _iter_leaf_files(entry)
    if not leaf_files:
        return ()

    for path in leaf_files:
        if path.suffix.lower() in _DISC_IMAGE_EXTENSIONS | _DISC_STRUCTURE_EXTENSIONS:
            return leaf_files
        if any(part.lower() in _DISC_STRUCTURE_DIRS for part in path.parts):
            return leaf_files
    return ()


def _disc_base_category(entry: Path, folder_hint: str, anime_status: Optional[bool]) -> tuple[str, str]:
    if anime_status is True:
        return "anime", "Anime"

    normalized_hint = str(folder_hint or "").strip().lower()
    if normalized_hint in {"tv", "television", "series", "shows", "show"}:
        return "tv", "TV Show"
    if looks_like_generic_tv_season_folder(entry.name) or looks_like_tv_name(entry.name):
        return "tv", "TV Show"
    return "movies", "Movie"


def _movie_fallback_itype(category: str) -> str:
    return {
        "movies": "Movie",
        "tv": "TV Show",
        "anime": "Anime",
        "disc": "DISC",
        "music": "Music",
        "audiobooks": "Audiobook",
        "books": "Ebook",
        "apps": "App",
    }.get(category, "Misc")


def _needs_source_token(category: str, itype: str, name: str) -> bool:
    """Return True when a path should be rejected for lacking a media source token."""
    normalized_category = str(category or "").strip().lower()
    normalized_itype = str(itype or "").strip().lower()
    name_text = str(name or "")

    if normalized_category in {"music", "audiobooks", "books", "ebooks", "disc"}:
        return False
    if normalized_itype in {"music", "audiobook", "ebook", "disc"}:
        return False
    if normalized_category in {"movie", "movies"} or normalized_itype == "movie":
        return False
    if has_clear_movie_year(name_text):
        return False
    if normalized_category in {"tv", "anime"}:
        return True
    if normalized_itype in {"tv show", "tv episode", "anime"}:
        return True
    return bool(_looks_like_tv_episode_name(name_text) or looks_like_tv_name(name_text))


def _resolve_episode_queue_paths(
    entry: Path,
    *,
    anime_mode: bool,
    video_extensions: Set[str],
) -> tuple[Tuple[Path, ...], Tuple[IgnoredScanPath, ...]]:
    all_files = _iter_leaf_files(entry)
    queue_paths: list[Path] = []
    ignored_paths: list[IgnoredScanPath] = []

    for path in all_files:
        if path.suffix.lower() in video_extensions and _matches_episode_pattern(path.name, anime_mode=anime_mode):
            queue_paths.append(path)
            continue
        ignored_paths.append(IgnoredScanPath(path=path, reason="No episode pattern"))

    return tuple(queue_paths), tuple(ignored_paths)


def _resolve_series_queue_paths(
    entry: Path,
    *,
    video_files: Tuple[Path, ...],
    episode_files: Tuple[Path, ...],
    all_leaf_files: Optional[Tuple[Path, ...]] = None,
    strict_tv_pack: bool = False,
    video_extensions: Optional[Set[str]] = None,
) -> tuple[Tuple[Path, ...], Tuple[IgnoredScanPath, ...]]:
    all_files = all_leaf_files if all_leaf_files is not None else _iter_leaf_files(entry)
    if strict_tv_pack:
        video_exts = video_extensions or VIDEO_EXTENSIONS
        queue_paths: list[Path] = []
        ignored_paths: list[IgnoredScanPath] = []
        for path in all_files:
            reason = _tv_pack_episode_rejection_reason(path, video_exts)
            if reason:
                ignored_paths.append(IgnoredScanPath(path=path, reason=reason))
            else:
                queue_paths.append(path)
        return tuple(queue_paths), tuple(ignored_paths)

    if episode_files:
        queue_paths = []
        ignored_paths = []
        video_exts = video_extensions or VIDEO_EXTENSIONS
        for path in all_files:
            if path.suffix.lower() in video_exts:
                reason = _tv_pack_episode_rejection_reason(path, video_exts)
                if reason:
                    ignored_paths.append(IgnoredScanPath(path=path, reason=reason))
                else:
                    queue_paths.append(path)
                continue
            ignored_paths.append(IgnoredScanPath(path=path, reason="TV season pack extra/non-video content"))
        return tuple(queue_paths), tuple(ignored_paths)
    elif _has_related_video_files([path.name for path in video_files]) or video_files:
        # A TV-like tree that does not expose explicit episode matches should still
        # be judged file-by-file instead of promoting every video leaf blindly.
        # This keeps source-bearing episodes selectable while leaving source-less
        # or malformed files ignored.
        queue_paths = []
        ignored_paths = []
        for path in video_files:
            reason = _tv_pack_episode_rejection_reason(path, video_extensions or VIDEO_EXTENSIONS)
            if reason:
                ignored_paths.append(IgnoredScanPath(path=path, reason=reason))
            else:
                queue_paths.append(path)
        valid = set(queue_paths)
    else:
        return (), ()

    ignored = tuple(IgnoredScanPath(path=path, reason="No episode pattern") for path in all_files if path not in valid)
    return tuple(queue_paths), ignored


def _resolve_early_explicit_path(
    entry: Path,
    *,
    category_hint: str,
    itype_hint: str,
    folder_hint: str,
    all_leaf_files: Tuple[Path, ...],
    video_extensions: Set[str],
    anime_lookup: Callable[[str], Optional[bool]],
) -> Optional[ExplicitPathResolution]:
    """Resolve extension-first and clearly non-video cases before video classification."""
    if entry.is_file() and entry.suffix.lower() in EBOOK_EXTENSIONS:
        override = ""
        if folder_hint and folder_hint not in {"books", "ebooks"}:
            override = f"EPUB extension overrode {folder_hint.upper()} hint"
        return ExplicitPathResolution(
            source_path=entry,
            category="books",
            itype="Ebook",
            detection_method="File extension",
            queue_paths=(entry,),
            override_note=override,
        )

    non_video_category = _non_video_media_category(entry, all_leaf_files)
    if non_video_category:
        non_video_exts = {
            "books": EBOOK_EXTENSIONS,
            "audiobooks": AUDIOBOOK_EXTENSIONS | MUSIC_EXTENSIONS,
            "music": MUSIC_EXTENSIONS | AUDIOBOOK_EXTENSIONS,
            "apps": {".exe", ".msi", ".apk", ".dmg", ".pkg", ".deb", ".rpm", ".zip", ".rar", ".7z"},
        }.get(non_video_category, set())
        queue_paths = tuple(path for path in all_leaf_files if path.suffix.lower() in non_video_exts)
        non_video_label = "ebook" if non_video_category == "books" else non_video_category.rstrip("s")
        queued = set(queue_paths)
        ignored = tuple(
            IgnoredScanPath(path=path, reason=f"Non-{non_video_label} folder content")
            for path in all_leaf_files
            if path not in queued
        )
        if entry.is_file():
            queue_paths = (entry,)
            ignored = ()
        override = ""
        if folder_hint and _coerce_category_hint(folder_hint) not in {non_video_category, "books"}:
            override = f"{non_video_category.title()} content overrode {folder_hint.upper()} hint"
        return ExplicitPathResolution(
            source_path=entry,
            category=non_video_category,
            itype=_movie_fallback_itype(non_video_category),
            detection_method="File scan",
            queue_paths=queue_paths,
            ignored_paths=ignored,
            override_note=override,
        )

    if (
        entry.is_file()
        and entry.suffix.lower() in video_extensions
        and not _TV_PACK_SOURCE_RE.search(entry.stem)
        and _needs_source_token(category_hint, itype_hint, entry.name)
    ):
        source_less_category, source_less_itype = _source_less_video_category(entry.name, entry, anime_lookup)
        return ExplicitPathResolution(
            source_path=entry,
            category=source_less_category,
            itype=source_less_itype,
            detection_method="File scan",
            queue_paths=(),
            ignored_paths=(IgnoredScanPath(path=entry, reason="Missing media source token"),),
        )

    ebook_files = tuple(path for path in all_leaf_files if path.suffix.lower() in EBOOK_EXTENSIONS)
    if entry.is_dir() and ebook_files:
        ignored = tuple(
            IgnoredScanPath(path=path, reason="Non-ebook folder content")
            for path in all_leaf_files
            if path not in ebook_files
        )
        override = ""
        if folder_hint and folder_hint not in {"books", "ebooks"}:
            override = f"EPUB scan overrode {folder_hint.upper()} hint"
        return ExplicitPathResolution(
            source_path=entry,
            category="books",
            itype="Ebook",
            detection_method="File scan",
            queue_paths=ebook_files,
            ignored_paths=ignored,
            override_note=override,
        )

    return None


@dataclass(frozen=True)
class _ExplicitVideoState:
    entry: Path
    category_hint: str
    itype_hint: str
    folder_hint: str
    video_extensions: Set[str]
    anime_lookup: Callable[[str], Optional[bool]]
    all_leaf_files: Tuple[Path, ...]
    leaf_video_files: Tuple[Path, ...]
    video_files: Tuple[Path, ...]
    episode_queue_paths: Tuple[Path, ...]
    non_episode_files: Tuple[IgnoredScanPath, ...]
    anime_queue_paths: Tuple[Path, ...]
    anime_ignored: Tuple[IgnoredScanPath, ...]
    anime_relaxed_queue_paths: Tuple[Path, ...]
    anime_relaxed_ignored: Tuple[IgnoredScanPath, ...]
    anime_episode_files: Tuple[Path, ...]
    strict_tv_pack: bool
    series_like: bool


def _build_explicit_video_state(
    entry: Path,
    *,
    category_hint: str,
    itype_hint: str,
    folder_hint: str,
    video_extensions: Set[str],
    anime_lookup: Callable[[str], Optional[bool]],
    all_leaf_files: Tuple[Path, ...],
) -> _ExplicitVideoState:
    leaf_video_files = tuple(path for path in all_leaf_files if path.suffix.lower() in video_extensions)
    video_files = _iter_video_candidates(entry, video_extensions)
    video_names = [path.name for path in video_files]
    episodic_files = tuple(path for path in video_files if _matches_episode_pattern(path.name))
    anime_episode_files = tuple(
        path for path in video_files if _matches_episode_pattern(path.name, anime_mode=True)
    )
    strict_tv_pack = bool(
        entry.is_dir()
        and video_files
        and (
            looks_like_tv_name(entry.name)
            or folder_hint == "tv"
            or _coerce_category_hint(category_hint) == "tv"
            or _hint_category_from_itype(itype_hint) == "tv"
        )
    )
    episode_queue_paths, non_episode_files = _resolve_series_queue_paths(
        entry,
        video_files=video_files,
        episode_files=episodic_files,
        all_leaf_files=all_leaf_files,
        strict_tv_pack=strict_tv_pack,
        video_extensions=video_extensions,
    )
    anime_queue_paths, anime_ignored = _resolve_series_queue_paths(
        entry,
        video_files=video_files,
        episode_files=anime_episode_files,
        all_leaf_files=all_leaf_files,
    )
    anime_relaxed_queue_paths, anime_relaxed_ignored = (
        _resolve_episode_queue_paths(entry, anime_mode=True, video_extensions=video_extensions)
        if entry.is_dir()
        else (anime_episode_files, anime_ignored)
    )
    tv_context = _has_tv_context(entry)
    series_like = bool(
        _matches_episode_pattern(entry.name)
        or episodic_files
        or _has_related_video_files(video_names)
        or (entry.suffix.lower() in video_extensions and (looks_like_tv_name(entry.name) or tv_context))
        or (
            entry.is_dir()
            and bool(video_files or leaf_video_files)
            and (looks_like_tv_name(entry.name) or tv_context)
        )
    )
    return _ExplicitVideoState(
        entry=entry,
        category_hint=category_hint,
        itype_hint=itype_hint,
        folder_hint=folder_hint,
        video_extensions=video_extensions,
        anime_lookup=anime_lookup,
        all_leaf_files=all_leaf_files,
        leaf_video_files=leaf_video_files,
        video_files=video_files,
        episode_queue_paths=episode_queue_paths,
        non_episode_files=non_episode_files,
        anime_queue_paths=anime_queue_paths,
        anime_ignored=anime_ignored,
        anime_relaxed_queue_paths=anime_relaxed_queue_paths,
        anime_relaxed_ignored=anime_relaxed_ignored,
        anime_episode_files=anime_episode_files,
        strict_tv_pack=strict_tv_pack,
        series_like=series_like,
    )


def _explicit_anime_status(state: _ExplicitVideoState) -> Optional[bool]:
    return _lookup_anime_status(
        state.entry,
        state.video_files,
        state.anime_lookup,
    )


def _explicit_is_anime(state: _ExplicitVideoState) -> bool:
    """Use detector evidence first and a configured anime hint only when unknown."""
    anime_status = _explicit_anime_status(state)
    if anime_status is not None:
        return anime_status
    return _coerce_category_hint(state.folder_hint or state.category_hint) == "anime"


def _resolve_explicit_disc(state: _ExplicitVideoState) -> Optional[ExplicitPathResolution]:
    disc_leaf_files = _detect_disc_leaf_files(state.entry)
    if not disc_leaf_files and not _DISC_NAME_RE.search(state.entry.name):
        return None

    anime_status = _explicit_anime_status(state)
    disc_category, disc_itype = _disc_base_category(state.entry, state.folder_hint, anime_status)
    override = "DISC content detected"
    if state.category_hint and state.category_hint.strip().lower() == "anime" and anime_status is not True:
        override = "DISC content detected; Jikan did not confirm Anime"
    return ExplicitPathResolution(
        source_path=state.entry,
        category=disc_category,
        itype=disc_itype,
        detection_method="Disc scan",
        queue_paths=(),
        ignored_paths=tuple(IgnoredScanPath(path=path, reason="Disc content") for path in disc_leaf_files),
        content_flags=("disc",),
        override_note=override,
    )


def _resolve_explicit_movie(state: _ExplicitVideoState) -> Optional[ExplicitPathResolution]:
    if _explicit_is_anime(state):
        return None
    if not (
        has_clear_movie_year(state.entry.name)
        and not _matches_episode_pattern(state.entry.name, anime_mode=True)
        and not state.strict_tv_pack
    ):
        return None
    return ExplicitPathResolution(
        source_path=state.entry,
        category="movies",
        itype="Movie",
        detection_method="File scan",
        queue_paths=(state.entry,),
    )


def _anime_override_note(state: _ExplicitVideoState) -> str:
    raw_hint = str(state.category_hint or state.folder_hint).strip()
    return f"Jikan match overrode {raw_hint.upper()} hint" if raw_hint.lower() not in {"", "anime"} else ""


def _resolve_explicit_series(state: _ExplicitVideoState) -> ExplicitPathResolution:
    anime_status = _explicit_anime_status(state)
    is_anime = anime_status is True or (
        anime_status is None and _coerce_category_hint(state.folder_hint or state.category_hint) == "anime"
    )
    if state.entry.is_dir() and _is_multi_season_container_dir(state.entry):
        return ExplicitPathResolution(
            source_path=state.entry,
            category="anime" if is_anime else "tv",
            itype="Anime" if is_anime else "TV Show",
            detection_method="File scan",
            queue_paths=(),
            ignored_paths=(
                IgnoredScanPath(
                    path=state.entry,
                    reason="Multi-season container; queue child season folders individually",
                ),
            ),
        )

    if is_anime:
        anime_selected_paths = (
            state.anime_relaxed_queue_paths
            if state.entry.is_dir()
            else (state.episode_queue_paths or state.anime_queue_paths or state.anime_episode_files)
        )
        anime_rejected_paths = state.anime_relaxed_ignored if state.entry.is_dir() else state.anime_ignored
        return ExplicitPathResolution(
            source_path=state.entry,
            category="anime",
            itype="Anime",
            detection_method="Jikan match",
            queue_paths=anime_selected_paths,
            ignored_paths=anime_rejected_paths,
            override_note=_anime_override_note(state),
        )

    raw_hint = str(state.category_hint or state.folder_hint).strip().lower()
    override = "Jikan rejected anime hint; treating as TV" if raw_hint == "anime" and anime_status is False else ""
    if (
        state.entry.is_file()
        and state.entry.suffix.lower() in state.video_extensions
        and _looks_like_tv_episode_name(state.entry.name)
    ):
        reason = _tv_pack_episode_rejection_reason(state.entry, state.video_extensions)
        return ExplicitPathResolution(
            source_path=state.entry,
            category="tv",
            itype="TV Episode",
            detection_method="File scan",
            queue_paths=() if reason else (state.entry,),
            ignored_paths=(IgnoredScanPath(path=state.entry, reason=reason),) if reason else (),
            override_note=override,
        )
    if (
        state.entry.is_file()
        and state.entry.suffix.lower() in state.video_extensions
        and not _TV_PACK_SOURCE_RE.search(state.entry.stem)
    ):
        return ExplicitPathResolution(
            source_path=state.entry,
            category="tv",
            itype="TV Episode" if _looks_like_tv_episode_name(state.entry.name) else "Misc",
            detection_method="File scan",
            queue_paths=(),
            ignored_paths=(IgnoredScanPath(path=state.entry, reason="Missing media source token"),),
            override_note=override,
        )

    if state.episode_queue_paths:
        tv_queue_paths = state.episode_queue_paths
        tv_ignored = state.non_episode_files
    else:
        tv_queue_paths, tv_ignored = _split_tv_video_files(state.video_files, state.video_extensions)
    return ExplicitPathResolution(
        source_path=state.entry,
        category="tv",
        itype="TV Show" if state.entry.is_dir() else "TV Episode",
        detection_method="File scan",
        queue_paths=tv_queue_paths,
        ignored_paths=tv_ignored,
        override_note=override,
    )


def _resolve_explicit_anime(state: _ExplicitVideoState) -> Optional[ExplicitPathResolution]:
    if not _explicit_is_anime(state):
        return None
    anime_selected_paths = (
        state.anime_relaxed_queue_paths
        if state.entry.is_dir()
        else (state.anime_queue_paths or state.anime_episode_files or ((state.entry,) if state.entry.is_file() else ()))
    )
    anime_rejected_paths = state.anime_relaxed_ignored if state.entry.is_dir() else state.anime_ignored
    return ExplicitPathResolution(
        source_path=state.entry,
        category="anime",
        itype="Anime",
        detection_method="Jikan match",
        queue_paths=anime_selected_paths,
        ignored_paths=anime_rejected_paths,
        override_note=_anime_override_note(state),
    )


def _resolve_explicit_fallback(state: _ExplicitVideoState) -> ExplicitPathResolution:
    fallback_category = _coerce_category_hint(state.folder_hint or state.category_hint)
    fallback_category = fallback_category or _hint_category_from_itype(state.itype_hint) or "misc"
    queue_paths = (state.entry,)
    ignored_paths: Tuple[IgnoredScanPath, ...] = ()
    if (
        fallback_category in {"anime", "tv", "movies"}
        and state.entry.is_file()
        and state.entry.suffix.lower() in state.video_extensions
    ):
        if (
            fallback_category in {"tv", "anime"}
            and not _TV_PACK_SOURCE_RE.search(state.entry.stem)
            and _needs_source_token(
                fallback_category,
                _movie_fallback_itype(fallback_category),
                state.entry.name,
            )
        ):
            queue_paths = ()
            ignored_paths = (IgnoredScanPath(path=state.entry, reason="Missing media source token"),)
    elif fallback_category == "tv" and state.entry.is_dir():
        if state.episode_queue_paths:
            queue_paths = state.episode_queue_paths
            ignored_paths = state.non_episode_files
        elif state.video_files:
            queue_paths, ignored_paths = _split_tv_video_files(state.video_files, state.video_extensions)
        else:
            queue_paths = ()
            ignored_paths = state.non_episode_files

    return ExplicitPathResolution(
        source_path=state.entry,
        category=fallback_category,
        itype=_movie_fallback_itype(fallback_category),
        detection_method="Folder fallback",
        queue_paths=queue_paths,
        ignored_paths=ignored_paths,
    )


def resolve_explicit_path(
    entry: Path,
    *,
    category_hint: str = "",
    itype_hint: str = "",
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
    video_extensions: Optional[Set[str]] = None,
) -> ExplicitPathResolution:
    """Resolve one explicit path into a processing category with anime filtering."""
    video_exts = video_extensions or VIDEO_EXTENSIONS
    lookup = anime_lookup or _default_cached_anime_lookup
    folder_hint = infer_entry_category_hint(entry, category_hint, itype_hint)
    all_leaf_files = _iter_leaf_files(entry)
    early_resolution = _resolve_early_explicit_path(
        entry,
        category_hint=category_hint,
        itype_hint=itype_hint,
        folder_hint=folder_hint,
        all_leaf_files=all_leaf_files,
        video_extensions=video_exts,
        anime_lookup=lookup,
    )
    if early_resolution is not None:
        return early_resolution

    state = _build_explicit_video_state(
        entry,
        category_hint=category_hint,
        itype_hint=itype_hint,
        folder_hint=folder_hint,
        video_extensions=video_exts,
        anime_lookup=lookup,
        all_leaf_files=all_leaf_files,
    )
    for resolver in (_resolve_explicit_disc, _resolve_explicit_movie):
        resolution = resolver(state)
        if resolution is not None:
            return resolution
    if state.series_like:
        return _resolve_explicit_series(state)
    anime_resolution = _resolve_explicit_anime(state)
    return anime_resolution or _resolve_explicit_fallback(state)


def detect_external_category(name: str, entry_path: Path) -> str:
    """Auto-detect queue category for an external folder/file based on naming patterns."""
    disc_leaf_files = _detect_disc_leaf_files(entry_path)
    if disc_leaf_files or _DISC_NAME_RE.search(name):
        category, _itype = _disc_base_category(entry_path, infer_folder_category_hint(entry_path.parent), None)
        return category
    non_video_category = _non_video_media_category(entry_path)
    if non_video_category:
        return non_video_category
    anime_status = _lookup_anime_status(
        entry_path,
        _iter_video_candidates(entry_path, VIDEO_EXTENSIONS),
        _default_cached_anime_lookup,
    )
    if anime_status is True:
        return "anime"
    if entry_path.is_dir():
        leaf_files = _iter_leaf_files(entry_path)
        if any(path.suffix.lower() in EBOOK_EXTENSIONS for path in leaf_files):
            return "books"
        if any(_ANIME_EXTRA_RE.search(path.name) for path in leaf_files):
            return "anime"
    elif _ANIME_EXTRA_RE.search(name):
        return "anime"
    if has_clear_movie_year(name):
        return "movies"
    if looks_like_tv_name(name):
        return "tv"

    if entry_path.is_dir():
        try:
            children = [child.name for child in entry_path.iterdir() if not child.name.startswith(".")]
        except OSError:
            children = []
        if any(looks_like_tv_name(child) for child in children) or has_multi_file_episode_pattern(children):
            return "tv"
        if _has_tv_episode_like_video(children):
            return "tv"
        if _has_related_video_files(children):
            return "tv"

    return ""


def _hinted_content_itype(folder_category: str) -> str:
    return {
        "music": "Music",
        "audiobooks": "Audiobook",
        "books": "Ebook",
        "apps": "App",
    }.get(str(folder_category or "").strip().lower(), "")


def _detect_file_content_itype(
    name: str,
    entry_path: Path,
    folder_category: str,
    video_files: Tuple[Path, ...],
    lookup: Callable[[str], Optional[bool]],
    anime_lookup: Optional[Callable[[str], Optional[bool]]],
) -> str:
    ext = Path(name).suffix.lower()
    stem = Path(str(name)).stem
    if ext in AUDIOBOOK_EXTENSIONS:
        return "Audiobook"
    if ext in EBOOK_EXTENSIONS:
        return "Ebook"
    if ext in MUSIC_EXTENSIONS:
        return "Music"
    if ext not in VIDEO_EXTENSIONS:
        result = classify_video_name(name, folder_category, anime_lookup=lookup)
        return _hinted_content_itype(folder_category) if result == "Misc" else result

    anime_status = _lookup_anime_status(entry_path, video_files, lookup)
    if anime_status is True:
        return "Anime"
    if anime_status is None and str(folder_category or "").strip().lower() == "anime":
        return "Anime"
    if _ANIME_EXTRA_RE.search(name):
        return "Anime"
    if has_clear_movie_year(name) and not _matches_episode_pattern(name, anime_mode=True):
        return "Movie"
    if _looks_like_source_bearing_tv_episode(name):
        return "TV Episode"
    if _matches_episode_pattern(name) or _TV_EPISODE_HINT_RE.search(stem):
        return "TV Episode"
    if looks_like_tv_name(name) or _has_tv_context(entry_path):
        return "TV Episode"
    if _looks_like_tv_episode_name(name) or _has_tv_context(entry_path):
        return "TV Episode"
    return "Misc"


def _scan_content_extensions(entry_path: Path) -> tuple[dict[str, int], list[str]]:
    ext_counts: dict[str, int] = {}
    video_names: list[str] = []
    try:
        for child in entry_path.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_file():
                child_ext = child.suffix.lower()
                ext_counts[child_ext] = ext_counts.get(child_ext, 0) + 1
                if child_ext in VIDEO_EXTENSIONS:
                    video_names.append(child.name)
            elif child.is_dir():
                try:
                    for grandchild in child.iterdir():
                        if grandchild.is_file() and not grandchild.name.startswith("."):
                            child_ext = grandchild.suffix.lower()
                            ext_counts[child_ext] = ext_counts.get(child_ext, 0) + 1
                            if child_ext in VIDEO_EXTENSIONS:
                                video_names.append(grandchild.name)
                except OSError:
                    continue
    except OSError:
        return {}, []
    return ext_counts, video_names


def _detect_directory_content_itype(
    name: str,
    entry_path: Path,
    ext_counts: dict[str, int],
    video_names: list[str],
    video_files: Tuple[Path, ...],
    lookup: Callable[[str], Optional[bool]],
    folder_category: str,
) -> str:
    if not ext_counts:
        return ""

    total = sum(ext_counts.values())
    audiobook_count = sum(value for ext, value in ext_counts.items() if ext in AUDIOBOOK_EXTENSIONS)
    music_count = sum(value for ext, value in ext_counts.items() if ext in MUSIC_EXTENSIONS)
    ebook_count = sum(value for ext, value in ext_counts.items() if ext in EBOOK_EXTENSIONS)
    video_count = sum(value for ext, value in ext_counts.items() if ext in VIDEO_EXTENSIONS)
    anime_status = _lookup_anime_status(entry_path, video_files, lookup)

    if anime_status is True:
        return "Anime"
    if anime_status is None and str(folder_category or "").strip().lower() == "anime":
        return "Anime"
    if any(_ANIME_EXTRA_RE.search(video_name) for video_name in video_names):
        return "Anime"
    if has_clear_movie_year(name):
        return "Movie"
    if video_count >= 1 and (
        _has_tv_episode_like_video(video_names)
        or any(_looks_like_source_bearing_tv_episode(video_name) for video_name in video_names)
    ):
        return "TV Show"
    if video_count >= 2 and has_multi_file_episode_pattern(video_names):
        return "TV Show"
    if video_count >= 2 and _has_related_video_files(video_names):
        return "TV Show"
    if audiobook_count > 0 and video_count == 0 and music_count == 0 and ebook_count == 0:
        return "Audiobook"
    if total > 0 and music_count / total >= 0.5 and video_count == 0:
        return "Music"
    if ebook_count > 0 and video_count == 0:
        return "Ebook"
    return ""


def detect_content_itype(
    name: str,
    entry_path: Path,
    folder_category: str,
    *,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
) -> str:
    """Detect the display content type for a pending item."""
    lookup = anime_lookup or _default_cached_anime_lookup
    video_files = _iter_video_candidates(entry_path, VIDEO_EXTENSIONS)
    disc_leaf_files = _detect_disc_leaf_files(entry_path)
    if disc_leaf_files or _DISC_NAME_RE.search(name):
        _category, itype = _disc_base_category(
            entry_path,
            infer_entry_category_hint(entry_path, folder_category),
            _lookup_anime_status(entry_path, video_files, lookup),
        )
        return itype

    non_video_category = _non_video_media_category(entry_path)
    if non_video_category:
        return _movie_fallback_itype(non_video_category)
    if not entry_path.is_dir():
        return _detect_file_content_itype(
            name,
            entry_path,
            folder_category,
            video_files,
            lookup,
            anime_lookup,
        )

    ext_counts, video_names = _scan_content_extensions(entry_path)
    detected = _detect_directory_content_itype(
        name,
        entry_path,
        ext_counts,
        video_names,
        video_files,
        lookup,
        folder_category,
    )
    if detected:
        return detected
    anime_status = _lookup_anime_status(entry_path, video_files, lookup)
    if anime_status is True:
        return "Anime"
    if anime_status is None and str(folder_category or "").strip().lower() == "anime":
        return "Anime"
    if has_clear_movie_year(name):
        return "Movie"
    if looks_like_tv_name(name):
        return "TV Show"
    hinted = _hinted_content_itype(folder_category)
    if hinted:
        return hinted
    return "Misc"


def detect_auto_itype(
    entry: Path,
    folder_category_hint: str = "",
    *,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
) -> str:
    """Best-effort content type detection for external-style folders."""
    return detect_content_itype(entry.name, entry, folder_category_hint, anime_lookup=anime_lookup)


def detect_auto_category(
    entry: Path,
    folder_category_hint: str = "",
    *,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
) -> str:
    """Map an entry to the upload category used by explicit-path jobs."""
    itype = detect_auto_itype(entry, folder_category_hint, anime_lookup=anime_lookup)
    return {
        "TV Show": "tv",
        "TV Episode": "tv",
        "Anime": "anime",
        "Movie": "movies",
        "Music": "music",
        "Audiobook": "audiobooks",
        "Ebook": "books",
        "App": "apps",
    }.get(itype, "misc")


def _resolve_configured_scan_category(configured_category: str, entry: Path, folder_category_hint: str = "") -> str:
    """Return the effective queue category for a configured folder entry."""
    normalized = str(configured_category or "").strip().lower()
    if normalized in _AUTO_ROOT_CATEGORIES:
        return detect_auto_category(entry, folder_category_hint)
    return normalized


def _collect_tv_episode_paths(entry: Path, video_extensions: Set[str]) -> Tuple[Path, ...]:
    if entry.is_dir():
        return tuple(sorted(_iter_video_files(entry, video_extensions), key=lambda path: str(path).casefold()))
    if entry.is_file() and entry.suffix.lower() in video_extensions:
        return (entry,)
    return ()


def build_pending_scan_item(
    folder: Path,
    configured_category: str,
    entry: Path,
    *,
    video_extensions: Optional[Set[str]] = None,
) -> PendingScanItem:
    """Build a canonical top-level scan item for a configured folder entry."""
    folder_category_hint = infer_folder_category_hint(folder)
    effective_category = _resolve_configured_scan_category(configured_category, entry, folder_category_hint)
    video_exts = video_extensions or VIDEO_EXTENSIONS
    episode_paths: Tuple[Path, ...] = ()
    episode_rel_keys: Tuple[str, ...] = ()

    if effective_category == "tv":
        episode_paths = _collect_tv_episode_paths(entry, video_exts)
        episode_rel_keys = tuple(relative_key(video, folder) for video in episode_paths)

    return PendingScanItem(
        category=effective_category,
        configured_category=str(configured_category or "").strip().lower() or "external",
        folder=folder,
        path=entry,
        name=entry.name,
        rel_key=relative_key(entry, folder),
        is_dir=entry.is_dir(),
        episode_paths=episode_paths,
        episode_rel_keys=episode_rel_keys,
    )


def scan_folder_items(
    folder: Path,
    configured_category: str,
    *,
    sort_entries: bool = True,
    video_extensions: Optional[Set[str]] = None,
) -> List[PendingScanItem]:
    """Scan one configured folder and return normalized top-level items."""
    return [
        build_pending_scan_item(folder, configured_category, entry, video_extensions=video_extensions)
        for entry in iter_visible_entries(folder, sort_entries=sort_entries)
    ]


def scan_configured_items(
    conf: Any,
    *,
    filter_category: Optional[str] = None,
    include_external: bool = True,
    must_exist: bool = False,
    sort_entries: bool = True,
    video_extensions: Optional[Set[str]] = None,
) -> List[PendingScanItem]:
    """Return canonical top-level scan items across all configured folders."""
    wanted = str(filter_category or "").strip().lower() or None
    items: List[PendingScanItem] = []

    for configured_category, folder in get_configured_category_folders(
        conf,
        filter_category=None,
        include_external=include_external,
        must_exist=must_exist,
    ):
        for item in scan_folder_items(
            folder,
            configured_category,
            sort_entries=sort_entries,
            video_extensions=video_extensions,
        ):
            if wanted and item.category != wanted:
                continue
            items.append(item)

    return items


def collect_auto_category_scan_items(folder: Path) -> List[Tuple[str, Path]]:
    """Collect direct child entries from a folder grouped by inferred category."""
    return [(item.category, item.path) for item in scan_folder_items(folder, "external", sort_entries=True)]


def collect_configured_scan_items(conf: Any, *, must_exist: bool = False) -> List[Tuple[str, Path, Path]]:
    """Collect direct child entries from configured folders using assigned categories."""
    return [(item.category, item.folder, item.path) for item in scan_configured_items(conf, must_exist=must_exist)]


def collect_category_scan_items(folder: Path, category: str, video_extensions: Set[str]) -> List[ScanPathItem]:
    """Collect pending-scan items for a specific configured category folder."""
    if not folder.exists():
        return []

    items: List[ScanPathItem] = []

    for scan_item in scan_folder_items(folder, category, sort_entries=False, video_extensions=video_extensions):
        if scan_item.category == "tv":
            for episode_path, episode_rel_key in zip(scan_item.episode_paths, scan_item.episode_rel_keys):
                items.append(
                    ScanPathItem(
                        path=episode_path,
                        name=episode_path.name,
                        rel_key=episode_rel_key,
                        is_episode=True,
                    )
                )
            if scan_item.is_dir or not scan_item.episode_paths:
                items.append(
                    ScanPathItem(
                        path=scan_item.path,
                        name=scan_item.name,
                        rel_key=scan_item.rel_key,
                        is_episode=False,
                    )
                )
            continue

        items.append(
            ScanPathItem(
                path=scan_item.path,
                name=scan_item.name,
                rel_key=scan_item.rel_key,
                is_episode=False,
            )
        )

    return items


def find_configured_root(path: Path, folders: Iterable[Path]) -> Optional[Path]:
    """Return the deepest configured folder that contains the given path."""
    best_match: Optional[Path] = None
    best_len = -1
    for folder in folders:
        try:
            path.relative_to(folder)
        except ValueError:
            continue
        folder_len = len(str(folder))
        if folder_len > best_len:
            best_match = folder
            best_len = folder_len
    return best_match


def relative_key(path: Path, folder: Path) -> str:
    """Build a stable slash-normalized key relative to the category folder."""
    try:
        return str(path.relative_to(folder)).replace("\\", "/")
    except ValueError:
        return path.name


def iter_visible_entries(folder: Path, *, sort_entries: bool = False) -> List[Path]:
    """Return non-hidden direct children of a folder."""
    entries: List[Path] = []
    try:
        with os.scandir(str(folder)) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                entries.append(Path(entry.path))
    except OSError:
        return []

    if sort_entries:
        entries.sort(key=lambda p: p.name.lower())
    return entries


def _iter_video_files(folder: Path, video_extensions: Set[str]) -> Iterable[Path]:
    for root, _dirs, files in os.walk(str(folder)):
        for fname in files:
            if fname.startswith("."):
                continue
            if os.path.splitext(fname)[1].lower() in video_extensions:
                yield Path(root) / fname
