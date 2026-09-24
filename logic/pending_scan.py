"""Shared pending-scan helpers used by API, dashboard, and headless flows."""
from logic.pending_scan_base import (
    AUDIOBOOK_EXTENSIONS as AUDIOBOOK_EXTENSIONS, Any as Any, Callable as Callable, DEFAULT_TV_PACK_IGNORE_RULES as DEFAULT_TV_PACK_IGNORE_RULES,
    EBOOK_EXTENSIONS as EBOOK_EXTENSIONS, Iterable as Iterable, List as List, MUSIC_EXTENSIONS as MUSIC_EXTENSIONS, Optional as Optional, Path as Path,
    Set as Set, TV_PACK_IGNORED_FILE_TYPES as TV_PACK_IGNORED_FILE_TYPES, TV_PACK_IGNORED_NAME_PATTERNS as TV_PACK_IGNORED_NAME_PATTERNS,
    TV_PACK_IGNORE_RULE_LABELS as TV_PACK_IGNORE_RULE_LABELS, Tuple as Tuple, VIDEO_EXTENSIONS as VIDEO_EXTENSIONS,
    _AMBIGUOUS_TRACK_RE as _AMBIGUOUS_TRACK_RE, _ANIME_ABSOLUTE_EPISODE_RE as _ANIME_ABSOLUTE_EPISODE_RE, _ANIME_EXTRA_RE as _ANIME_EXTRA_RE,
    _ANIME_LOOKUP_CLEAN_RE as _ANIME_LOOKUP_CLEAN_RE, _ANIME_SEASONAL_FOLDER_RE as _ANIME_SEASONAL_FOLDER_RE,
    _APP_FILE_EXTENSIONS as _APP_FILE_EXTENSIONS, _AUDIOBOOK_CHAPTER_RE as _AUDIOBOOK_CHAPTER_RE, _AUDIOBOOK_HINT_RE as _AUDIOBOOK_HINT_RE,
    _AUTO_ANIME_FANSUB_RE as _AUTO_ANIME_FANSUB_RE, _AUTO_COMPLETE_SERIES_RANGE_RE as _AUTO_COMPLETE_SERIES_RANGE_RE,
    _AUTO_MOVIE_PATTERNS as _AUTO_MOVIE_PATTERNS, _AUTO_ROOT_CATEGORIES as _AUTO_ROOT_CATEGORIES, _AUTO_SEASON_TOKEN_RE as _AUTO_SEASON_TOKEN_RE,
    _AUTO_TV_PATTERNS as _AUTO_TV_PATTERNS, _AUTO_YEAR_RANGE_RE as _AUTO_YEAR_RANGE_RE, _AUTO_YEAR_TOKEN_RE as _AUTO_YEAR_TOKEN_RE,
    _BARE_HIGH_ABSOLUTE_EPISODE_RE as _BARE_HIGH_ABSOLUTE_EPISODE_RE, _COMPLETE_MINISERIES_RE as _COMPLETE_MINISERIES_RE,
    _DATE_EPISODE_RE as _DATE_EPISODE_RE, _DISC_IMAGE_EXTENSIONS as _DISC_IMAGE_EXTENSIONS, _DISC_STRUCTURE_DIRS as _DISC_STRUCTURE_DIRS,
    _EBOOK_HINT_RE as _EBOOK_HINT_RE, _EXPLICIT_EPISODE_RE as _EXPLICIT_EPISODE_RE, _FOLDER_CATEGORY_HINTS as _FOLDER_CATEGORY_HINTS,
    _FRAMED_ABSOLUTE_EPISODE_RE as _FRAMED_ABSOLUTE_EPISODE_RE, _GENERIC_ANIME_FOLDER_RE as _GENERIC_ANIME_FOLDER_RE,
    _GENERIC_TV_SEASON_FOLDER_RE as _GENERIC_TV_SEASON_FOLDER_RE, _GUESSIT_EPISODE_SHAPE_RE as _GUESSIT_EPISODE_SHAPE_RE,
    _ITYPE_TO_CATEGORY as _ITYPE_TO_CATEGORY, _MOVIE_COLLECTION_RE as _MOVIE_COLLECTION_RE, _MUSIC_RELEASE_HINT_RE as _MUSIC_RELEASE_HINT_RE,
    _SCAN_CACHE_VAR as _SCAN_CACHE_VAR, _SERIES_SIGNATURE_STRIP_RE as _SERIES_SIGNATURE_STRIP_RE, _SPORTS_EVENT_CONTEXT_RE as _SPORTS_EVENT_CONTEXT_RE,
    _SPORTS_LEAGUE_RE as _SPORTS_LEAGUE_RE, _STREAMING_EPISODE_SOURCE_RE as _STREAMING_EPISODE_SOURCE_RE,
    _TRAILING_RELEASE_GROUP_YEAR_RE as _TRAILING_RELEASE_GROUP_YEAR_RE, _TV_EPISODE_HINT_RE as _TV_EPISODE_HINT_RE,
    _TV_PACK_EPISODE_RE as _TV_PACK_EPISODE_RE, _TV_PACK_EXTRAS_WORD_RE as _TV_PACK_EXTRAS_WORD_RE, _TV_PACK_EXTRA_RE as _TV_PACK_EXTRA_RE,
    _TV_PACK_HARD_EXTRA_RE as _TV_PACK_HARD_EXTRA_RE, _TV_PACK_RESOLUTION_RE as _TV_PACK_RESOLUTION_RE, _TV_PACK_SOURCE_RE as _TV_PACK_SOURCE_RE,
    _TV_PART_EPISODE_RE as _TV_PART_EPISODE_RE, annotations as annotations, contextvars as contextvars, dataclass as dataclass,
    has_multi_file_episode_pattern as has_multi_file_episode_pattern, os as os, parse_release_name as parse_release_name, re as re,
    unicodedata as unicodedata,
)
from logic.pending_scan_g1 import (
    ExplicitPathResolution as ExplicitPathResolution, IgnoredScanPath as IgnoredScanPath, PendingScanItem as PendingScanItem,
    ScanPathItem as ScanPathItem, VideoClassificationResult as VideoClassificationResult, _coerce_category_hint as _coerce_category_hint,
    _default_cached_anime_lookup as _default_cached_anime_lookup, _entry_hint_text as _entry_hint_text,
    _folder_path_entry_categories as _folder_path_entry_categories, _has_related_video_files as _has_related_video_files,
    _hint_category_from_itype as _hint_category_from_itype, _hinted_content_itype as _hinted_content_itype, _is_anime_directory as _is_anime_directory,
    _is_non_episode_anime_extra as _is_non_episode_anime_extra, _iter_leaf_files as _iter_leaf_files, _iter_video_candidates as _iter_video_candidates,
    _iter_video_files as _iter_video_files, _legacy_external_folder_categories as _legacy_external_folder_categories,
    _legacy_folder_field_categories as _legacy_folder_field_categories, _looks_like_part_episode as _looks_like_part_episode,
    _looks_like_sports_event as _looks_like_sports_event, _movie_fallback_itype as _movie_fallback_itype,
    _normalize_lookup_title as _normalize_lookup_title, _scan_cache_get as _scan_cache_get, _scan_cache_put as _scan_cache_put,
    _scan_content_extensions as _scan_content_extensions, _series_signature as _series_signature, _video_classification as _video_classification,
    begin_scan_cache as begin_scan_cache, category_from_itype as category_from_itype, end_scan_cache as end_scan_cache,
    find_configured_root as find_configured_root, get_configured_category_folders as get_configured_category_folders,
    has_clear_movie_year as has_clear_movie_year, infer_entry_category_hint as infer_entry_category_hint,
    infer_folder_category_hint as infer_folder_category_hint, iter_visible_entries as iter_visible_entries,
    looks_like_generic_tv_season_folder as looks_like_generic_tv_season_folder, looks_like_tv_name as looks_like_tv_name, relative_key as relative_key,
)
from logic.pending_scan_g2 import (
    _ExplicitVideoState as _ExplicitVideoState, _anime_lookup_candidates as _anime_lookup_candidates, _anime_override_note as _anime_override_note,
    _anime_resolution_provenance as _anime_resolution_provenance, _best_series_lookup_name as _best_series_lookup_name,
    _collect_tv_episode_paths as _collect_tv_episode_paths, _detect_video_disc_leaf_files as _detect_video_disc_leaf_files,
    _disc_base_category as _disc_base_category, _explicit_anime_status as _explicit_anime_status, _explicit_is_anime as _explicit_is_anime,
    _has_nested_tv_context as _has_nested_tv_context, _has_tv_context as _has_tv_context, _lookup_anime_status as _lookup_anime_status,
    _non_video_media_category as _non_video_media_category, _resolve_explicit_anime as _resolve_explicit_anime,
    _resolve_explicit_disc as _resolve_explicit_disc, _resolve_explicit_series_anime as _resolve_explicit_series_anime,
    _resolve_explicit_series_non_video as _resolve_explicit_series_non_video, _resolve_strict_tv_pack as _resolve_strict_tv_pack,
    anime_lookup_candidates as anime_lookup_candidates, classify_audio_folder as classify_audio_folder, get_configured_folders as get_configured_folders,
    has_video_disc_structure as has_video_disc_structure,
)

def _classify_by_explicit_or_anime_hint(
    name: str,
    folder_hint: str,
    anime_lookup: Optional[Callable[[str], Optional[bool]]],
    explicit_category_hint: str,
    explicit_itype_hint: str,
) -> Optional[VideoClassificationResult]:
    """Resolve classification from an explicit user category or an anime-cache/folder hint, if any applies."""
    # A category selected by the user is authoritative.  The accompanying
    # display type can be stale after a UI category change, so it must not
    # reverse that explicit choice.
    explicit_hint = _coerce_category_hint(explicit_category_hint) or _hint_category_from_itype(explicit_itype_hint)
    if explicit_hint == "anime":
        return _video_classification("anime", "Anime", "confirmed", "Explicit category", "anime")
    if explicit_hint == "tv":
        return _video_classification("tv", "TV Show", "confirmed", "Explicit category", "tv")
    if explicit_hint == "movies":
        return _video_classification("movies", "Movie", "confirmed", "Explicit category", "movies")

    anime_status: Optional[bool] = None
    if anime_lookup is not None:
        try:
            anime_status = anime_lookup(name)
        except Exception:
            anime_status = None

    if anime_status is True:
        return _video_classification("anime", "Anime", "confirmed", "Anime cache", "confirmed anime")
    if anime_status is None and folder_hint == "anime":
        return _video_classification("anime", "Anime", "strong", "Configured folder", "anime folder")
    if folder_hint in {"movie", "movies"} and not _matches_episode_pattern(name, anime_mode=True):
        return _video_classification("movies", "Movie", "strong", "Configured folder", "movies folder")
    return None

def classify_video_name_result(
    name: str,
    folder_category: str = "",
    *,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
    explicit_category_hint: str = "",
    explicit_itype_hint: str = "",
) -> VideoClassificationResult:
    """Classify one video name without silently converting unknowns to movies."""
    folder_hint = str(folder_category or "").strip().lower()
    hinted = _classify_by_explicit_or_anime_hint(
        name, folder_hint, anime_lookup, explicit_category_hint, explicit_itype_hint
    )
    if hinted is not None:
        return hinted
    if _matches_episode_pattern(name, include_guessit=False):
        return _video_classification("tv", "TV Show", "strong", "Episode pattern", "episode token")
    if _DATE_EPISODE_RE.search(name):
        return _video_classification("tv", "TV Show", "strong", "Episode pattern", "air date")
    if _looks_like_part_episode(name, folder_hint):
        return _video_classification("tv", "TV Show", "strong", "Episode pattern", "part episode")
    if _COMPLETE_MINISERIES_RE.search(name):
        return _video_classification("tv", "TV Show", "strong", "Series pattern", "complete miniseries")
    if _looks_like_sports_event(name):
        return _video_classification("tv", "TV Show", "strong", "Event pattern", "sports event")

    try:
        parsed = parse_release_name(name)
    except Exception:
        parsed = {}
    parsed_media_type = str(parsed.get("media_type") or "").strip().lower()
    parsed_season = parsed.get("season_number")
    parsed_episode = parsed.get("episode_number")
    if (
        parsed_media_type == "tv"
        and (parsed_season is not None or parsed_episode is not None)
        and _GUESSIT_EPISODE_SHAPE_RE.search(name)
    ):
        return _video_classification("tv", "TV Show", "strong", "guessit", "episode metadata")

    if has_clear_movie_year(name):
        return _video_classification("movies", "Movie", "strong", "Movie pattern", "single release year")
    if looks_like_tv_name(name):
        return _video_classification("tv", "TV Show", "strong", "Series pattern", "TV name")
    if _TV_EPISODE_HINT_RE.search(Path(str(name)).stem):
        return _video_classification("tv", "TV Show", "strong", "Episode pattern", "episode hint")
    if parsed_media_type == "movie" and (
        _AUTO_YEAR_TOKEN_RE.search(name) or _MOVIE_COLLECTION_RE.search(name)
    ):
        evidence = "movie collection" if _MOVIE_COLLECTION_RE.search(name) else "movie metadata"
        return _video_classification("movies", "Movie", "strong", "guessit", evidence)
    return _video_classification("misc", "Misc", "unknown", "No reliable signal")

def classify_video_name(
    name: str,
    folder_category: str = "",
    assume_movie_if_unknown: bool = False,
    *,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
    explicit_category_hint: str = "",
    explicit_itype_hint: str = "",
) -> str:
    """Compatibility wrapper returning only the display item type."""
    result = classify_video_name_result(
        name,
        folder_category,
        anime_lookup=anime_lookup,
        explicit_category_hint=explicit_category_hint,
        explicit_itype_hint=explicit_itype_hint,
    )
    if result.confidence == "unknown" and assume_movie_if_unknown:
        return "Movie"
    return result.itype

def _has_guessit_episode_metadata(name: str) -> bool:
    """Require both a compatible name shape and parsed episode metadata."""
    if not _GUESSIT_EPISODE_SHAPE_RE.search(Path(str(name)).stem):
        return False
    try:
        parsed = parse_release_name(name)
    except Exception:
        return False
    return bool(
        str(parsed.get("media_type") or "").strip().lower() == "tv"
        and (parsed.get("season_number") is not None or parsed.get("episode_number") is not None)
    )

def _matches_episode_pattern(
    name: str,
    *,
    anime_mode: bool = False,
    include_guessit: bool = True,
) -> bool:
    stem = Path(name).stem
    if _EXPLICIT_EPISODE_RE.search(stem):
        return True
    if _FRAMED_ABSOLUTE_EPISODE_RE.search(stem):
        return True
    bare_high_match = _BARE_HIGH_ABSOLUTE_EPISODE_RE.search(stem)
    if bare_high_match:
        prefix = _AUTO_ANIME_FANSUB_RE.sub("", stem[: bare_high_match.start()])
        if len(re.findall(r"[A-Za-z]{2,}", prefix)) >= 2:
            return True
    if include_guessit and _has_guessit_episode_metadata(name):
        return True
    if not anime_mode:
        return False
    if _ANIME_EXTRA_RE.search(stem):
        return False
    return bool(_ANIME_ABSOLUTE_EPISODE_RE.search(stem))

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
            # Older configs still use the pre-merge keys; map them onto the current ones.
            compatibility_map = {
                "ignore_non_video": "ignore_non_episode",
                "ignore_extras": "ignore_non_episode",
                "require_sxxexx": "require_episode",
            }
            for key, value in configured.items():
                normalized_key = compatibility_map.get(key, key)
                if normalized_key in rules:
                    rules[normalized_key] = bool(value)
    except Exception:
        pass

    if not rules.get("enabled", True):
        return ""

    name = path.name
    stem = path.stem
    has_sxxexx_pattern = bool(
        _TV_PACK_EPISODE_RE.search(stem)
        or _has_guessit_episode_metadata(name)
        or _looks_like_sports_event(name)
    )
    has_episode_pattern = bool(has_sxxexx_pattern or _looks_like_tv_episode_name(stem, anime_mode=True))
    has_source_token = bool(_TV_PACK_SOURCE_RE.search(stem))
    if rules.get("ignore_non_episode", True) and path.suffix.lower() not in video_extensions:
        return "TV season pack extra/non-video content"
    # Allow legit episodic releases whose episode title includes words like "Extras".
    if (
        rules.get("ignore_non_episode", True)
        and _is_tv_pack_extra_name(name)
        and not (has_episode_pattern and has_source_token)
    ):
        return "TV season pack extra/sample"
    if rules.get("require_source", True) and not has_source_token:
        return "TV episode missing media source token"
    # Resolution is descriptive metadata, not evidence that an upload should
    # be ignored. Valid SD and source-native releases may omit this token.
    # Anime-style numbering counts too (has_episode_pattern includes the S##E##,
    # guessit and sports-event signals).
    if rules.get("require_episode", True) and not has_episode_pattern:
        return "No recognized episode pattern"
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

def _has_tv_episode_like_video(video_names: list[str]) -> bool:
    """Return True if any single video leaf already looks like a TV episode."""
    return any(_looks_like_tv_episode_name(name) or _matches_episode_pattern(name) for name in video_names)

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
            "apps": _APP_FILE_EXTENSIONS,
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

def _resolve_series_like(
    entry: Path,
    video_files: Tuple[Path, ...],
    leaf_video_files: Tuple[Path, ...],
    episodic_files: Tuple[Path, ...],
    video_names: List[str],
    video_extensions: Set[str],
    entry_classification: VideoClassificationResult,
    tv_context: bool,
) -> bool:
    return bool(
        _matches_episode_pattern(entry.name)
        or episodic_files
        or entry_classification.category == "tv"
        or _has_related_video_files(video_names)
        or (entry.suffix.lower() in video_extensions and (looks_like_tv_name(entry.name) or tv_context))
        or _has_nested_tv_context(entry, video_files)
        or (
            entry.is_dir()
            and bool(video_files or leaf_video_files)
            and (looks_like_tv_name(entry.name) or tv_context)
        )
    )

def _build_explicit_video_state(
    entry: Path,
    *,
    category_hint: str,
    itype_hint: str,
    respect_explicit_hint: bool,
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
    explicit_selection = (
        _coerce_category_hint(category_hint) or _hint_category_from_itype(itype_hint)
        if respect_explicit_hint
        else ""
    )
    strict_tv_pack = _resolve_strict_tv_pack(
        entry, video_files, explicit_selection, folder_hint, category_hint, itype_hint
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
    entry_classification = classify_video_name_result(
        entry.name,
        folder_hint,
        anime_lookup=anime_lookup,
        explicit_category_hint=category_hint if respect_explicit_hint else "",
        explicit_itype_hint=itype_hint if respect_explicit_hint else "",
    )
    series_like = _resolve_series_like(
        entry, video_files, leaf_video_files, episodic_files, video_names, video_extensions,
        entry_classification, tv_context,
    )
    return _ExplicitVideoState(
        entry=entry,
        category_hint=category_hint,
        itype_hint=itype_hint,
        respect_explicit_hint=respect_explicit_hint,
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

def _resolve_explicit_movie(state: _ExplicitVideoState) -> Optional[ExplicitPathResolution]:
    if _explicit_is_anime(state):
        return None
    movie_name = state.entry.name
    if state.entry.is_dir() and len(state.video_files) == 1:
        movie_name = state.video_files[0].name
    classification = classify_video_name_result(
        movie_name,
        state.folder_hint,
        anime_lookup=state.anime_lookup,
        explicit_category_hint=state.category_hint if state.respect_explicit_hint else "",
        explicit_itype_hint=state.itype_hint if state.respect_explicit_hint else "",
    )
    # A year-named folder ("Show (2019)") holding episode files is not a movie.
    has_episodic_files = any(_matches_episode_pattern(path.name) for path in state.video_files)
    if classification.category != "movies" or state.strict_tv_pack or has_episodic_files:
        return None
    return ExplicitPathResolution(
        source_path=state.entry,
        category="movies",
        itype="Movie",
        detection_method=classification.method,
        queue_paths=(state.entry,),
        detection_confidence=classification.confidence,
        detection_evidence=classification.evidence,
    )

def _resolve_explicit_series(state: _ExplicitVideoState) -> ExplicitPathResolution:
    anime_status = _explicit_anime_status(state)
    is_anime = _explicit_is_anime(state)
    anime_method, anime_confidence, anime_evidence = (
        _anime_resolution_provenance(state, anime_status)
        if is_anime
        else ("", "unknown", ())
    )
    series_name = state.entry.name
    if state.entry.is_dir():
        representative = state.episode_queue_paths or state.video_files
        if representative:
            series_name = representative[0].name
    classification = classify_video_name_result(
        series_name,
        state.folder_hint,
        anime_lookup=state.anime_lookup,
        explicit_category_hint=state.category_hint if state.respect_explicit_hint else "",
        explicit_itype_hint=state.itype_hint if state.respect_explicit_hint else "",
    )
    detection_method = classification.method if classification.category == "tv" else "File scan"
    detection_confidence = classification.confidence if classification.category == "tv" else "strong"
    detection_evidence = classification.evidence if classification.category == "tv" else ()
    if state.entry.is_file() and state.entry.suffix.lower() not in state.video_extensions:
        return _resolve_explicit_series_non_video(
            state, is_anime, anime_method, anime_confidence, anime_evidence,
            detection_method, detection_confidence, detection_evidence,
        )

    if is_anime:
        return _resolve_explicit_series_anime(state, anime_method, anime_confidence, anime_evidence)

    return _resolve_explicit_series_tv(state, anime_status, detection_method, detection_confidence, detection_evidence)

def _resolve_explicit_series_tv(
    state: _ExplicitVideoState,
    anime_status: Optional[bool],
    detection_method: str,
    detection_confidence: str,
    detection_evidence: Tuple[str, ...],
) -> ExplicitPathResolution:
    """Resolve the non-anime TV branch of _resolve_explicit_series.

    Extracted to keep the parent's branching down.
    """
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
            detection_method=detection_method,
            queue_paths=() if reason else (state.entry,),
            ignored_paths=(IgnoredScanPath(path=state.entry, reason=reason),) if reason else (),
            override_note=override,
            detection_confidence=detection_confidence,
            detection_evidence=detection_evidence,
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
            detection_method=detection_method,
            queue_paths=(),
            ignored_paths=(IgnoredScanPath(path=state.entry, reason="Missing media source token"),),
            override_note=override,
            detection_confidence=detection_confidence,
            detection_evidence=detection_evidence,
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
        detection_method=detection_method,
        queue_paths=tv_queue_paths,
        ignored_paths=tv_ignored,
        override_note=override,
        detection_confidence=detection_confidence,
        detection_evidence=detection_evidence,
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
    respect_explicit_hint: bool = False,
    anime_lookup: Optional[Callable[[str], Optional[bool]]] = None,
    video_extensions: Optional[Set[str]] = None,
) -> ExplicitPathResolution:
    """Resolve one explicit path into a processing category with anime filtering."""
    video_exts = video_extensions or VIDEO_EXTENSIONS
    lookup = anime_lookup or _default_cached_anime_lookup
    raw_category_hint = str(category_hint or "").strip().lower()
    folder_hint = (
        ""
        if raw_category_hint in {"external", "auto"}
        else infer_entry_category_hint(entry, category_hint, itype_hint)
    )
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
        respect_explicit_hint=respect_explicit_hint,
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
    disc_leaf_files = _detect_video_disc_leaf_files(entry_path)
    if disc_leaf_files:
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

    classification = classify_video_name_result(
        name,
        anime_lookup=_default_cached_anime_lookup,
    )
    if classification.category != "misc":
        return classification.category

    return "misc"

def _detect_file_content_itype(
    name: str,
    entry_path: Path,
    folder_category: str,
    video_files: Tuple[Path, ...],
    lookup: Callable[[str], Optional[bool]],
    anime_lookup: Optional[Callable[[str], Optional[bool]]],
) -> str:
    ext = Path(name).suffix.lower()
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
    classification = classify_video_name_result(
        name,
        folder_category,
        anime_lookup=lookup,
    )
    if classification.category == "tv" or _has_tv_context(entry_path):
        return "TV Episode"
    return classification.itype

def _is_tv_show_directory(video_count: int, video_names: list[str]) -> bool:
    if video_count >= 1 and (
        _has_tv_episode_like_video(video_names)
        or any(_looks_like_source_bearing_tv_episode(video_name) for video_name in video_names)
    ):
        return True
    if video_count >= 2 and has_multi_file_episode_pattern(video_names):
        return True
    if video_count >= 2 and _has_related_video_files(video_names):
        return True
    return False

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
    audio_count = audiobook_count + music_count

    if _is_anime_directory(anime_status, folder_category, video_names):
        return "Anime"
    if _looks_like_sports_event(name):
        return "TV Show"
    if _is_tv_show_directory(video_count, video_names):
        return "TV Show"
    if audio_count > video_count:
        audio_category = classify_audio_folder(entry_path)
        if audio_category == "audiobooks":
            return "Audiobook"
        if audio_category == "music" and total > 0 and music_count / total >= 0.5:
            return "Music"
        return ""
    if ebook_count > video_count:
        return "Ebook"
    if video_count:
        classification = classify_video_name_result(
            name,
            folder_category,
            anime_lookup=lookup,
        )
        if classification.category == "tv":
            return "TV Show"
        if classification.category in {"anime", "movies"}:
            return classification.itype
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
    disc_leaf_files = _detect_video_disc_leaf_files(entry_path)
    if disc_leaf_files:
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
    classification = classify_video_name_result(
        name,
        folder_category,
        anime_lookup=lookup,
    )
    if classification.category == "tv":
        return "TV Show"
    if classification.category in {"anime", "movies"}:
        return classification.itype
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
    """Map the shared content type result to its canonical category."""
    itype = detect_auto_itype(entry, folder_category_hint, anime_lookup=anime_lookup)
    return category_from_itype(itype)

def _resolve_configured_scan_category(configured_category: str, entry: Path, folder_category_hint: str = "") -> str:
    """Return the effective queue category for a configured folder entry."""
    normalized = str(configured_category or "").strip().lower()
    if normalized in _AUTO_ROOT_CATEGORIES:
        return detect_auto_category(entry, "external")
    return normalized

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

