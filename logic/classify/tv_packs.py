"""TV season-pack rules: episode validation, ignore rules and the pack-folder predicate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Set, Tuple

from logic.classify.hints import _coerce_category_hint, _hint_category_from_itype
from logic.classify.names import (
    _has_guessit_episode_metadata,
    _looks_like_sports_event,
    _looks_like_tv_episode_name,
    _matches_episode_pattern,
    has_clear_movie_year,
    looks_like_generic_tv_season_folder,
    looks_like_tv_name,
)
from logic.classify.patterns import (
    _TV_PACK_EPISODE_RE,
    _TV_PACK_EXTRAS_WORD_RE,
    _TV_PACK_HARD_EXTRA_RE,
    SOURCE_TOKEN_RE,
)
from logic.classify.walk import _iter_video_files


DEFAULT_TV_PACK_IGNORE_RULES: dict[str, bool] = {
    "enabled": True,
    "ignore_non_episode": True,
    "require_episode": True,
    "require_resolution": False,
    "require_source": True,
}

TV_PACK_IGNORE_RULE_LABELS: dict[str, str] = {
    "ignore_non_episode": "Non-episode files",
    "require_episode": "Files without recognized episode numbering",
    "require_resolution": "Files without quality/format such as NTSC/PAL/480i/480p/576i/576p/720p/1080p/2160p",
    "require_source": "Files without a media source token such as WEB-DL, WEBRip, BluRay, REMUX, HDTV, DVDRip, VHSRip",
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
    "preview",
    "pv",
    "proof",
    "screen",
    "screens",
    "subs",
    "subtitles",
    "extras",
    "featurette",
    "featurettes",
    "trailer",
    "teaser",
    "special",
    "specials",
    "ova",
    "oad",
    "op",
    "ed",
    "ncop",
    "nced",
)

@dataclass(frozen=True)
class IgnoredScanPath:
    """A discovered path that was intentionally excluded from processing."""

    path: Path
    reason: str

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

def _collect_tv_episode_paths(entry: Path, video_extensions: Set[str]) -> Tuple[Path, ...]:
    if entry.is_dir():
        return tuple(sorted(_iter_video_files(entry, video_extensions), key=lambda path: str(path).casefold()))
    if entry.is_file() and entry.suffix.lower() in video_extensions:
        return (entry,)
    return ()

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
        ) and SOURCE_TOKEN_RE.search(text):
            return False
        return not bool(_TV_PACK_EPISODE_RE.search(stem) and SOURCE_TOKEN_RE.search(stem))
    return False

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
    has_source_token = bool(SOURCE_TOKEN_RE.search(stem))
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


_SEASON_PACK_NAME_RE = re.compile(r"(?:^|[^a-z0-9])s\d{1,2}(?:[^a-z0-9]|$)")


def has_season_pack_name(name: str) -> bool:
    """Return True when a folder name itself looks like a season pack (sNN, season, complete)."""
    folder_name = name.lower()
    return bool(_SEASON_PACK_NAME_RE.search(folder_name)) or any(
        token in folder_name for token in ("season", "complete")
    )


def is_season_pack_folder(path: Path, episode_paths: list[Path], *, require_source_token: bool) -> bool:
    """Return True for a first-level season-pack folder inferred from its selected episode paths.

    require_source_token=True is the queue policy: the folder name must carry a real source token
    (SOURCE_TOKEN_RE) and a generic season folder such as "Season 1" or "S01" is rejected.
    require_source_token=False is the processing policy: the season-pack name alone is enough.
    """
    if not path.is_dir() or len(episode_paths) < 2:
        return False
    folder_name = path.name.lower()
    if require_source_token:
        if not SOURCE_TOKEN_RE.search(folder_name):
            return False
        if looks_like_generic_tv_season_folder(folder_name):
            return False
    return has_season_pack_name(folder_name)
