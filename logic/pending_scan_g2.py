# Auto-split from pending_scan.py - verbatim symbol bodies, synthesized imports.

from logic.pending_scan_base import (
    AUDIOBOOK_EXTENSIONS, Any, Callable, EBOOK_EXTENSIONS, List, MUSIC_EXTENSIONS, Optional, Path, Set, Tuple, VIDEO_EXTENSIONS, _AMBIGUOUS_TRACK_RE,
    _ANIME_SEASONAL_FOLDER_RE, _APP_FILE_EXTENSIONS, _AUDIOBOOK_CHAPTER_RE, _AUDIOBOOK_HINT_RE, _AUTO_YEAR_TOKEN_RE, _DISC_STRUCTURE_DIRS,
    _EBOOK_HINT_RE, _GENERIC_ANIME_FOLDER_RE, _MUSIC_RELEASE_HINT_RE, dataclass, re,
)
from logic.pending_scan_g1 import (ExplicitPathResolution, IgnoredScanPath, _coerce_category_hint, _entry_hint_text, _hint_category_from_itype, _iter_leaf_files, _iter_video_candidates, _iter_video_files, _normalize_lookup_title, _series_signature, get_configured_category_folders, looks_like_generic_tv_season_folder, looks_like_tv_name)  # noqa: F401

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

def classify_audio_folder(
    entry: Path,
    leaf_files: Optional[Tuple[Path, ...]] = None,
) -> str:
    """Classify audio leaves as audiobooks, music, or unresolved."""
    leaves = leaf_files if leaf_files is not None else _iter_leaf_files(entry)
    audio_files = tuple(
        path for path in leaves if path.suffix.lower() in AUDIOBOOK_EXTENSIONS | MUSIC_EXTENSIONS
    )
    if not audio_files:
        return ""

    hint_text = " ".join((_entry_hint_text(entry), *(path.stem for path in audio_files)))
    if any(path.suffix.lower() in AUDIOBOOK_EXTENSIONS for path in audio_files):
        return "audiobooks"
    if _AUDIOBOOK_HINT_RE.search(hint_text):
        return "audiobooks"
    if _MUSIC_RELEASE_HINT_RE.search(hint_text):
        return "music"

    chapter_count = sum(bool(_AUDIOBOOK_CHAPTER_RE.search(path.stem)) for path in audio_files)
    if chapter_count >= 2:
        return "audiobooks"

    track_count = sum(bool(_AMBIGUOUS_TRACK_RE.search(path.stem)) for path in audio_files)
    if len(audio_files) >= 2 and track_count >= 2:
        return ""
    return "music"

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
        elif ext in _APP_FILE_EXTENSIONS:
            app_count += 1
            known_count += 1

    audio_count = audiobook_count + music_count
    if audio_count > video_count:
        audio_category = classify_audio_folder(entry, leaves)
        if audio_category:
            return audio_category

    if ebook_count > video_count:
        hint_text = _entry_hint_text(entry)
        if _EBOOK_HINT_RE.search(hint_text) or ebook_count >= max(1, audio_count):
            return "books"

    if app_count > video_count and known_count and app_count / known_count >= 0.5:
        return "apps"

    return ""

def _has_nested_tv_context(entry: Path, video_files: Tuple[Path, ...]) -> bool:
    """Return True when nested season and episode folders identify a TV leaf."""
    if not entry.is_dir():
        return False

    episode_folder_re = re.compile(r"^(?:episode|ep|e)\s*\d{1,4}$", re.IGNORECASE)
    for video_file in video_files:
        try:
            relative_parts = video_file.relative_to(entry).parts[:-1]
        except ValueError:
            continue
        normalized_parts = [re.sub(r"[^a-z0-9]+", " ", part.lower()).strip() for part in relative_parts]
        has_season = any(looks_like_generic_tv_season_folder(part) for part in relative_parts)
        has_episode = any(episode_folder_re.fullmatch(part) for part in normalized_parts)
        if has_season and has_episode:
            return True
    return False

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
        raw_text = str(value or "").strip()
        if not raw_text or not _normalize_lookup_title(raw_text):
            return
        if raw_text not in candidates:
            candidates.append(raw_text)

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
    """Return detector queries derived from a release and its video leaves."""
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
            if _AUTO_YEAR_TOKEN_RE.search(candidate):
                return False
    return False if saw_false else None

def _detect_video_disc_leaf_files(entry: Path) -> Tuple[Path, ...]:
    """Return leaves only when they contain an actual DVD/Blu-ray structure."""
    leaf_files = _iter_leaf_files(entry)
    if not leaf_files:
        return ()

    structured_suffixes = {".bdmv", ".ifo", ".bup", ".vob", ".m2ts", ".mpls", ".clpi", ".ssif"}
    if any(
        path.suffix.lower() in structured_suffixes
        and any(part.lower() in _DISC_STRUCTURE_DIRS for part in path.parts)
        for path in leaf_files
    ):
        return leaf_files

    suffixes = {path.suffix.lower() for path in leaf_files}
    has_dvd_controls = bool(suffixes & {".ifo", ".bup"})
    has_dvd_stream = ".vob" in suffixes
    has_bluray_controls = bool(suffixes & {".mpls", ".clpi"})
    has_bluray_stream = bool(suffixes & {".m2ts", ".ssif"})
    if (has_dvd_controls and has_dvd_stream) or (has_bluray_controls and has_bluray_stream):
        return leaf_files
    return ()

def has_video_disc_structure(entry: Path) -> bool:
    """Return True only for an inspectable DVD or Blu-ray filesystem structure."""
    return bool(_detect_video_disc_leaf_files(entry))

def _disc_base_category(entry: Path, folder_hint: str, anime_status: Optional[bool]) -> tuple[str, str]:
    normalized_hint = _coerce_category_hint(folder_hint)
    if normalized_hint == "apps":
        return "apps", "App"
    if anime_status is True:
        return "anime", "Anime"

    if normalized_hint in {"tv", "television", "series", "shows", "show"}:
        return "tv", "TV Show"
    if looks_like_generic_tv_season_folder(entry.name) or looks_like_tv_name(entry.name):
        return "tv", "TV Show"
    return "movies", "Movie"

@dataclass(frozen=True)
class _ExplicitVideoState:
    entry: Path
    category_hint: str
    itype_hint: str
    respect_explicit_hint: bool
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

def _resolve_strict_tv_pack(
    entry: Path,
    video_files: Tuple[Path, ...],
    explicit_selection: str,
    folder_hint: str,
    category_hint: str,
    itype_hint: str,
) -> bool:
    if not (entry.is_dir() and video_files):
        return False
    if explicit_selection:
        return explicit_selection == "tv"
    return bool(
        looks_like_tv_name(entry.name)
        or folder_hint == "tv"
        or _coerce_category_hint(category_hint) == "tv"
        or _hint_category_from_itype(itype_hint) == "tv"
    )

def _explicit_anime_status(state: _ExplicitVideoState) -> Optional[bool]:
    return _lookup_anime_status(
        state.entry,
        state.video_files,
        state.anime_lookup,
    )

def _explicit_is_anime(state: _ExplicitVideoState) -> bool:
    """Use detector evidence first and a configured anime hint only when unknown."""
    if state.respect_explicit_hint:
        selected_category = _coerce_category_hint(state.category_hint) or _hint_category_from_itype(state.itype_hint)
        if selected_category:
            return selected_category == "anime"

    anime_status = _explicit_anime_status(state)
    if anime_status is not None:
        return anime_status
    return _coerce_category_hint(state.folder_hint or state.category_hint) == "anime"

def _resolve_explicit_disc(state: _ExplicitVideoState) -> Optional[ExplicitPathResolution]:
    disc_leaf_files = _detect_video_disc_leaf_files(state.entry)
    if not disc_leaf_files:
        return None

    anime_status = _explicit_anime_status(state)
    if state.respect_explicit_hint:
        selected_category = _coerce_category_hint(state.category_hint) or _hint_category_from_itype(state.itype_hint)
        if selected_category:
            anime_status = selected_category == "anime"
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

def _anime_override_note(state: _ExplicitVideoState) -> str:
    raw_hint = str(state.category_hint or state.folder_hint).strip()
    return f"Jikan match overrode {raw_hint.upper()} hint" if raw_hint.lower() not in {"", "anime"} else ""

def _anime_resolution_provenance(
    state: _ExplicitVideoState,
    anime_status: Optional[bool],
) -> tuple[str, str, Tuple[str, ...]]:
    if state.respect_explicit_hint and _coerce_category_hint(state.category_hint) == "anime":
        return "Explicit category", "confirmed", ("anime",)
    if anime_status is True:
        return "Jikan match", "confirmed", ("confirmed anime",)
    return "Configured folder", "strong", ("anime folder",)

def _resolve_explicit_series_non_video(
    state: _ExplicitVideoState,
    is_anime: bool,
    anime_method: str,
    anime_confidence: str,
    anime_evidence: Tuple[str, ...],
    detection_method: str,
    detection_confidence: str,
    detection_evidence: Tuple[str, ...],
) -> ExplicitPathResolution:
    """Resolve the no-recognized-video-extension branch of _resolve_explicit_series.

    Extracted to keep the parent's branching down.
    """
    return ExplicitPathResolution(
        source_path=state.entry,
        category="anime" if is_anime else "tv",
        itype="Anime" if is_anime else "TV Episode",
        detection_method=anime_method if is_anime else detection_method,
        queue_paths=(),
        ignored_paths=(IgnoredScanPath(path=state.entry, reason="No recognized video extension"),),
        detection_confidence=anime_confidence if is_anime else detection_confidence,
        detection_evidence=anime_evidence if is_anime else detection_evidence,
    )

def _resolve_explicit_series_anime(
    state: _ExplicitVideoState,
    anime_method: str,
    anime_confidence: str,
    anime_evidence: Tuple[str, ...],
) -> ExplicitPathResolution:
    """Resolve the is_anime branch of _resolve_explicit_series.

    Extracted to keep the parent's branching down.
    """
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
        detection_method=anime_method,
        queue_paths=anime_selected_paths,
        ignored_paths=anime_rejected_paths,
        override_note=_anime_override_note(state),
        detection_confidence=anime_confidence,
        detection_evidence=anime_evidence,
    )

def _resolve_explicit_anime(state: _ExplicitVideoState) -> Optional[ExplicitPathResolution]:
    if not _explicit_is_anime(state):
        return None
    anime_status = _explicit_anime_status(state)
    detection_method, detection_confidence, detection_evidence = _anime_resolution_provenance(
        state,
        anime_status,
    )
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
        detection_method=detection_method,
        queue_paths=anime_selected_paths,
        ignored_paths=anime_rejected_paths,
        override_note=_anime_override_note(state),
        detection_confidence=detection_confidence,
        detection_evidence=detection_evidence,
    )

def _collect_tv_episode_paths(entry: Path, video_extensions: Set[str]) -> Tuple[Path, ...]:
    if entry.is_dir():
        return tuple(sorted(_iter_video_files(entry, video_extensions), key=lambda path: str(path).casefold()))
    if entry.is_file() and entry.suffix.lower() in video_extensions:
        return (entry,)
    return ()

