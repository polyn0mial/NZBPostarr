"""Shared release-name parsing backed by guessit with a small safe fallback."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict

from loguru import logger

_FALLBACK_TV_RE = re.compile(
    r"(?i)(?P<title>.+?)(?:[.\s_-]+)S(?P<season>\d{1,2})[.\s_-]*E(?P<episode>\d{1,3})"
    r"(?:[.\s_-]*E?(?P<episode_end>\d{1,3}))?"
)
_FALLBACK_TVX_RE = re.compile(
    r"(?i)(?P<title>.+?)(?:[.\s_-]+)(?P<season>\d{1,2})x(?P<episode>\d{1,3})"
    r"(?:-(?P<episode_end>\d{1,3}))?"
)
_FALLBACK_MOVIE_YEAR_RE = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")


def _clean_release_title(value: str) -> str:
    cleaned = re.sub(r"[._-]+", " ", Path(value).stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -._")
    return cleaned or Path(value).stem or value


def _fallback_parse_release_name(name: str) -> Dict[str, Any]:
    """Best-effort parser used when guessit is unavailable or fails."""
    stem = Path(name).stem
    for pattern in (_FALLBACK_TV_RE, _FALLBACK_TVX_RE):
        match = pattern.search(stem)
        if not match:
            continue
        episode_end = match.groupdict().get("episode_end")
        return {
            "parsed_title": _clean_release_title(match.group("title")),
            "media_type": "tv",
            "season_number": int(match.group("season")),
            "episode_number": int(match.group("episode")),
            "episode_end_number": int(episode_end) if episode_end else 0,
        }

    movie_year = _FALLBACK_MOVIE_YEAR_RE.search(stem)
    if movie_year:
        return {
            "parsed_title": _clean_release_title(stem[: movie_year.start()]),
            "media_type": "movie",
            "season_number": None,
            "episode_number": None,
            "episode_end_number": 0,
        }

    return {
        "parsed_title": _clean_release_title(stem),
        "media_type": "other",
        "season_number": None,
        "episode_number": None,
        "episode_end_number": 0,
    }


def parse_release_name(item_name: str) -> Dict[str, Any]:
    """Extract stable title, media type, season, and episode metadata."""
    name = (item_name or "").strip()
    if not name:
        return {
            "parsed_title": name,
            "media_type": "other",
            "season_number": None,
            "episode_number": None,
            "episode_end_number": 0,
        }

    normalized_path = name.replace("\\", "/")
    if "/" in normalized_path:
        name = normalized_path.rsplit("/", 1)[-1]

    try:
        from guessit import guessit as _guessit

        guess = _guessit(name)
    except Exception as exc:
        logger.debug(f"guessit unavailable for '{name}', using fallback parser: {exc}")
        return _fallback_parse_release_name(name)

    title = str(guess.get("title", name))
    guess_type = str(guess.get("type", "")).lower()
    season = guess.get("season")
    episode = guess.get("episode")
    episode_end = 0

    if isinstance(season, list):
        season = season[0] if season else None

    if isinstance(episode, list):
        episode_start = episode[0]
        episode_last = episode[-1]
        episode_end = episode_last if episode_last > episode_start else 0
        episode = episode_start

    if guess_type == "episode" or season is not None:
        media_type = "tv"
    elif guess_type == "movie":
        media_type = "movie"
    else:
        media_type = "other"

    return {
        "parsed_title": title,
        "media_type": media_type,
        "season_number": int(season) if season is not None else None,
        "episode_number": int(episode) if episode is not None else None,
        "episode_end_number": int(episode_end),
    }
