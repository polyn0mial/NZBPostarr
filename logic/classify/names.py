"""Release-name classification: TV, anime, movie or misc."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.release_name import parse_release_name
from core.utils import VIDEO_EXTENSIONS
from logic.classify.anime import _lookup_anime_status
from logic.classify.hints import _coerce_category_hint, _hint_category_from_itype
from logic.classify.patterns import (
    _ANIME_ABSOLUTE_EPISODE_RE,
    _ANIME_EXTRA_RE,
    _AUTO_ANIME_FANSUB_RE,
    _AUTO_COMPLETE_SERIES_RANGE_RE,
    _AUTO_MOVIE_PATTERNS,
    _AUTO_TV_PATTERNS,
    _AUTO_YEAR_RANGE_RE,
    _AUTO_YEAR_TOKEN_RE,
    _BARE_HIGH_ABSOLUTE_EPISODE_RE,
    _COMPLETE_MINISERIES_RE,
    _DATE_EPISODE_RE,
    _EXPLICIT_EPISODE_RE,
    _FRAMED_ABSOLUTE_EPISODE_RE,
    _GENERIC_TV_SEASON_FOLDER_RE,
    _GUESSIT_EPISODE_SHAPE_RE,
    _MOVIE_COLLECTION_RE,
    _SPORTS_EVENT_CONTEXT_RE,
    _SPORTS_LEAGUE_RE,
    _STREAMING_EPISODE_SOURCE_RE,
    _TRAILING_RELEASE_GROUP_YEAR_RE,
    _TV_EPISODE_HINT_RE,
    _TV_PART_EPISODE_RE,
    SOURCE_TOKEN_RE,
)
from logic.classify.walk import _iter_video_candidates


@dataclass(frozen=True)
class VideoClassificationResult:
    """Canonical name-level classification with evidence strength."""

    category: str
    itype: str
    confidence: str
    method: str
    evidence: Tuple[str, ...] = ()

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
        return bool(len(title_words) >= 2 and SOURCE_TOKEN_RE.search(raw_name))
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
    return bool(SOURCE_TOKEN_RE.search(text) and (_looks_like_tv_episode_name(text) or looks_like_tv_name(text)))

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
