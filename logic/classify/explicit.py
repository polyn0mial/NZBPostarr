"""Resolve an explicitly selected path into its category, itype and upload paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

from core.utils import AUDIOBOOK_EXTENSIONS, EBOOK_EXTENSIONS, MUSIC_EXTENSIONS, VIDEO_EXTENSIONS
from logic.classify.anime import _lookup_anime_status, cached_lookup
from logic.classify.content import (
    _APP_FILE_EXTENSIONS,
    _detect_video_disc_leaf_files,
    _disc_base_category,
    _has_nested_tv_context,
    _has_related_video_files,
    _movie_fallback_itype,
    _non_video_media_category,
)
from logic.classify.hints import _coerce_category_hint, _hint_category_from_itype, infer_entry_category_hint
from logic.classify.names import (
    _has_tv_context,
    _looks_like_tv_episode_name,
    _matches_episode_pattern,
    _source_less_video_category,
    classify_video_name_result,
    looks_like_tv_name,
    VideoClassificationResult,
)
from logic.classify.patterns import SOURCE_TOKEN_RE
from logic.classify.tv_packs import (
    _needs_source_token,
    _resolve_strict_tv_pack,
    _split_tv_video_files,
    _tv_pack_episode_rejection_reason,
    IgnoredScanPath,
)
from logic.classify.walk import _iter_leaf_files, _iter_video_candidates


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
        and not SOURCE_TOKEN_RE.search(entry.stem)
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
        and not SOURCE_TOKEN_RE.search(state.entry.stem)
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
            and not SOURCE_TOKEN_RE.search(state.entry.stem)
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
    lookup = anime_lookup or cached_lookup
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
