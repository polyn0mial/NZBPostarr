"""Content detection from files on disk: disc, audio, books, apps and the display itype."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, Optional, Tuple

from core.media import (
    AUDIOBOOK_EXTENSIONS,
    DISC_STRUCTURE_DIRS,
    EBOOK_EXTENSIONS,
    EXTENSION_FIRST_APP,
    EXTENSION_FIRST_AUDIOBOOK,
    EXTENSION_FIRST_EBOOK,
    EXTENSION_FIRST_MUSIC,
    EXTENSION_FIRST_VIDEO,
    MUSIC_EXTENSIONS,
    VIDEO_EXTENSIONS,
    category_for_itype,
)
from logic.classify.patterns import has_multi_file_episode_pattern
from logic.classify.anime import _lookup_anime_status, _series_signature, cached_lookup
from logic.classify.hints import (
    _coerce_category_hint,
    _entry_hint_text,
    infer_entry_category_hint,
    infer_folder_category_hint,
)
from logic.classify.names import (
    _has_tv_context,
    _looks_like_source_bearing_tv_episode,
    _looks_like_sports_event,
    classify_video_name,
    classify_video_name_result,
    looks_like_generic_tv_season_folder,
    looks_like_tv_name,
)
from logic.classify.patterns import (
    ANIME_BONUS_RE,
    _AMBIGUOUS_TRACK_RE,
    _ANIME_EXTRA_RE,
    _AUDIOBOOK_CHAPTER_RE,
    _AUDIOBOOK_HINT_RE,
    _EBOOK_HINT_RE,
    _MUSIC_RELEASE_HINT_RE,
)
from logic.classify.tv_packs import _has_tv_episode_like_video
from logic.classify.walk import _iter_leaf_files, _iter_video_candidates


def is_anime_bonus_name(name: str) -> bool:
    """True for NCOP/NCED/creditless anime extras."""
    return bool(ANIME_BONUS_RE.search(str(name or "").lower()))

def _directory_extension_first_category(entry: Path) -> str:
    """Classify by real file extensions inside a directory before any name/token inference."""
    if not entry.is_dir():
        return ""
    counts = {"audiobooks": 0, "music": 0, "ebooks": 0, "video": 0}
    try:
        for _root, _dirs, filenames in os.walk(str(entry)):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in EXTENSION_FIRST_VIDEO:
                    counts["video"] += 1
                elif ext in EXTENSION_FIRST_AUDIOBOOK:
                    counts["audiobooks"] += 1
                elif ext in EXTENSION_FIRST_MUSIC:
                    counts["music"] += 1
                elif ext in EXTENSION_FIRST_EBOOK:
                    counts["ebooks"] += 1
    except OSError:
        return ""
    if counts["audiobooks"] == 0 and counts["music"] == 0 and counts["ebooks"] == 0:
        return ""
    audio_count = counts["audiobooks"] + counts["music"]
    if audio_count > counts["video"]:
        return classify_audio_folder(entry)
    if counts["ebooks"] > counts["video"] and counts["ebooks"] >= audio_count:
        return "books"
    return ""

def classify_standalone_file_category(entry: Path) -> str:
    """Classify a loose file by filename + extension only, without parent-path hints."""
    # DISC has absolute priority over ebook/music/audiobook extension detection.
    # A Blu-ray or DVD folder may contain companion PDFs - it is still disc, not ebooks.
    if has_video_disc_structure(entry):
        return "disc"
    ext_first_category = _directory_extension_first_category(entry)
    if ext_first_category:
        return ext_first_category
    if entry.suffix.lower() in EXTENSION_FIRST_EBOOK:
        return "books"
    return ""

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
        elif ext in EXTENSION_FIRST_APP:
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

def _detect_video_disc_leaf_files(entry: Path) -> Tuple[Path, ...]:
    """Return leaves only when they contain an actual DVD/Blu-ray structure."""
    leaf_files = _iter_leaf_files(entry)
    if not leaf_files:
        return ()

    structured_suffixes = {".bdmv", ".ifo", ".bup", ".vob", ".m2ts", ".mpls", ".clpi", ".ssif"}
    if any(
        path.suffix.lower() in structured_suffixes
        and any(part.lower() in DISC_STRUCTURE_DIRS for part in path.parts)
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

def detect_external_category(name: str, entry_path: Path) -> str:
    """Auto-detect queue category for an external folder/file based on naming patterns."""
    if is_anime_bonus_name(name) or cached_lookup(str(name or "")) is True:
        return "anime"
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
        cached_lookup,
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
        anime_lookup=cached_lookup,
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
    lookup = anime_lookup or cached_lookup
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
    if is_anime_bonus_name(entry.name):
        return "anime"
    itype = detect_auto_itype(entry, folder_category_hint, anime_lookup=anime_lookup)
    return category_for_itype(itype)
