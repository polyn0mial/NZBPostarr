# Auto-split from pending_scan.py - verbatim symbol bodies, synthesized imports.

from logic.pending_scan_base import (
    Any, Iterable, List, Optional, Path, Set, Tuple, VIDEO_EXTENSIONS, _ANIME_EXTRA_RE, _ANIME_LOOKUP_CLEAN_RE, _ANIME_SEASONAL_FOLDER_RE,
    _AUTO_ANIME_FANSUB_RE, _AUTO_COMPLETE_SERIES_RANGE_RE, _AUTO_MOVIE_PATTERNS, _AUTO_ROOT_CATEGORIES, _AUTO_TV_PATTERNS, _AUTO_YEAR_RANGE_RE,
    _AUTO_YEAR_TOKEN_RE, _COMPLETE_MINISERIES_RE, _DATE_EPISODE_RE, _EXPLICIT_EPISODE_RE, _FOLDER_CATEGORY_HINTS, _GENERIC_TV_SEASON_FOLDER_RE,
    _ITYPE_TO_CATEGORY, _SCAN_CACHE_VAR, _SERIES_SIGNATURE_STRIP_RE, _SPORTS_EVENT_CONTEXT_RE, _SPORTS_LEAGUE_RE, _STREAMING_EPISODE_SOURCE_RE,
    _TRAILING_RELEASE_GROUP_YEAR_RE, _TV_PACK_SOURCE_RE, _TV_PART_EPISODE_RE, contextvars, dataclass, has_multi_file_episode_pattern, os, re,
    unicodedata,
)

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
class VideoClassificationResult:
    """Canonical name-level classification with evidence strength."""

    category: str
    itype: str
    confidence: str
    method: str
    evidence: Tuple[str, ...] = ()

def category_from_itype(itype: str, default: str = "misc") -> str:
    """Map a display item type to its canonical submission category."""
    return _ITYPE_TO_CATEGORY.get(str(itype or "").strip().lower(), default)

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
    detection_confidence: str = ""
    detection_evidence: Tuple[str, ...] = ()

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

        evidence = self.detection_evidence
        if evidence is None:
            object.__setattr__(self, "detection_evidence", ())
        elif not isinstance(evidence, tuple):
            try:
                object.__setattr__(self, "detection_evidence", tuple(evidence))
            except TypeError:
                object.__setattr__(self, "detection_evidence", ())

        if not self.detection_confidence:
            confidence = "unknown" if self.category in {"", "misc"} else "strong"
            object.__setattr__(self, "detection_confidence", confidence)

def _folder_path_entry_categories(
    folder_entries: Any,
    *,
    wanted: Optional[str],
    include_external: bool,
    must_exist: bool,
    seen: Set[tuple[str, str]],
) -> List[Tuple[str, Path]]:
    """Scan roots from the modern `folder_paths` entries."""
    categories: List[Tuple[str, Path]] = []

    for fp in folder_entries:
        if isinstance(fp, str):
            fp = {"path": fp}
        if not isinstance(fp, dict):
            continue

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

    return categories

def get_configured_category_folders(
    conf: Any,
    *,
    filter_category: Optional[str] = None,
    include_external: bool = False,
    must_exist: bool = False,
) -> List[Tuple[str, Path]]:
    """Return configured folders as generic external scan roots."""
    seen: Set[tuple[str, str]] = set()
    wanted = str(filter_category or "").strip().lower() or None
    if wanted == "auto":
        wanted = "external"

    get_folder_path_entries = getattr(conf, "get_folder_path_entries", None)
    folder_entries = (
        get_folder_path_entries() if callable(get_folder_path_entries) else getattr(conf, "folder_paths", [])
    )

    return _folder_path_entry_categories(
        folder_entries,
        wanted=wanted,
        include_external=include_external,
        must_exist=must_exist,
        seen=seen,
    )

def looks_like_tv_name(name: str) -> bool:
    """Return True when a release name clearly looks episodic/TV-like."""
    if _AUTO_COMPLETE_SERIES_RANGE_RE.search(name):
        return True
    if _COMPLETE_MINISERIES_RE.search(name):
        return True
    # An air-date stamp outranks the movie-year veto below: the veto matches the
    # YYYY of the date itself, so without this a daily show reads as a movie.
    if _DATE_EPISODE_RE.search(name):
        return True
    if has_clear_movie_year(name):
        return False
    return bool(_AUTO_TV_PATTERNS.search(name))

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

def has_clear_movie_year(name: str) -> bool:
    """Return True when a release name has exactly one plausible movie year token."""
    comparable_name = Path(str(name)).stem
    comparable_name = _TRAILING_RELEASE_GROUP_YEAR_RE.sub("", comparable_name)
    if not _AUTO_MOVIE_PATTERNS.search(comparable_name):
        return False
    if _AUTO_YEAR_RANGE_RE.search(comparable_name):
        return False
    return len(set(_AUTO_YEAR_TOKEN_RE.findall(comparable_name))) == 1

def _looks_like_sports_event(name: str) -> bool:
    """Return True for a recognized league paired with event/broadcast context."""
    stem = Path(str(name or "")).stem
    return bool(_SPORTS_LEAGUE_RE.search(stem) and _SPORTS_EVENT_CONTEXT_RE.search(stem))

def _looks_like_part_episode(name: str, folder_hint: str = "") -> bool:
    """Require TV corroboration before treating ``Part N`` as an episode."""
    raw_name = str(name or "")
    stem = Path(raw_name).stem
    match = _TV_PART_EPISODE_RE.search(stem)
    if not match:
        return False
    if _coerce_category_hint(folder_hint) in {"tv", "anime"}:
        return True

    tail = stem[match.end() :]
    year_match = _AUTO_YEAR_TOKEN_RE.search(tail)
    if year_match and re.search(r"[A-Za-z]{3,}", tail[: year_match.start()]):
        return True

    title_words = [
        word
        for word in re.findall(r"[A-Za-z]{2,}", stem[: match.start()])
        if word.casefold() not in {"a", "an", "the", "of", "in", "on", "at", "to", "and", "or"}
    ]
    if year_match is None:
        return bool(len(title_words) >= 2 and _TV_PACK_SOURCE_RE.search(raw_name))
    return bool(
        year_match
        and 1 <= len(title_words) <= 2
        and any(len(word) >= 5 for word in title_words)
        and _STREAMING_EPISODE_SOURCE_RE.search(raw_name)
    )

def _video_classification(
    category: str,
    itype: str,
    confidence: str,
    method: str,
    *evidence: str,
) -> VideoClassificationResult:
    return VideoClassificationResult(
        category=category,
        itype=itype,
        confidence=confidence,
        method=method,
        evidence=tuple(item for item in evidence if item),
    )

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

def infer_entry_category_hint(entry: Path, explicit_hint: str = "", itype_hint: str = "") -> str:
    """Infer the weakest-possible category hint from path structure and UI metadata."""
    for hinted in (_coerce_category_hint(explicit_hint), _hint_category_from_itype(itype_hint)):
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

def _is_non_episode_anime_extra(name: str) -> bool:
    stem = Path(name).stem
    return bool(_ANIME_EXTRA_RE.search(stem)) and not _EXPLICIT_EPISODE_RE.search(stem)

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

def _hinted_content_itype(folder_category: str) -> str:
    return {
        "music": "Music",
        "audiobooks": "Audiobook",
        "books": "Ebook",
        "apps": "App",
    }.get(str(folder_category or "").strip().lower(), "")

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

def _is_anime_directory(
    anime_status: Optional[bool],
    folder_category: str,
    video_names: list[str],
) -> bool:
    if anime_status is True:
        return True
    if anime_status is None and str(folder_category or "").strip().lower() == "anime":
        return True
    if any(_ANIME_EXTRA_RE.search(video_name) for video_name in video_names):
        return True
    return False

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
