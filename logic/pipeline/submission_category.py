"""Indexer submission category: map a routing category and item type onto indexer vocabulary."""

from __future__ import annotations

import os
import re
from pathlib import Path

from core.media import (
    AUDIOBOOK_EXTENSIONS,
    EBOOK_EXTENSIONS,
    MUSIC_EXTENSIONS,
    VIDEO_EXTENSIONS,
    normalize_category,
    processing_itype,
)
from logic.classify.names import has_clear_movie_year, looks_like_tv_name
from logic.classify.patterns import has_multi_file_episode_pattern
from logic.classify.tv_packs import is_season_pack
from logic.pipeline.prepare import _APP_EXTENSIONS


def _normalize_processing_type(raw: str) -> str:
    """Normalize DB/display item types for strict submission-category decisions."""
    key = str(raw or "").strip().lower()
    special_types = {
        "tv episode": "tv_episode",
        "tv show": "tv",
        "tv pack": "tv",
        "season pack": "tv",
        "movie pack": "movie",
    }
    if key in special_types:
        return special_types[key]
    normalized = normalize_category(key)
    return "movie" if normalized == "movies" else normalized


def _scan_release_media(path: Path) -> tuple[dict[str, int], list[str]]:
    """Return media-extension counts and discovered video filenames for a release."""
    counts = {"video": 0, "music": 0, "book": 0, "app": 0, "audiobook": 0}
    video_names: list[str] = []

    def _count_file(candidate: Path) -> None:
        suffix = candidate.suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            counts["video"] += 1
            video_names.append(candidate.name)
        elif suffix in MUSIC_EXTENSIONS:
            counts["music"] += 1
        elif suffix in AUDIOBOOK_EXTENSIONS:
            counts["audiobook"] += 1
        elif suffix in EBOOK_EXTENSIONS:
            counts["book"] += 1
        elif suffix in _APP_EXTENSIONS:
            counts["app"] += 1

    if path.is_file():
        _count_file(path)
        return counts, video_names

    try:
        for root, _dirs, files in os.walk(path):
            for file_name in files:
                if file_name.startswith("."):
                    continue
                _count_file(Path(root) / file_name)
    except OSError:
        return counts, video_names

    return counts, video_names


def _collect_release_keywords(path: Path, itype: str) -> set[str]:
    """Extract lowercase release keywords from the path and item type."""
    values = [path.name, path.stem if path.suffix else "", itype]
    if path.parent and path.parent.name:
        values.append(path.parent.name)

    keywords: set[str] = set()
    for value in values:
        for token in re.split(r"[^a-zA-Z0-9]+", str(value or "").lower()):
            if token:
                keywords.add(token)
    return keywords


def _is_tv_pack_release(
    path: Path,
    normalized_category: str,
    normalized_type: str,
    keywords: set[str],
    video_names: list[str],
) -> bool:
    """Return True when the release should use the TV-pack submission category."""
    if normalized_type == "tv_pack":
        return True
    if normalized_category == "tv" and path.is_dir():
        return True
    if "season" in keywords or "complete" in keywords:
        return path.is_dir() or len(video_names) > 1
    return len(video_names) > 1 and has_multi_file_episode_pattern(video_names)


def _is_movie_pack_release(
    path: Path,
    normalized_category: str,
    normalized_type: str,
    keywords: set[str],
    video_names: list[str],
) -> bool:
    """Return True when the release should use the movie-pack submission category."""
    if normalized_type == "movie_pack":
        return True
    if normalized_category not in {"movies", "misc"} and normalized_type not in {"movie", "movie_pack"}:
        return False
    if not path.is_dir():
        return False

    pack_keywords = {"anthology", "boxset", "collection", "complete", "duology", "pack", "tetralogy", "trilogy"}
    if keywords & pack_keywords:
        return True

    movie_like_videos = [name for name in video_names if has_clear_movie_year(name)]
    return len(movie_like_videos) >= 2 and not has_multi_file_episode_pattern(movie_like_videos)


def _submission_category_label(category: str) -> str:
    """Return the human-readable category label used in logs."""
    return {
        "movies": "Movie",
        "tv": "TV",
        "movie": "Movie",
        "anime": "Anime",
        "disc": "DISC",
        "music": "Music",
        "audiobooks": "Audiobooks",
        "books": "Books",
        "apps": "Apps",
        "misc": "Misc",
    }.get(category, category or "Unknown")


def _resolve_ambiguous_submission_category(path: Path, normalized_category: str, normalized_itype: str) -> str:
    """Disambiguate a tv/movies/misc category against cached anime status.

    Extracted from _resolve_submission_category to keep its own branching down.
    """
    if normalized_itype in {"anime", "music", "audiobooks", "books", "apps"}:
        return normalized_itype

    from logic.classify.anime import get_cached

    candidates = [path.name]
    if path.suffix:
        candidates.append(path.stem)
    candidates.extend(parent.name for parent in path.parents[:2] if parent.name)

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            if get_cached(candidate) is True:
                return "anime"
        except Exception:
            return normalized_category
    else:
        if normalized_itype in {"tv", "tv_episode"} and normalized_category != "movies":
            return "tv"
        if normalized_itype == "movie" and normalized_category != "tv":
            return "movies"

    return normalized_category


def _resolve_submission_category(path: Path, category: str, itype: str) -> str:
    """Validate and preserve the detected category used for indexer submission."""
    normalized_category = normalize_category(category)
    normalized_itype = _normalize_processing_type(itype)
    media_counts, _video_names = _scan_release_media(path)

    if normalized_category == "disc":
        if normalized_itype in {"anime", "tv", "tv_episode", "movie"}:
            normalized_category = {
                "anime": "anime",
                "tv": "tv",
                "tv_episode": "tv",
                "movie": "movies",
            }[normalized_itype]
        elif looks_like_tv_name(path.name) or any(
            re.search(r"S\d{1,2}[.\s_-]*E\d{1,3}", name, re.IGNORECASE) for name in _video_names
        ):
            normalized_category = "tv"
        else:
            normalized_category = "movies"

    allowed_categories = {"movies", "tv", "anime", "music", "audiobooks", "books", "apps", "misc"}
    if normalized_category not in allowed_categories:
        if normalized_itype in allowed_categories:
            raise ValueError(
                f"explicit category '{category}' is invalid; detected type '{itype}' cannot override the configured category"
            )
        raise ValueError(f"explicit category '{category}' is invalid")

    resolved_category = normalized_category
    if normalized_category in {"tv", "movies", "misc"}:
        resolved_category = _resolve_ambiguous_submission_category(path, normalized_category, normalized_itype)

    has_video = media_counts["video"] > 0
    if resolved_category == "misc" and has_video:
        raise ValueError("video content cannot be submitted as Misc")
    if resolved_category == "books" and media_counts["book"] == 0 and media_counts["audiobook"] == 0:
        raise ValueError("book category requires book metadata or book file types")
    if (
        resolved_category == "audiobooks"
        and media_counts["audiobook"] == 0
        and media_counts["music"] == 0
    ):
        raise ValueError("audiobook category requires audio file types")
    if resolved_category == "apps" and media_counts["app"] == 0:
        raise ValueError("apps category requires application/archive file types")
    if resolved_category == "music" and media_counts["music"] == 0:
        raise ValueError("music category requires audio file types")

    return resolved_category


def _processing_db_type(path: Path, category: str) -> str:
    """Map queue routing categories to the DB-facing item label."""
    return processing_itype(category, season_pack=category == "tv" and is_season_pack(path))
