"""Pending snapshot scanning, classification, and filtering helpers."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from core import database
from core.config import get_config
from core.utils import (
    compute_size_uncached,
    log_backend_timing,
    should_skip_file,
)
from logic.pending_scan import (
    anime_lookup_candidates,
    begin_scan_cache,
    category_from_itype,
    classify_audio_folder,
    classify_video_name as shared_classify_video_name,
    classify_video_name_result,
    detect_content_itype as shared_detect_content_itype,
    detect_external_category as shared_detect_external_category,
    end_scan_cache,
    get_configured_category_folders,
    has_video_disc_structure,
    relative_key,
    resolve_explicit_path,
)
from logic.queue_metrics import (
    count_pending_indexer_slots,
    incomplete_pending_indexer_ids,
    log_queue_update,
    required_pending_indexer_ids,
)


def stamp_skip_flags(result: Dict[str, Any], skip_config: Optional[Dict[str, Any]]) -> None:
    """Post-process scan results to add 'skipped' flags to all items."""
    if not skip_config or not skip_config.get("enabled"):
        return

    for category_key, category_items in result.items():
        if not isinstance(category_items, list):
            continue

        if category_key == "external":
            for group in category_items:
                for item in group.get("items", []):
                    item["skipped"] = should_skip_file(item["name"], "external", skip_config)
        elif category_key != "tv":
            for item in category_items:
                item["skipped"] = should_skip_file(item["name"], category_key, skip_config)


def _has_filepart_path(path_str: str) -> bool:
    """Return True if path_str has .filepart counterpart (file) or contains any .filepart (dir)."""
    try:
        if os.path.isdir(path_str):
            for _root, _dirs, files in os.walk(path_str):
                if any(f.endswith(".filepart") for f in files):
                    return True
            return False
        return os.path.exists(path_str + ".filepart")
    except OSError:
        return False


def stamp_filepart_flags(result: Dict[str, Any]) -> None:
    """Flag items still being transferred. A flagged folder flags all its children."""

    def _flag_item(item: Dict[str, Any], inherited: bool = False) -> None:
        path = item.get("path", "")
        name = item.get("name", "")

        if inherited:
            own = False
        elif name.endswith(".filepart"):
            own = True
        elif path:
            # File: check the direct .filepart counterpart only.
            # Dir (folder pack): os.walk finds any nested .filepart at any depth.
            own = _has_filepart_path(path)
        else:
            own = False

        item["has_filepart"] = own or inherited
        propagate = item["has_filepart"]

        for child in item.get("children", []):
            _flag_item(child, inherited=propagate)
        for child in item.get("files", []):
            _flag_item(child, inherited=propagate)

    for category_key, category_items in result.items():
        if not isinstance(category_items, list):
            continue
        if category_key == "external":
            for group in category_items:
                for item in group.get("items", []):
                    _flag_item(item)
        else:
            for item in category_items:
                _flag_item(item)


def _normalize_dashboard_lookup_values(values: Any) -> Set[str]:
    normalized: Set[str] = set()
    if values is None:
        return normalized
    if isinstance(values, (str, Path)):
        raw_values = [values]
    else:
        try:
            raw_values = list(values)
        except TypeError:
            raw_values = [values]
    for raw in raw_values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        normalized.add(text.casefold())
        normalized.add(text.replace("\\", "/").casefold())
    return normalized


def _coerce_int(value: Any) -> Optional[int]:
    """Best-effort int conversion: some legacy filesize records were stored
    as text, so a raw `==` against a real int would silently never match."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lookup_upload_map_indexers(
    upload_map: Dict[str, Set[str]],
    *candidate_values: Any,
    filesize_by_indexer: Optional[Dict[str, Dict[str, int]]] = None,
    current_size: Optional[int] = None,
) -> Set[str]:
    """Merge indexer matches across exact keys plus basename/path variants."""
    if not upload_map:
        return set()

    matches: Set[str] = set()
    seen: Set[str] = set()
    candidates: List[str] = []

    for raw in candidate_values:
        if raw is None:
            continue
        text = str(raw).strip()
        if not text:
            continue
        variants = [text, text.replace("\\", "/")]
        for variant in variants:
            if variant not in seen:
                seen.add(variant)
                candidates.append(variant)
            if "/" in variant:
                basename = variant.rsplit("/", 1)[-1]
                if basename and basename not in seen:
                    seen.add(basename)
                    candidates.append(basename)

    for candidate in candidates:
        idx_ids = upload_map.get(candidate, set())
        if current_size is not None and filesize_by_indexer:
            sizes = filesize_by_indexer.get(candidate)
            if sizes:
                # Only count an indexer's success for this candidate if its
                # stored filesize (when known) still matches what is on disk:
                # a locally replaced file/folder with a new size must not
                # inherit a stale indexer's completed status.
                idx_ids = {
                    idx_id for idx_id in idx_ids
                    if idx_id not in sizes or _coerce_int(sizes.get(idx_id)) == current_size
                }
        matches.update(idx_ids)

    return matches


_EPISODE_TAG_RE = re.compile(r"S\d{1,2}[.\s_&-]*E\d{1,3}", re.IGNORECASE)
_SOURCE_EXEMPT_NAME_RE = re.compile(
    r"(?i)(?:\.(?:mp3|flac|m4a|aac|ogg|opus|wav|wma|aif|aiff|alac|ape|mka|cue)(?:$|\b)|\b(?:music|audiobook|audiobooks|ebook|ebooks|disc|cd|vinyl|lossless|podcast)\b)"
)
_SEASON_MARKER_RE = re.compile(r"(?:\bS\d{1,2}\b|\bS\d{4}\b|\bS\d{1,2}X?E\d{1,3}\b|\bSeason\b|\b\d{1,2}x\d{1,3}\b|\bEp(?:isode)?\.?\s?\d{1,3}\b)", re.IGNORECASE)
_SCENE_VIDEO_TAG_RE = re.compile(
    r"(?:\b(?:19|20)\d{2}\b|\b(?:480|576|720|1080|1440|2160|4320)[pi]\b|\b(?:bluray|bdrip|brrip|webrip|web[-_.\s]?dl|remux|hdtv|dvdrip|x26[45]|h\.?26[45])\b)",
    re.IGNORECASE,
)
_VIDEO_FILE_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".mov",
    ".m4v",
    ".wmv",
    ".ts",
    ".m2ts",
    ".mpg",
    ".mpeg",
    ".webm",
    ".flv",
}
_AUDIOBOOK_EXTENSIONS = {".m4b"}
_MUSIC_EXTENSIONS = {".m4a", ".mp3", ".flac", ".cue"}
_EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi"}
_ANIME_SEQUENCE_PATTERN = re.compile(
    r"(?i)(?:"
    r"\bS\d{1,2}[.\s_-]*E\d{1,3}(?:[.\s_-]*-[.\s_-]*\d{1,3})?\b"
    r"|(?:^|[.\s_-])\d{1,2}x\d{1,3}(?:[.\s_-]|$)"
    r"|\bEP\d{1,3}\b"
    r"|\bEp\.\d{1,3}\b"
    r"|\bEpisode[.\s_-]+\d{1,3}\b"
    r"|\bOVA[.\s_-]*\d{1,3}\b"
    r"|\[\d{1,3}(?:v\d+)?\]"
    r"|\(\d{1,3}(?:v\d+)?\)"
    r"|(?:^|[.\s_-])-\s?\d{1,3}(?:v\d+)?(?:$|[.\s_-])"
    r"|(?:^|[.\s_-])-[.\s_-]*(?!(?:19|20)\d{2}(?:$|[.\s_-]))\d{1,5}(?:v\d+)?(?:$|[.\s_-])"
    r"|\[(?!(?:19|20)\d{2}\])\d{1,5}(?:v\d+)?\]"
    r"|(?:^|[.\s_-])(?!(?:19|20)\d{2}(?:[.\s_-]|$))\d{4,5}(?:v\d+)?"
    r"(?=[.\s_-]+(?:2160p|1080[pi]?|720p|576[pi]?|480[pi]?|WEB|BluRay|BDRip|HDTV|DVD))"
    r"|\b\d{4}[.\-_]\d{2}[.\-_]\d{2}\b"
    r"|\bSeason[.\s_-]+\d{1,2}\b"
    r"|\bS\d{1,2}\b"
    r"|\bComplete[.\s_-]+Series\b"
    r")"
)
_VIDEO_CHILD_EXTENSIONS = {".mkv", ".mp4", ".avi"}
_CHILD_BURNLIST_PATTERN = re.compile(r"(?i)(?:\bNCED\b|\bNCOP\b|\bsample\b|\.nfo\b)")
_ANIME_BONUS_PATTERN = re.compile(r"(?i)(?:\bncop\b|\bnced\b|\bcreditless\b|(?:^|[.\s_-])op\d{0,2}(?:$|[.\s_-])|(?:^|[.\s_-])ed\d{0,2}(?:$|[.\s_-]))")
_EPISODIC_TV_PATTERN = re.compile(r"(?i)(?:\bS\d{1,2}X?E\d{1,3}\b|\bSeason\b|\bEp(?:isode)?\.?\s?\d{1,3}\b|\b\d{1,2}x\d{1,3}\b)")
_SOURCE_TAG_TOKENS = [
    "Bluray",
    "BluRay",
    "Blu-Ray",
    "BD",
    "BDRip",
    "BRRip",
    "4K-UHD",
    "UHD",
    "REMUX",
    "BD-Remux",
    "UHD-Remux",
    "COMPLETE.BLURAY",
    "WEB-DL",
    "WEBDL",
    "WEB_DL",
    "WEB-Rip",
    "WEBRip",
    "WEB_Rip",
    "WEB",
    "WebHD",
    "DVDRip",
    "DVD-Rip",
    "DVD",
    "NTSC",
    "PAL",
    "DVDR",
    "DVD5",
    "DVD9",
    "HDTV",
    "HDTVRip",
    "SDTV",
    "EDTV",
    "PDTV",
    "DSR",
    "DSRip",
    "DVB",
    "DVBRip",
    "SATRip",
    "HQSATRip",
    "DTV",
    "DTVRip",
    "TVRip",
    "SATELLITE",
    "MPEG2",
    "VHS",
    "VHSRip",
    "CAM",
    "CAMRip",
    "TS",
    "TELESYNC",
    "TC",
    "TELECINE",
    "WORKPRINT",
    "WP",
    "SCREENER",
    "SCR",
    "DVDSCR",
    "BDSRC",
    "PPV",
    "PPVRip",
    "INTERNAL",
    "INT",
]
_SOURCE_TAG_PATTERN = re.compile(
    r"(?i)(?<![a-z0-9])(?:"
    + "|".join(re.escape(token).replace(r"\-", "[-_.\\s]?").replace(r"\.", r"[._\\s]?") for token in _SOURCE_TAG_TOKENS)
    + r")(?![a-z0-9])"
)


def _directory_extension_first_category(entry: Path) -> str:
    """Classify by real file extensions inside a directory before any name/token inference."""
    if not entry.is_dir():
        return ""
    counts = {"audiobooks": 0, "music": 0, "ebooks": 0, "video": 0}
    try:
        for root, _dirs, filenames in os.walk(str(entry)):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext in _VIDEO_FILE_EXTENSIONS:
                    counts["video"] += 1
                elif ext in _AUDIOBOOK_EXTENSIONS:
                    counts["audiobooks"] += 1
                elif ext in _MUSIC_EXTENSIONS:
                    counts["music"] += 1
                elif ext in _EBOOK_EXTENSIONS:
                    counts["ebooks"] += 1
    except OSError:
        return ""
    if counts["audiobooks"] == 0 and counts["music"] == 0 and counts["ebooks"] == 0:
        return ""
    audio_count = counts["audiobooks"] + counts["music"]
    if audio_count > counts["video"]:
        audio_category = classify_audio_folder(entry)
        if audio_category:
            return audio_category
        return ""
    if counts["ebooks"] > counts["video"] and counts["ebooks"] >= audio_count:
        return "books"
    return ""


def _source_matrix_ignore_reason(category: str, title: str) -> str:
    """Return ignore reason when MOVIES/TV/ANIME item lacks source/episode signals."""
    cat = str(category or "").strip().lower()
    if cat not in {"movies", "tv", "anime"}:
        return ""
    title_text = str(title or "").lower()
    if _SOURCE_TAG_PATTERN.search(title_text) or _ANIME_SEQUENCE_PATTERN.search(title_text):
        return ""
    return "Missing media source/episode signal (source-matrix validation)"


def _has_video_container_and_source_signal(name: str, path_text: str = "") -> bool:
    """True when a node is a real video container with a real video-source token."""
    combined = f"{name or ''} {path_text or ''}".lower()
    suffix = Path(path_text or name or "").suffix.lower()
    return suffix in _VIDEO_FILE_EXTENSIONS and bool(
        _SOURCE_TAG_PATTERN.search(combined) or _SCENE_VIDEO_TAG_RE.search(combined)
    )


def _force_video_processing_state(node: Dict[str, Any], fallback_folder_hint: str = "") -> None:
    """Force a node to VALID/eligible video processing state for REMUX/BLURAY/Web sources."""
    if not isinstance(node, dict):
        return
    name = str(node.get("name") or "")
    ptxt = str(node.get("path") or "")
    existing_category = str(node.get("detected_category") or node.get("category") or "").strip().lower()
    # Keep historical Jikan/anime inheritance behavior:
    # once a row resolves to ANIME, do not let later source-token normalization
    # demote it back to TV/MOVIES.
    if existing_category == "anime":
        node["status"] = "VALID"
        node["eligible"] = True
        node["ignored"] = False
        node["skip_reason"] = ""
        return
    if _ANIME_BONUS_PATTERN.search(f"{name} {ptxt}".lower()):
        return
    if not _has_video_container_and_source_signal(name, ptxt):
        return
    classification = classify_video_name_result(
        name,
        fallback_folder_hint,
        anime_lookup=_anime_cache_lookup,
    )
    if classification.category not in {"tv", "anime", "movies"}:
        return
    node["detected_category"] = classification.category
    node["category"] = classification.category
    node["detection_method"] = classification.method
    node["detection_confidence"] = classification.confidence
    node["detection_evidence"] = list(classification.evidence)
    node["auto_select_ignored"] = False
    node["auto_select_reason"] = ""
    node["auto_selectable"] = True
    # Compatibility flags requested by UI/queue workers.
    node["status"] = "VALID"
    node["eligible"] = True
    node["ignored"] = False
    node["skip_reason"] = ""
    if "requirements_met" in node:
        node["requirements_met"] = True


def _node_has_source_or_episode_signal(item: Dict[str, Any]) -> bool:
    """Check parent + descendants for source or episode-pattern signals (case-insensitive)."""
    if not isinstance(item, dict):
        return False
    text = f"{item.get('name') or ''} {item.get('path') or ''}".lower()
    if _SOURCE_TAG_PATTERN.search(text) or _ANIME_SEQUENCE_PATTERN.search(text):
        return True
    for child in item.get("children", []) or []:
        if _node_has_source_or_episode_signal(child):
            return True
    return False


def _apply_source_matrix_guard(item: Dict[str, Any]) -> None:
    """Mark MOVIES/TV items ignored when source matrix tokens are absent."""
    category = str(item.get("detected_category") or item.get("category") or "").strip().lower()
    if category in {"disc", "ebooks", "books", "audiobooks", "music"}:
        return
    if category in {"movies", "tv", "anime"} and _node_has_source_or_episode_signal(item):
        return
    reason = _source_matrix_ignore_reason(category, f"{item.get('name') or ''} {item.get('path') or ''}")
    if reason:
        item["auto_select_ignored"] = True
        item["auto_select_reason"] = reason
        item["auto_selectable"] = False


def _validate_child_video_items(node: Dict[str, Any]) -> None:
    """Validate each child video independently; parent pass does not grant child pass."""
    if not isinstance(node, dict):
        return

    def walk(item: Dict[str, Any]) -> None:
        children = item.get("children", []) or []
        for child in children:
            if not isinstance(child, dict):
                continue
            path_text = str(child.get("path") or "")
            name_text = str(child.get("name") or "")
            ext = Path(path_text or name_text).suffix.lower()
            child_category = str(child.get("detected_category") or child.get("category") or "").strip().lower()
            # Non-video classes are exempt.
            if child_category in {"books", "ebooks", "music", "audiobooks", "disc"}:
                walk(child)
                continue
            if ext in _VIDEO_CHILD_EXTENSIONS:
                if _ANIME_BONUS_PATTERN.search(name_text):
                    # Keep historical behavior: anime bonus assets are always ANIME rows,
                    # then marked ignored as bonus/non-episode content.
                    child["detected_category"] = "anime"
                    child["category"] = "anime"
                    child["auto_select_ignored"] = True
                    child["auto_select_reason"] = "Anime bonus asset (NCOP/NCED/OP/ED/Creditless)"
                    child["auto_selectable"] = False
                elif _CHILD_BURNLIST_PATTERN.search(name_text):
                    child["auto_select_ignored"] = True
                    child["auto_select_reason"] = "Burn-list exclusion (NCED/NCOP/sample/nfo)"
                    child["auto_selectable"] = False
                else:
                    reason = _source_matrix_ignore_reason(child_category or "anime", name_text)
                    if reason:
                        child["auto_select_ignored"] = True
                        child["auto_select_reason"] = reason
                        child["auto_selectable"] = False
            walk(child)

    walk(node)


def _force_tree_category(node: Dict[str, Any], category: str) -> None:
    """Force a category across a node and all descendants (case-insensitive safe-zone use)."""
    cat = str(category or "").strip().lower()
    if not isinstance(node, dict) or not cat:
        return
    node["detected_category"] = cat
    node["category"] = cat
    if cat == "disc":
        node["itype"] = "Disc"
    if cat in {"disc", "books", "ebooks", "audiobooks", "music"}:
        node["auto_select_ignored"] = False
        node["auto_select_reason"] = ""
        node["auto_selectable"] = True
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _force_tree_category(child, cat)


def _decide_child_promoted_category(
    node: Dict[str, Any],
    current: str,
    children: List[Dict[str, Any]],
    child_cats: List[str],
) -> str:
    """Decide which category (if any) ``node`` should be promoted to from its children.

    Returns ``"disc"``, another category name, or ``""`` for no promotion. DISC is
    highest precedence; the caller applies it via ``_force_tree_category``.
    """
    child_set = {c for c in child_cats if c}
    if not child_set:
        return ""
    if "disc" in child_set:
        return "disc"
    # A folder whose children are all one book/music type is that type.
    for target in ("audiobooks", "ebooks", "books", "music"):
        if child_set == {target}:
            return target

    # For regular media packs, let strong child consensus set the parent.
    tv_count = sum(1 for c in child_cats if c == "tv")
    anime_count = sum(1 for c in child_cats if c == "anime")
    movie_count = sum(1 for c in child_cats if c == "movies")
    anime_bonus_count = sum(
        1
        for c in children
        if _ANIME_BONUS_PATTERN.search(str(c.get("name") or "").lower())
    )
    parent_anime_cached = _anime_cache_lookup(str(node.get("name") or "")) is True
    has_desc_anime_cached = any(
        _anime_cache_lookup(str(c.get("name") or "")) is True for c in children
    )

    if (
        (parent_anime_cached or has_desc_anime_cached)
        and not (tv_count > 0 and anime_count == 0 and anime_bonus_count == 0)
    ):
        return "anime"
    if anime_bonus_count > 0:
        # Presence of NCOP/NCED bonus markers should lock the enclosing pack to ANIME.
        return "anime"
    if anime_count > 0 and movie_count == 0:
        return "anime"
    if tv_count > 0 and movie_count == 0:
        return "tv"
    if current in {"", "misc"} and movie_count > 0:
        return "movies"
    return ""


def _walk_category_inheritance(n: Dict[str, Any]) -> None:
    """Post-order walk that promotes a parent's category from its children's consensus."""
    children = [c for c in (n.get("children") or []) if isinstance(c, dict)]
    for child in children:
        _walk_category_inheritance(child)
    if not children:
        return

    current = str(n.get("detected_category") or n.get("category") or "").strip().lower()
    child_cats = [str(c.get("detected_category") or c.get("category") or "").strip().lower() for c in children]
    promote = _decide_child_promoted_category(n, current, children, child_cats)
    if promote in {"disc", "audiobooks", "ebooks", "books", "music"}:
        _force_tree_category(n, promote)
        return
    if promote:
        n["detected_category"] = promote
        n["category"] = promote


def _inherit_category_from_children(node: Dict[str, Any]) -> None:
    """Promote parent category from descendants when structure clearly indicates one type."""
    if not isinstance(node, dict):
        return
    _walk_category_inheritance(node)


def _inherit_anime_context_to_children(node: Dict[str, Any]) -> None:
    """If a parent resolves to ANIME, keep descendants in ANIME context."""
    if not isinstance(node, dict):
        return

    def walk(n: Dict[str, Any], parent_cat: str = "") -> None:
        current = str(n.get("detected_category") or n.get("category") or "").strip().lower()
        effective = current or parent_cat
        if parent_cat == "anime" and current in {"", "misc", "movies", "tv"}:
            n["detected_category"] = "anime"
            n["category"] = "anime"
            effective = "anime"
        for child in n.get("children", []) or []:
            if isinstance(child, dict):
                walk(child, effective)

    walk(node)


def _stamp_failed_item(item: Dict[str, Any], failed_map: Dict[str, Dict[str, str]], active_ids: List[str]) -> None:
    """Stamp ``indexer_errors`` onto a single item if it has failed submissions."""
    key = item.get("key", "")
    name = item.get("name", "")
    path = item.get("path", "")
    errors: Dict[str, str] = {}
    for lookup in (key, name, path):
        if lookup in failed_map:
            for idx_id, error in failed_map[lookup].items():
                if idx_id in active_ids and idx_id not in errors:
                    errors[idx_id] = error
    if errors:
        item["indexer_errors"] = errors
        if active_ids:
            indexers = dict(item.get("indexers") or {})
            for idx_id in errors:
                if idx_id in active_ids:
                    indexers[idx_id] = False
            item["indexers"] = {idx_id: bool(indexers.get(idx_id, False)) for idx_id in active_ids}
        item["completed"] = False


def _stamp_failed_indexer_flags(
    result: Dict[str, Any], failed_map: Dict[str, Dict[str, str]], active_ids: List[str]
) -> None:
    """Walk the pending snapshot and stamp ``indexer_errors`` on items with failed submissions.

    This adds a dict ``{indexer_id: error_message}`` alongside the existing
    ``indexers`` boolean dict so the UI can distinguish 'failed' (yellow) from
    'pending' (red) without changing the existing boolean contract.
    """
    # Flat categories (movies, misc, custom)
    for cat_key, cat_items in result.items():
        if cat_key in ("tv", "external") or not isinstance(cat_items, list):
            continue
        for item in cat_items:
            if isinstance(item, dict):
                _stamp_failed_item(item, failed_map, active_ids)

    # External folder groups
    for group in result.get("external", []):
        for item in group.get("items", []):
            if isinstance(item, dict):
                _stamp_failed_item(item, failed_map, active_ids)
                for child in item.get("children", []):
                    if isinstance(child, dict):
                        _stamp_failed_item(child, failed_map, active_ids)


def _external_group_order_key(group: Dict[str, Any]) -> str:
    raw_key = str(
        group.get("key")
        or group.get("folder_path")
        or group.get("folder_name")
        or group.get("id")
        or ""
    ).strip()
    if not raw_key:
        return ""
    normalized_key = raw_key.replace("\\", "/").rstrip("/").casefold()
    return f"external:{normalized_key}"


def _sort_external_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conf = get_config()
    raw_order = getattr(conf, "pending_external_group_order", []) or []
    if not isinstance(raw_order, list):
        raw_order = []
    order_map = {str(key): idx for idx, key in enumerate(raw_order) if str(key).strip()}
    if not order_map:
        return groups

    def sort_key(group: Dict[str, Any]) -> tuple[int, str]:
        key = _external_group_order_key(group)
        order_index = order_map.get(key, len(order_map))
        label = str(group.get("folder_name") or group.get("key") or key or "").casefold()
        return order_index, label

    return sorted(groups, key=sort_key)


def collect_anime_check_names(data: Dict[str, Any]) -> list[str]:
    """Extract unique candidate titles for anime lookups from a pending snapshot."""
    seen: set[str] = set()
    names: list[str] = []
    video_itypes = {"TV Show", "Movie", "Anime"}

    def _add(name: str) -> None:
        cleaned = str(name or "").strip()
        identity = cleaned.casefold()
        if cleaned and identity not in seen:
            seen.add(identity)
            names.append(cleaned)

    def _add_item(item: Dict[str, Any], section: str = "") -> None:
        detector_candidates = item.get("_anime_lookup_candidates")
        if isinstance(detector_candidates, (list, tuple)):
            for candidate in detector_candidates:
                _add(str(candidate or ""))
        if item.get("itype") in video_itypes or section in {"tv", "movies", "anime"}:
            _add(str(item.get("name") or ""))

    items = data.get("items", {})

    for category_key, category_items in items.items():
        if category_key == "external" or not isinstance(category_items, list):
            continue
        for item in category_items:
            if not isinstance(item, dict):
                continue
            _add_item(item, category_key)

    for group in items.get("external", []):
        for item in group.get("items", []):
            if isinstance(item, dict):
                _add_item(item)

    return names


def collect_uncached_anime_check_names(data: Dict[str, Any]) -> list[str]:
    """Return anime-check candidates that are not already cached."""
    from logic.anime_cache import get_cached

    return [name for name in collect_anime_check_names(data) if get_cached(name) is None]


def _anime_cache_lookup(name: str) -> Optional[bool]:
    from logic.anime_cache import get_cached as anime_cached

    return anime_cached(name)


def classify_video_name(
    name: str,
    folder_category: str = "",
    assume_movie_if_unknown: bool = False,
    *,
    explicit_category_hint: str = "",
    explicit_itype_hint: str = "",
) -> str:
    """Classify a video release name as TV Show, Anime, Movie, or Misc."""
    return shared_classify_video_name(
        name,
        folder_category,
        assume_movie_if_unknown,
        anime_lookup=_anime_cache_lookup,
        explicit_category_hint=explicit_category_hint,
        explicit_itype_hint=explicit_itype_hint,
    )


def detect_external_category(name: str, entry_path: Path) -> str:
    """Auto-detect category for an external folder/file based on naming patterns."""
    lower_name = str(name or "").lower()
    if _ANIME_BONUS_PATTERN.search(lower_name):
        return "anime"
    anime_cached = _anime_cache_lookup(str(name or ""))
    if anime_cached is True:
        return "anime"
    return shared_detect_external_category(name, entry_path)


def detect_content_itype(name: str, entry_path: Path, folder_category: str) -> str:
    """Detect the display content type for a pending item."""
    return shared_detect_content_itype(
        name,
        entry_path,
        folder_category,
        anime_lookup=_anime_cache_lookup,
    )


def _classify_standalone_file_category(entry: Path) -> str:
    """Classify a loose file by filename + extension only, without parent-path hints."""
    # DISC has absolute priority over ebook/music/audiobook extension detection.
    # A Blu-ray or DVD folder may contain companion PDFs - it is still disc, not ebooks.
    if has_video_disc_structure(entry):
        return "disc"
    ext_first_category = _directory_extension_first_category(entry)
    if ext_first_category:
        return ext_first_category
    ext = entry.suffix.lower()
    if ext in _EBOOK_EXTENSIONS:
        return "books"
    return ""


def _selection_path_identity(value: str | Path) -> str:
    text = str(value)
    try:
        resolved = str(Path(text).resolve())
    except OSError:
        resolved = text
    return resolved.casefold()


def _row_source_exempt(node: Dict[str, Any]) -> bool:
    category = str(node.get("detected_category") or node.get("category") or "").strip().lower()
    itype = str(node.get("itype") or "").strip().lower()
    name_text = f"{node.get('name') or ''} {node.get('path') or ''}".strip()
    if _SOURCE_EXEMPT_NAME_RE.search(name_text):
        return True
    return category in {"music", "books", "ebooks", "audiobooks", "disc"} or itype in {
        "music",
        "ebook",
        "audiobook",
        "disc",
    }


def _resolve_node_ignored_reason(node: Dict[str, Any], ignored_reason: Optional[str]) -> str:
    if (
        ignored_reason
        and _row_source_exempt(node)
        and ignored_reason.lower().startswith("missing media source")
    ):
        return ""
    return ignored_reason or ""


def _annotate_selection_node(
    node: Dict[str, Any],
    node_identity: str,
    selectable: Set[str],
    ignored: Dict[str, str],
    child_selected: bool,
) -> bool:
    """Set auto-select fields on one tree node; returns whether it (or a descendant) is selected."""
    ignored_reason = _resolve_node_ignored_reason(node, ignored.get(node_identity))
    node_selected = node_identity in selectable
    # A pack/folder is valid (selectable) if it has ≥1 valid episode child
    is_dir = bool(node.get("is_dir"))
    children = [c for c in node.get("children", []) or [] if isinstance(c, dict)]
    is_leaf_file = not is_dir and not children
    leaf_fallback_selected = is_leaf_file and not ignored_reason
    if is_dir and child_selected and ignored_reason:
        ignored_reason = ""
    effectively_selected = node_selected or leaf_fallback_selected or (is_dir and child_selected)

    node["auto_selectable"] = bool(effectively_selected)
    node["auto_select_ignored"] = bool(ignored_reason)
    # Track whether this directory became selectable via its children
    # (pack folder) vs being directly in queue_paths (movie folder).
    if not node_selected and is_dir and child_selected:
        node["_pack_via_children"] = True
    if ignored_reason:
        node["auto_select_reason"] = ignored_reason
    elif child_selected and not node_selected and not is_dir:
        node["auto_select_reason"] = "Selectable descendants only"
    else:
        node.pop("auto_select_reason", None)

    return node_selected or child_selected or leaf_fallback_selected


def _stamp_tree_selection_state(item: Dict[str, Any], resolution: Any) -> bool:
    """Annotate tree nodes with auto-select metadata from explicit-path resolution."""
    selectable = {_selection_path_identity(path) for path in getattr(resolution, "queue_paths", ())}
    ignored = {_selection_path_identity(entry.path): entry.reason for entry in getattr(resolution, "ignored_paths", ())}

    def visit(node: Dict[str, Any]) -> bool:
        child_selected = False
        built_children = node.get("children", []) or []
        for child in built_children:
            child_selected = visit(child) or child_selected
        node_identity = _selection_path_identity(node.get("path", ""))
        if node.get("is_dir") and not built_children:
            # Lazy tree: children are not built yet, so look for selectable
            # descendants among the resolved queue paths instead.
            prefix = node_identity.rstrip("\\/") + os.sep
            child_selected = any(path.startswith(prefix) for path in selectable)
        return _annotate_selection_node(node, node_identity, selectable, ignored, child_selected)

    has_selectable = visit(item)
    if not has_selectable and ignored:
        item["auto_selectable"] = False
        item["auto_select_ignored"] = True
        item["auto_select_reason"] = next(iter(ignored.values()))
    return has_selectable


def _mark_ignored_tree_nodes_completed(node: Dict[str, Any], active_ids: List[str]) -> None:
    for child in node.get("children", []) or []:
        _mark_ignored_tree_nodes_completed(child, active_ids)

    if not node.get("auto_select_ignored"):
        return

    node["skipped"] = True
    node["completed"] = False
    if active_ids:
        direct_indexers = node.get("_direct_indexers", {}) if isinstance(node.get("_direct_indexers"), dict) else {}
        node["indexers"] = {idx_id: bool(direct_indexers.get(idx_id, False)) for idx_id in active_ids}


def _clear_non_target_ignored_flags(node: Dict[str, Any]) -> None:
    """Only TV/ANIME trees keep ignored state from episode/source rules."""
    if not isinstance(node, dict):
        return
    category = str(node.get("detected_category") or node.get("category") or "").strip().lower()
    if category not in {"tv", "anime"}:
        node["auto_select_ignored"] = False
        node["auto_select_reason"] = ""
        node["auto_selectable"] = True
    for child in node.get("children", []) or []:
        _clear_non_target_ignored_flags(child)


def _direct_indexers_of(node: Dict[str, Any]) -> Dict[str, Any]:
    """Return `node["_direct_indexers"]` when it's a dict, else `{}`."""
    raw = node.get("_direct_indexers", {})
    return raw if isinstance(raw, dict) else {}


def _rollup_ignored_completion(node: Dict[str, Any], active_ids: List[str]) -> None:
    node["skipped"] = True
    node["completed"] = False
    if active_ids:
        direct_indexers = _direct_indexers_of(node)
        node["indexers"] = {idx_id: bool(direct_indexers.get(idx_id, False)) for idx_id in active_ids}


def _rollup_leaf_completion(node: Dict[str, Any], active_ids: List[str]) -> None:
    direct_indexers = _direct_indexers_of(node)
    indexers = direct_indexers or (node.get("indexers", {}) if isinstance(node.get("indexers"), dict) else {})
    deferred_done = node.get("_deferred_children_done")
    if active_ids:
        if isinstance(deferred_done, dict):
            # Deferred (lazily loaded) directory: complete only when ALL of its
            # direct children are in the upload map for that indexer.
            node["indexers"] = {idx_id: bool(deferred_done.get(idx_id, True)) for idx_id in active_ids}
        else:
            node["indexers"] = {idx_id: bool(indexers.get(idx_id, False)) for idx_id in active_ids}
    node["completed"] = bool(active_ids) and bool(node.get("indexers")) and all(node["indexers"].values())


def _rollup_children_completion(
    node: Dict[str, Any], required_children: List[Dict[str, Any]], active_ids: List[str]
) -> None:
    if not required_children:
        if active_ids:
            node["indexers"] = {idx_id: False for idx_id in active_ids}
        node["completed"] = False
        return

    if active_ids:
        node["indexers"] = {
            idx_id: all(
                (child.get("indexers") or {}).get(idx_id, bool(child.get("completed"))) for child in required_children
            )
            for idx_id in active_ids
        }
        node["completed"] = all(node["indexers"].values())
    else:
        node["completed"] = all(bool(child.get("completed")) for child in required_children)


def _rollup_external_completion(node: Dict[str, Any], active_ids: List[str]) -> None:
    children = [child for child in node.get("children", []) or [] if isinstance(child, dict)]
    for child in children:
        _rollup_external_completion(child, active_ids)

    if node.get("auto_select_ignored"):
        _rollup_ignored_completion(node, active_ids)
        return

    if not children:
        _rollup_leaf_completion(node, active_ids)
        return

    # Packs roll up from their children (queue-backend-16); a pack's own record
    # alone no longer marks it done.
    required_children = [child for child in children if not child.get("auto_select_ignored")]
    _rollup_children_completion(node, required_children, active_ids)


def _propagate_pack_completion_down(node: Dict[str, Any], inherited_indexers: Optional[Dict[str, bool]] = None) -> None:
    """Propagate a pack's upload status down to children that have no individual upload record.

    Runs after _rollup_external_completion (bottom-up) to fill in children whose rel_path
    was never individually recorded in the DB (e.g. files inside a pack uploaded as a unit).
    Only updates nodes with no direct record; nodes with their own DB entries keep their values.
    Skipped/ignored items are excluded - they carry their own (False) completion state.
    """
    children = [child for child in node.get("children", []) or [] if isinstance(child, dict)]

    if inherited_indexers is not None:
        direct = node.get("_direct_indexers") or {}
        # Don't overwrite skipped items - they were explicitly excluded from upload selection
        if not any(direct.values()) and not node.get("skipped"):
            node["indexers"] = dict(inherited_indexers)
            node["completed"] = all(inherited_indexers.values())

    current = node.get("indexers") or {}
    propagate_down = current if any(current.values()) else (inherited_indexers or current)
    for child in children:
        _propagate_pack_completion_down(child, propagate_down)


def _enforce_child_source_requirement(node: Dict[str, Any]) -> None:
    """Mark child video files as ignored when they lack a quality source token in their filename.

    A child file without WEB-DL/BluRay/HDTV/DVD/etc. in its own name cannot be auto-selected
    for individual upload - the parent pack (which carries the source token) is the upload unit.
    Episode numbers like S03E01 are NOT sufficient on their own.

    Must run after _stamp_tree_selection_state (which grants leaf-fallback eligibility) and after
    _clear_non_target_ignored_flags so this acts as the final authority on child eligibility.
    """
    for child in node.get("children", []) or []:
        if not isinstance(child, dict):
            continue
        _enforce_child_source_requirement(child)

        if child.get("auto_select_ignored"):
            continue

        name = str(child.get("name") or "")
        ext = Path(name).suffix.lower()
        if ext not in _VIDEO_CHILD_EXTENSIONS:
            continue

        category = str(child.get("detected_category") or child.get("category") or "").strip().lower()
        if category in {"disc", "books", "ebooks", "audiobooks", "music"}:
            continue

        # Check only the file's own name - parent folder tokens do not count
        if _SOURCE_TAG_PATTERN.search(name):
            continue

        child["auto_select_ignored"] = True
        child["auto_select_reason"] = "Missing quality source in filename (WEB-DL/BluRay/HDTV/DVD/etc. required for individual upload)"
        child["auto_selectable"] = False
        child["eligible"] = False


def _exclude_ignored_deferred_children(item: Dict[str, Any], resolution: Any, active_ids: List[str]) -> None:
    """Judge a lazily scanned folder only by the children that can be uploaded.

    The deferred check looks at every direct child; extras, samples and
    sidecars the resolver ignores are never uploaded on their own, so they must
    not keep a finished pack from showing as done. A folder with no uploadable
    child is never done (ignored rows never count as done).
    """
    per_child = item.get("_deferred_child_indexers")
    if not isinstance(per_child, dict) or not isinstance(item.get("_deferred_children_done"), dict) or not active_ids:
        return
    ignored = {_selection_path_identity(entry.path) for entry in getattr(resolution, "ignored_paths", ()) or ()}
    required = [indexers for identity, indexers in per_child.items() if identity not in ignored]
    item["_deferred_children_done"] = {
        idx_id: bool(required) and all(idx_id in indexers for indexers in required) for idx_id in active_ids
    }


def _stamp_lazy_children_selection(children: List[Dict[str, Any]], resolution: Any) -> None:
    """Stamp auto-select state on one lazily loaded level from its parent's resolution."""
    selectable = {_selection_path_identity(path) for path in getattr(resolution, "queue_paths", ()) or ()}
    ignored = {
        _selection_path_identity(entry.path): entry.reason for entry in getattr(resolution, "ignored_paths", ()) or ()
    }
    for child in children:
        identity = _selection_path_identity(child.get("path", ""))
        prefix = identity.rstrip("\\/") + os.sep
        descendant_selected = bool(child.get("is_dir")) and any(path.startswith(prefix) for path in selectable)
        _annotate_selection_node(child, identity, selectable, ignored, descendant_selected)


def _inherit_parent_valid_state(node: Dict[str, Any]) -> bool:
    """Bubble child VALID/eligible state upward so healthy episode packs keep parent selectable."""
    if not isinstance(node, dict):
        return False
    children = [child for child in node.get("children", []) or [] if isinstance(child, dict)]
    child_has_valid = False
    for child in children:
        if _inherit_parent_valid_state(child):
            child_has_valid = True
    self_valid = (
        str(node.get("status") or "").upper() == "VALID"
        or bool(node.get("eligible"))
        or (not node.get("auto_select_ignored") and bool(node.get("auto_selectable")))
    )
    if child_has_valid and not self_valid:
        node["status"] = "VALID"
        node["eligible"] = True
        node["ignored"] = False
        node["skip_reason"] = ""
        node["auto_select_ignored"] = False
        node["auto_select_reason"] = ""
        node["auto_selectable"] = True
        return True
    return self_valid or child_has_valid


def _strip_external_helper_fields(node: Dict[str, Any]) -> None:
    node.pop("_direct_indexers", None)
    node.pop("_pack_via_children", None)
    node.pop("_deferred_children_done", None)
    node.pop("_deferred_child_indexers", None)
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _strip_external_helper_fields(child)


def _log_pack_completion_state(item: Dict[str, Any]) -> None:
    leaves: List[Dict[str, Any]] = []

    def visit(node: Dict[str, Any]) -> None:
        children = [child for child in node.get("children", []) or [] if isinstance(child, dict)]
        if not children:
            leaves.append(node)
            return
        for child in children:
            visit(child)

    visit(item)
    valid_count = sum(1 for leaf in leaves if not leaf.get("auto_select_ignored"))
    ignored_count = sum(1 for leaf in leaves if leaf.get("auto_select_ignored"))
    if valid_count == 0 and ignored_count == 0:
        return

    status = "completed" if item.get("completed") else "pending"
    logger.log(
        "VERBOSE",
        f"Pack completion status for '{item.get('name', 'item')}': {status} "
        f"({valid_count} valid files, {ignored_count} ignored)",
    )


def _compact_external_tree_item(
    item: Dict[str, Any],
    metadata_by_path: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Drop retained descendants while preserving a compact search document.

    Full descendants are needed transiently while classification and completion
    state are rolled up. The Queue page fetches them lazily, so retaining every
    descendant dictionary after the scan duplicates filesystem state and costs
    substantially more memory than the searchable names themselves.
    """
    searchable_names: list[str] = []
    stack: list[Dict[str, Any]] = [item]
    while stack:
        node = stack.pop()
        name = str(node.get("name") or "").strip()
        if name:
            searchable_names.append(name)
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(child for child in children if isinstance(child, dict))
        if metadata_by_path is not None and node is not item:
            path_identity = _selection_path_identity(str(node.get("path") or ""))
            if path_identity:
                metadata_by_path[path_identity] = {
                    key: value
                    for key, value in node.items()
                    if key not in {"children", "files"}
                }

    if isinstance(item.get("children"), list) or isinstance(item.get("files"), list):
        item["children"] = []
        item["files"] = []
    return "\n".join(searchable_names)


def _compact_external_groups(groups: List[Dict[str, Any]]) -> tuple[Dict[str, str], bytes]:
    """Compact external rows and return private search and metadata indexes."""
    search_index: Dict[str, str] = {}
    metadata_by_path: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        for item in group.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            search_text = _compact_external_tree_item(item, metadata_by_path)
            item_key = str(item.get("key") or "")
            if item_key and search_text:
                search_index[item_key] = search_text
    metadata_json = json.dumps(
        metadata_by_path,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return search_index, zlib.compress(metadata_json, level=6)


def _load_external_metadata(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    raw_metadata = data.get("_external_metadata_zlib")
    if not isinstance(raw_metadata, bytes):
        return {}
    try:
        decoded = json.loads(zlib.decompress(raw_metadata).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zlib.error):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {
        str(key): value
        for key, value in decoded.items()
        if isinstance(value, dict)
    }


def _build_detected_item_metadata(entry: Path, *, category_hint: str = "") -> Dict[str, Any]:
    """Resolve backend-owned category/detection metadata for one visible item."""
    # Strict path isolation for loose files: never let parent folder hints drive detection.
    hint = "" if entry.is_file() else category_hint
    resolution = resolve_explicit_path(
        entry,
        category_hint=hint,
        anime_lookup=_anime_cache_lookup,
    )
    ignored_reason = next(
        (ignored.reason for ignored in getattr(resolution, "ignored_paths", ()) if ignored.path == entry),
        "",
    )
    if ignored_reason:
        detected_category = str(resolution.category or "").strip().lower()
        detected_itype = str(resolution.itype or "").strip().lower()
        if detected_category in {"music", "books", "ebooks", "audiobooks", "disc"} or detected_itype in {
            "music",
            "ebook",
            "audiobook",
            "disc",
        }:
            ignored_reason = ""
    detected_category = resolution.category
    raw_text = f"{entry.name} {entry}".lower()
    # Keep historical behavior: anime bonus assets must short-circuit to ANIME+IGNORED
    # so later video/source guards cannot rewrite them to TV/MOVIES.
    if _ANIME_BONUS_PATTERN.search(raw_text):
        return {
            "detected_category": "anime",
            "detection_method": "anime-bonus-pattern",
            "detection_confidence": "confirmed",
            "detection_evidence": ["anime bonus asset"],
            "detection_flags": ["anime_bonus"],
            "detection_override": "Anime bonus asset",
            "auto_selectable": False,
            "auto_select_ignored": True,
            "auto_select_reason": "Anime Non-Credit Feature / Bonus Asset",
            "status": "IGNORED",
            "eligible": False,
            "ignored": True,
            "skip_reason": "Anime Non-Credit Feature / Bonus Asset",
            "category": "anime",
        }
    payload = {
        "detected_category": detected_category,
        "detection_method": resolution.detection_method,
        "detection_confidence": resolution.detection_confidence,
        "detection_evidence": list(resolution.detection_evidence),
        "detection_flags": list(getattr(resolution, "content_flags", ()) or ()),
        "detection_override": getattr(resolution, "override_note", ""),
        "auto_selectable": bool(resolution.queue_paths),
        "auto_select_ignored": bool(ignored_reason),
        "auto_select_reason": ignored_reason,
    }
    payload["name"] = entry.name
    payload["path"] = str(entry)
    _force_video_processing_state(payload, category_hint)
    payload.pop("name", None)
    payload.pop("path", None)
    _apply_source_matrix_guard(payload | {"name": entry.name})
    return payload


def _scan_external_children(
    node: Path,
    rel_path: str,
    external_folder_name: str,
    upload_map: Dict[str, Set[str]],
    completed_lookup: Set[str],
    active_ids: List[str],
    folder_category_hint: str,
    include_children: bool,
    seen_dirs: Set[str],
    filesize_by_indexer: Optional[Dict[str, Dict[str, int]]] = None,
) -> "tuple[bool, List[Dict[str, Any]], bool, int, int, Optional[Dict[str, bool]], Dict[str, Set[str]]]":
    """Determine directory-ness and build child tree items.

    With include_children=False (lazy tree), a directory's direct children are
    only counted and checked against the upload map, so its completion is known
    without building them. Returns (is_dir, children, fully_scanned, size,
    deferred_child_count, deferred_children_done, deferred_child_indexers)."""
    is_dir = node.is_dir()
    fully_scanned = True

    if is_dir:
        try:
            resolved_key = str(node.resolve(strict=False))
        except OSError:
            resolved_key = str(node)
        if resolved_key in seen_dirs:
            fully_scanned = False
        else:
            seen_dirs.add(resolved_key)

    children: List[Dict[str, Any]] = []
    size = 0
    if is_dir and fully_scanned and include_children:
        try:
            for child in sorted(node.iterdir(), key=lambda path: path.name.lower()):
                if child.name.startswith("."):
                    continue
                child_rel = f"{rel_path}/{child.name}" if rel_path else child.name
                child_item, child_size = _build_external_tree_item(
                    external_folder_name,
                    child,
                    child_rel,
                    upload_map,
                    completed_lookup,
                    active_ids,
                    folder_category_hint=folder_category_hint,
                    top_level=False,
                    include_children=True,
                    filesize_by_indexer=filesize_by_indexer,
                    _seen_dirs=seen_dirs,
                )
                children.append(child_item)
                size += child_size
        except OSError:
            children = []
            fully_scanned = False

    deferred_child_count = 0
    deferred_children_done: Optional[Dict[str, bool]] = None
    deferred_child_indexers: Dict[str, Set[str]] = {}
    if is_dir and fully_scanned and not include_children:
        try:
            deferred_children_done = {idx_id: True for idx_id in active_ids} if active_ids else {}
            for child in node.iterdir():
                if child.name.startswith("."):
                    continue
                deferred_child_count += 1
                if not active_ids:
                    continue
                child_rel = f"{rel_path}/{child.name}" if rel_path else child.name
                # Cheap size check for file grandchildren only: sizing a directory
                # grandchild here would defeat the point of lazy loading.
                child_current_size: Optional[int] = None
                if not child.is_dir():
                    try:
                        child_current_size = child.stat().st_size
                    except OSError:
                        child_current_size = None
                child_indexers = _lookup_upload_map_indexers(
                    upload_map,
                    child.name,
                    child_rel,
                    child,
                    filesize_by_indexer=filesize_by_indexer,
                    current_size=child_current_size,
                )
                deferred_child_indexers[_selection_path_identity(child)] = set(child_indexers)
                for idx_id in active_ids:
                    if idx_id not in child_indexers:
                        deferred_children_done[idx_id] = False
        except OSError:
            deferred_child_count = 0
            deferred_children_done = None
            deferred_child_indexers = {}
            fully_scanned = False

    return (
        is_dir,
        children,
        fully_scanned,
        size,
        deferred_child_count,
        deferred_children_done,
        deferred_child_indexers,
    )


def _compute_external_item_indexer_status(
    upload_map: Dict[str, Set[str]],
    node: Path,
    rel_path: str,
    active_ids: List[str],
    is_dir: bool,
    children: List[Dict[str, Any]],
    *,
    filesize_by_indexer: Optional[Dict[str, Dict[str, int]]] = None,
    current_size: Optional[int] = None,
) -> tuple[Dict[str, bool], Dict[str, bool]]:
    """Return (indexer_status, direct_indexer_status) for one external tree item.

    A directory with children is "done" for an indexer only when every child is
    done for it. Extracted from _build_external_tree_item to keep its own
    branching down.
    """
    node_indexers = _lookup_upload_map_indexers(
        upload_map,
        node.name,
        rel_path,
        node,
        filesize_by_indexer=filesize_by_indexer,
        current_size=current_size,
    )
    direct_indexer_status: Dict[str, bool] = {idx_id: (idx_id in node_indexers) for idx_id in active_ids}
    indexer_status: Dict[str, bool] = dict(direct_indexer_status)
    if is_dir and children:
        indexer_status = {idx_id: True for idx_id in active_ids}
        for child in children:
            for idx_id, done in child.get("indexers", {}).items():
                if not done:
                    indexer_status[idx_id] = False
    return indexer_status, direct_indexer_status


def _apply_nested_external_item_category(item: Dict[str, Any], node: Path, folder_category_hint: str) -> None:
    """Populate detection/category fields for a non-top-level external tree item.

    Extracted from _build_external_tree_item to keep its own branching down;
    mutates item in place.
    """
    hint_category = str(folder_category_hint or "").strip().lower()
    if node.is_file():
        item["itype"] = detect_content_itype(node.name, node, folder_category_hint)
        # Nested rows keep their parent's category (as on the server) so a
        # child pill never falls back to another type.
        nested_category = hint_category or category_from_itype(item["itype"])
    else:
        nested_category = hint_category or "misc"
    if nested_category in {"disc", "books", "ebooks", "audiobooks", "music"}:
        _force_tree_category(item, nested_category)
    item["detected_category"] = nested_category
    item["category"] = nested_category
    item["detection_method"] = "Configured folder" if folder_category_hint else "Shared classifier"
    item["detection_confidence"] = "strong" if nested_category != "misc" else "unknown"


def _build_external_tree_item(
    external_folder_name: str,
    node: Path,
    rel_path: str,
    upload_map: Dict[str, Set[str]],
    completed_lookup: Set[str],
    active_ids: List[str],
    *,
    folder_category_hint: str = "",
    top_level: bool = False,
    include_children: bool = True,
    filesize_by_indexer: Optional[Dict[str, Dict[str, int]]] = None,
    _seen_dirs: Optional[Set[str]] = None,
) -> tuple[Dict[str, Any], int]:
    resolution = None
    seen_dirs = _seen_dirs if _seen_dirs is not None else set()
    (
        is_dir,
        children,
        fully_scanned,
        size,
        deferred_child_count,
        deferred_children_done,
        deferred_child_indexers,
    ) = _scan_external_children(
        node,
        rel_path,
        external_folder_name,
        upload_map,
        completed_lookup,
        active_ids,
        folder_category_hint,
        include_children,
        seen_dirs,
        filesize_by_indexer,
    )

    if not is_dir:
        try:
            size = node.stat().st_size
        except OSError:
            size = 0
    elif not fully_scanned or not include_children:
        size = compute_size_uncached(node)

    if top_level:
        top_level_hint = ""
        if node.is_dir():
            top_level_hint = folder_category_hint
        resolution = resolve_explicit_path(
            node,
            category_hint=top_level_hint,
            anime_lookup=_anime_cache_lookup,
        )

    indexer_status, direct_indexer_status = _compute_external_item_indexer_status(
        upload_map,
        node,
        rel_path,
        active_ids,
        is_dir,
        children,
        filesize_by_indexer=filesize_by_indexer,
        current_size=size,
    )

    item = {
        "name": node.name,
        "key": f"ext:{external_folder_name}:{rel_path}",
        "path": str(node),
        "size": size,
        "is_dir": is_dir,
        "is_directory": is_dir,
        "itype": detect_content_itype(node.name, node, "" if (top_level and not is_dir) else folder_category_hint) if top_level else "External",
        "indexers": indexer_status,
        "_direct_indexers": direct_indexer_status,
        "completed": bool(active_ids) and bool(indexer_status) and all(indexer_status.values()),
        "_deferred_children_done": deferred_children_done,
        "_deferred_child_indexers": deferred_child_indexers,
        "children": children,
        # Keep a second alias for clients that bind nested expansion off `files`.
        "files": children,
        "child_count": len(children) if include_children else deferred_child_count,
    }
    if top_level:
        item["_anime_lookup_candidates"] = list(anime_lookup_candidates(node))
    if is_dir and include_children and not fully_scanned:
        try:
            item["child_count"] = sum(1 for child in node.iterdir() if not child.name.startswith("."))
        except OSError:
            item["child_count"] = 0
    if not top_level:
        _apply_nested_external_item_category(item, node, folder_category_hint)
    _force_video_processing_state(item, folder_category_hint)
    if top_level:
        _finalize_top_level_external_item(item, node, resolution, folder_category_hint, active_ids, is_dir)
    return item, size


def _finalize_top_level_external_item(
    item: Dict[str, Any],
    node: Path,
    resolution: Any,
    folder_category_hint: str,
    active_ids: List[str],
    is_dir: bool,
) -> None:
    """Populate detection/category fields and run the validation cascade for a
    top-level external tree item. Extracted from _build_external_tree_item to
    keep its own branching down; mutates item in place."""
    item["itype"] = resolution.itype
    forced_category = _classify_standalone_file_category(node)
    resolved_category = forced_category or resolution.category
    item["detected_category"] = resolved_category or "misc"
    if item["detected_category"]:
        item["category"] = item["detected_category"]
    if item["detected_category"] == "anime":
        item["itype"] = "Anime"
    item["detection_method"] = resolution.detection_method
    item["detection_confidence"] = resolution.detection_confidence
    item["detection_evidence"] = list(resolution.detection_evidence)
    item["detection_flags"] = list(getattr(resolution, "content_flags", ()) or ())
    if resolution.override_note:
        item["detection_override"] = resolution.override_note
    # Parent DISC inheritance shield: once resolved as DISC, force all descendants
    # to DISC before validation so they never drift into video source checks.
    if str(item.get("detected_category") or "").strip().lower() == "disc":
        _force_tree_category(item, "disc")
    # Normalize child rows first so parent promotion sees final child categories
    # (especially NCOP/NCED => ANIME bonus rows).
    _validate_child_video_items(item)
    if item["detected_category"] == "anime":
        _inherit_anime_context_to_children(item)
    _inherit_category_from_children(item)
    _inherit_anime_context_to_children(item)
    # Re-run promotion after anime-context inheritance for stable parent lock.
    _inherit_category_from_children(item)
    if str(item.get("detected_category") or item.get("category") or "").strip().lower() != "anime":
        _force_video_processing_state(item, folder_category_hint)
    _apply_source_matrix_guard(item)
    _stamp_tree_selection_state(item, resolution)
    _clear_non_target_ignored_flags(item)
    _enforce_child_source_requirement(item)
    _inherit_parent_valid_state(item)
    _exclude_ignored_deferred_children(item, resolution, active_ids)
    _mark_ignored_tree_nodes_completed(item, active_ids)
    _rollup_external_completion(item, active_ids)
    _strip_external_helper_fields(item)
    if is_dir:
        _log_pack_completion_state(item)


def _iter_pending_summary_rows(items: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    rows: List[tuple[str, Dict[str, Any]]] = []
    for category_key, category_items in items.items():
        if category_key in ("tv", "external") or not isinstance(category_items, list):
            continue
        for item in category_items:
            if isinstance(item, dict):
                rows.append((category_key, item))
    for group in items.get("external", []) if isinstance(items.get("external"), list) else []:
        if not isinstance(group, dict):
            continue
        for item in group.get("items", []) or []:
            if isinstance(item, dict):
                rows.append(("external", item))
    return rows


def empty_pending_summary() -> Dict[str, int]:
    """Return the stable pending-summary response shape with zero counts."""
    return {
        "tv_shows": 0,
        "tv_episodes": 0,
        "movies": 0,
        "misc": 0,
        "external": 0,
        "item_total": 0,
        "task_total": 0,
        "red_indexers": 0,
        "total": 0,
    }


def build_pending_summary(items: Dict[str, Any], indexers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Build flat pending counters for external and dynamic categories."""
    movies_items = items.get("movies", []) if isinstance(items.get("movies"), list) else []
    misc_items = items.get("misc", []) if isinstance(items.get("misc"), list) else []
    external_groups = items.get("external", []) if isinstance(items.get("external"), list) else []

    movies_pending = len(movies_items)
    misc_pending = len(misc_items)
    external_pending = sum(len(group.get("items", [])) for group in external_groups if isinstance(group, dict))

    flat_total = 0
    flat_summary: Dict[str, int] = {}
    for category_key, category_items in items.items():
        if category_key in ("tv", "external"):
            continue
        if isinstance(category_items, list):
            count = len(category_items)
            flat_summary[category_key] = count
            flat_total += count

    item_total = flat_total + external_pending
    required_ids = required_pending_indexer_ids(indexers or [])
    task_total = 0
    red_indexer_ids: Set[str] = set()
    for _category_key, row in _iter_pending_summary_rows(items):
        row_status = row.get("indexers") if isinstance(row, dict) else None
        task_total += count_pending_indexer_slots(row_status, required_ids)
        red_indexer_ids.update(incomplete_pending_indexer_ids(row_status, required_ids))

    log_queue_update(red_indexers=len(red_indexer_ids), total_tasks=task_total)
    return {
        **empty_pending_summary(),
        "movies": movies_pending,
        "misc": misc_pending,
        "external": external_pending,
        "item_total": item_total,
        "task_total": task_total,
        "red_indexers": len(red_indexer_ids),
        "total": task_total,
        **{key: value for key, value in flat_summary.items() if key not in ("movies", "misc")},
    }


def scan_pending_snapshot() -> Dict[str, Any]:
    """Run a full filesystem scan and build the pending snapshot payload."""
    # Enable per-scan filesystem walk caching so resolve_explicit_path /
    # detect_content_itype / _build_external_tree_item don't each re-walk
    # the same TV pack folder.
    _scan_cache_token = begin_scan_cache()
    try:
        return _scan_pending_snapshot_inner()
    finally:
        end_scan_cache(_scan_cache_token)


def _scan_external_category_folder(
    category: str,
    folder: Path,
    upload_map: Dict[str, Set[str]],
    completed_lookup: Set[str],
    active_ids: List[str],
    indexer_status_available: bool,
    bulk_selection_by_folder: Dict[str, bool],
    filesize_by_indexer: Optional[Dict[str, Dict[str, int]]] = None,
) -> Optional[Dict[str, Any]]:
    """Build one tv/external-folder group of top-level tree items.

    Top-level rows are built without their children (lazy tree): each folder
    carries child_count and a deferred completion check, and its children load
    one level at a time through build_external_children_snapshot."""
    # Do not hard-bind by watch-folder path; classify from item naming/signatures.
    folder_category_hint = ""
    folder_items: List[Dict[str, Any]] = []
    try:
        entries = sorted(folder.iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        entries = []
    immediate_dirs: List[Path] = []
    loose_files: List[Path] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            immediate_dirs.append(entry)
        else:
            loose_files.append(entry)

    # Treat immediate sub-directories as absolute top-level queue parents.
    for entry in immediate_dirs:
        item, _size = _build_external_tree_item(
            folder.name,
            entry,
            entry.name,
            upload_map,
            completed_lookup,
            active_ids if indexer_status_available else [],
            folder_category_hint=folder_category_hint,
            top_level=True,
            include_children=False,
            filesize_by_indexer=filesize_by_indexer,
        )
        folder_items.append(item)

    # Keep loose files as independent top-level rows.
    for file_entry in loose_files:
        file_item, _file_size = _build_external_tree_item(
            folder.name,
            file_entry,
            file_entry.name,
            upload_map,
            completed_lookup,
            active_ids if indexer_status_available else [],
            folder_category_hint=folder_category_hint,
            top_level=True,
            include_children=False,
            filesize_by_indexer=filesize_by_indexer,
        )
        folder_items.append(file_item)

    if not folder_items:
        return None
    return {
        "source_category": category,
        "key": str(folder),
        "folder_name": folder.name,
        "folder_path": str(folder),
        "allow_bulk_selection": bulk_selection_by_folder.get(_selection_path_identity(folder), True),
        "items": folder_items,
    }


def _count_visible_children(entry: Path) -> int:
    """Direct non-hidden children of a directory (0 for files)."""
    if not entry.is_dir():
        return 0
    try:
        return sum(1 for child in entry.iterdir() if not child.name.startswith("."))
    except OSError:
        return 0


def _scan_regular_category_folder(
    category: str,
    folder: Path,
    upload_map: Dict[str, Set[str]],
    active_ids: List[str],
    indexer_status_available: bool,
    filesize_by_indexer: Optional[Dict[str, Dict[str, int]]] = None,
) -> List[Dict[str, Any]]:
    """Build the flat item rows for one non-tv/external category folder.
    Extracted from _scan_pending_snapshot_inner to keep its own branching down."""
    try:
        entries = sorted(folder.iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        entries = []

    items: List[Dict[str, Any]] = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        item_key = relative_key(entry, folder)
        # Size first: a name match only counts when the stored size still matches.
        if entry.is_dir():
            size = compute_size_uncached(entry)
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
        item_indexers = _lookup_upload_map_indexers(
            upload_map,
            item_key,
            entry.name,
            entry,
            filesize_by_indexer=filesize_by_indexer,
            current_size=size,
        )
        item_status: Dict[str, bool] = (
            {idx_id: (idx_id in item_indexers) for idx_id in active_ids} if indexer_status_available else {}
        )
        detected_meta = _build_detected_item_metadata(entry, category_hint=category)
        detected_category = str(detected_meta.get("detected_category") or category or "misc")
        items.append(
            {
                "name": entry.name,
                "key": item_key,
                "path": str(entry),
                "size": size,
                "itype": detect_content_itype(entry.name, entry, detected_category),
                "detected_category": detected_meta.get("detected_category", ""),
                "detection_method": detected_meta.get("detection_method", ""),
                "detection_confidence": detected_meta.get("detection_confidence", "unknown"),
                "detection_evidence": detected_meta.get("detection_evidence", []),
                "detection_flags": detected_meta.get("detection_flags", []),
                "detection_override": detected_meta.get("detection_override", ""),
                "auto_selectable": detected_meta.get("auto_selectable", True),
                "auto_select_ignored": detected_meta.get("auto_select_ignored", False),
                "auto_select_reason": detected_meta.get("auto_select_reason", ""),
                "indexers": item_status,
                "completed": bool(active_ids) and bool(item_status) and all(item_status.values()),
                "is_dir": entry.is_dir(),
                "child_count": _count_visible_children(entry),
                "_anime_lookup_candidates": list(anime_lookup_candidates(entry)),
            }
        )
    return items


def _scan_pending_snapshot_inner() -> Dict[str, Any]:
    """Inner snapshot builder, wrapped by ``scan_pending_snapshot`` for caching."""
    started = time.perf_counter()
    from core.registry import (
        get_available_categories,
        get_registry,
        resolve_indexer_backfill,
    )

    conf = get_config()
    registry = get_registry()
    active_indexers = [
        {
            "id": indexer.id,
            "name": indexer.name,
            "color": indexer.color,
            "icon": indexer.icon,
            "favicon_url": indexer.favicon_url,
            "backfill": bool(resolve_indexer_backfill(indexer, conf)),
        }
        for indexer in registry.enabled(conf)
    ]
    active_ids = [indexer["id"] for indexer in active_indexers]
    indexer_status_available = True

    db_error: Optional[str] = None
    failed_map: Dict[str, Dict[str, str]] = {}
    filesize_by_indexer: Dict[str, Dict[str, int]] = {}
    try:
        _fully_done, upload_map, failed_map, filesize_by_indexer = database.get_dashboard_data(active_ids)
        completed_lookup = _normalize_dashboard_lookup_values(_fully_done)
    except database.DatabaseOperationalError as exc:
        db_error = str(exc)
        indexer_status_available = False
        logger.warning(f"Pending scan continuing without indexer completion state due to DB error: {exc}")
        upload_map = {}
        completed_lookup = set()

    if not db_error:
        # Feed the scan's indexer context to folder expansion so it needs no separate DB query.
        _store_indexer_context(
            (
                active_indexers,
                active_ids,
                indexer_status_available,
                upload_map,
                completed_lookup,
                db_error,
                failed_map,
                filesize_by_indexer,
            )
        )

    result: Dict[str, Any] = {"tv": [], "movies": [], "misc": [], "external": []}
    categories_cfg = get_configured_category_folders(conf, include_external=True, must_exist=True)
    bulk_selection_by_folder: Dict[str, bool] = {}
    get_folder_path_entries = getattr(conf, "get_folder_path_entries", None)
    folder_entries = (
        get_folder_path_entries() if callable(get_folder_path_entries) else getattr(conf, "folder_paths", [])
    )
    for folder_entry in folder_entries or []:
        if not isinstance(folder_entry, dict):
            continue
        folder_path = str(folder_entry.get("path") or "").strip()
        if not folder_path:
            continue
        bulk_selection_by_folder[_selection_path_identity(folder_path)] = bool(
            folder_entry.get("allow_bulk_selection", True)
        )
    for category, _folder in categories_cfg:
        result.setdefault(category, [])

    external_groups: List[Dict[str, Any]] = []
    for category, folder in categories_cfg:
        if category in ("tv", "external"):
            group = _scan_external_category_folder(
                category,
                folder,
                upload_map,
                completed_lookup,
                active_ids,
                indexer_status_available,
                bulk_selection_by_folder,
                filesize_by_indexer,
            )
            if group is not None:
                external_groups.append(group)
            continue

        result[category].extend(
            _scan_regular_category_folder(
                category,
                folder,
                upload_map,
                active_ids,
                indexer_status_available,
                filesize_by_indexer,
            )
        )

    result["external"] = _sort_external_groups(external_groups)
    skip_config = getattr(conf, "skip_files", None)
    if isinstance(skip_config, dict) and skip_config.get("enabled"):
        stamp_skip_flags(result, skip_config)
    stamp_filepart_flags(result)
    if indexer_status_available and failed_map:
        _stamp_failed_indexer_flags(result, failed_map, active_ids)
    external_search_index, external_metadata_zlib = _compact_external_groups(result["external"])

    payload = {
        "items": result,
        "indexers": active_indexers,
        "indexer_status_available": indexer_status_available,
        "skip_files": {
            "enabled": skip_config.get("enabled", False) if isinstance(skip_config, dict) else False,
            "display_mode": (
                skip_config.get("display_mode", "disabled") if isinstance(skip_config, dict) else "disabled"
            ),
        },
        "categories": get_available_categories(),
        "summary": build_pending_summary(result, active_indexers if indexer_status_available else []),
        "db_error": db_error,
        "cached_at": time.time(),
        # Private server-side data. API response shaping removes underscore
        # fields before serializing the snapshot.
        "_external_search_index": external_search_index,
        "_external_metadata_zlib": external_metadata_zlib,
    }
    summary = payload.get("summary", {})
    log_backend_timing(
        "scan_pending_all",
        started,
        context=(
            f"tv={summary.get('tv_shows', 0)} movies={summary.get('movies', 0)} "
            f"misc={summary.get('misc', 0)} external={summary.get('external', 0)} "
            f"tasks={summary.get('total', 0)}"
        ),
        warn_threshold_s=1.0,
    )
    return payload


def _resolve_filter_categories(category: str, source: Dict[str, List[Any]]) -> List[str]:
    if category == "all":
        return list(source.keys())
    if category == "tv":
        return [c for c in ["tv", "external"] if c in source]
    if category in source:
        return [category]
    return []


def _is_match(raw_query: Optional[str], target: str, literal: bool) -> bool:
    """Shared search-match rule for every category branch below.

    Kept as a plain module-level helper (not a nested closure) so its own
    branches are what get measured, rather than folding into whichever
    category function calls it.
    """
    if not raw_query:
        return True
    if literal:
        return raw_query.lower() in target.lower()
    target_clean = target.lower()
    query_clean = raw_query.lower()
    for token in "._-[]()":
        target_clean = target_clean.replace(token, " ")
        query_clean = query_clean.replace(token, " ")
    words = [word for word in query_clean.split() if word]
    return not words or all(word in target_clean for word in words)


def _filter_tv_season(
    season: Dict[str, Any], query: Optional[str], literal: bool, indexer_ids: List[str]
) -> Optional[tuple]:
    """Return (filtered_season, size, episode_count, indexer_status), or None if the
    season has no matching items."""
    season_items = season.get("items") or []
    keep_items = [item for item in season_items if _is_match(query, item.get("name", ""), literal)]
    if not keep_items:
        return None

    season_status: Dict[str, bool] = {idx_id: True for idx_id in indexer_ids}
    for item in keep_items:
        item_status = item.get("indexers") or {}
        for idx_id in indexer_ids:
            if not item_status.get(idx_id, False):
                season_status[idx_id] = False

    season_size = sum(int(item.get("size") or 0) for item in keep_items)
    season_episodes = sum(1 for item in keep_items if item.get("itype") == "TV Episode")
    filtered_season = {
        **season,
        "items": keep_items,
        "indexers": season_status,
        "size": season_size,
        "episode_count": season_episodes,
        "expanded": False,
    }
    return filtered_season, season_size, season_episodes, season_status


def _filter_tv_show(
    show: Dict[str, Any], query: Optional[str], literal: bool, indexer_ids: List[str]
) -> Optional[Dict[str, Any]]:
    if not query or _is_match(query, show.get("name", ""), literal):
        return show

    keep_seasons = []
    show_size = 0
    show_episodes = 0
    show_status: Dict[str, bool] = {idx_id: True for idx_id in indexer_ids}
    for season in show.get("seasons") or []:
        result = _filter_tv_season(season, query, literal, indexer_ids)
        if result is None:
            continue
        filtered_season, season_size, season_episodes, season_status = result
        keep_seasons.append(filtered_season)
        show_size += season_size
        show_episodes += season_episodes
        for idx_id in indexer_ids:
            if not season_status[idx_id]:
                show_status[idx_id] = False

    if not keep_seasons:
        return None
    return {
        **show,
        "seasons": keep_seasons,
        "indexers": show_status,
        "size": show_size,
        "episode_count": show_episodes,
    }


def _filter_tv_category(
    shows: List[Dict[str, Any]], query: Optional[str], literal: bool, indexer_ids: List[str]
) -> List[Dict[str, Any]]:
    out = []
    for show in shows:
        filtered_show = _filter_tv_show(show, query, literal, indexer_ids)
        if filtered_show is not None:
            out.append(filtered_show)
    return out


def _filter_external_category(
    groups: List[Dict[str, Any]],
    category: str,
    query: Optional[str],
    literal: bool,
    external_search_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    detected_filter = category if category not in ("all", "external") else None
    out = []
    for group in groups:
        items_pool = group.get("items", [])
        if detected_filter:
            if group.get("source_category") == detected_filter:
                items_pool = list(items_pool)
            else:
                items_pool = [i for i in items_pool if i.get("detected_category") == detected_filter]
        if not items_pool:
            continue
        if not query:
            out.append({**group, "items": items_pool})
            continue
        keep_items = [
            item
            for item in items_pool
            if _is_match(
                query,
                str(external_search_index.get(str(item.get("key") or "")) or item.get("name", "")),
                literal,
            )
        ]
        if keep_items:
            out.append({**group, "items": keep_items})
    return out


def _filter_generic_category(
    items: List[Dict[str, Any]], query: Optional[str], literal: bool
) -> List[Dict[str, Any]]:
    return [item for item in items if not query or _is_match(query, item["name"], literal)]


def filter_pending_snapshot(
    data: Dict[str, Any], search: Optional[str], category: str, literal: bool = False
) -> Dict[str, Any]:
    """Apply search and category filters to a cached pending snapshot."""
    if not data:
        return {
            "items": {"tv": [], "movies": [], "misc": [], "external": []},
            "indexers": [],
            "summary": empty_pending_summary(),
            "categories": data.get("categories", []) if data else [],
            "indexer_status_available": data.get("indexer_status_available", True) if data else True,
            "db_error": data.get("db_error") if data else None,
        }

    source = data["items"]
    all_categories = list(source.keys())
    categories = _resolve_filter_categories(category, source)
    filtered: Dict[str, List[Any]] = {category_key: [] for category_key in all_categories}
    query = search.strip() if search else None
    indexer_ids: List[str] = [
        str(indexer.get("id"))
        for indexer in (data.get("indexers") or [])
        if isinstance(indexer, dict) and indexer.get("id")
    ]
    external_search_index = data.get("_external_search_index")
    if not isinstance(external_search_index, dict):
        external_search_index = {}

    for category_key in categories:
        if category_key not in source:
            continue
        if category_key == "tv":
            filtered["tv"] = _filter_tv_category(source[category_key], query, literal, indexer_ids)
        elif category_key == "external":
            filtered["external"] = _filter_external_category(
                source.get(category_key, []), category, query, literal, external_search_index
            )
        else:
            filtered[category_key] = _filter_generic_category(source[category_key], query, literal)

    return {
        "items": filtered,
        "indexers": data["indexers"],
        "summary": build_pending_summary(
            filtered,
            (data.get("indexers") or []) if data.get("indexer_status_available", True) else [],
        ),
        "cached_at": data.get("cached_at"),
        "skip_files": data.get("skip_files", {"enabled": False, "display_mode": "disabled"}),
        "categories": data.get("categories", []),
        "indexer_status_available": data.get("indexer_status_available", True),
        "db_error": data.get("db_error"),
    }


_IndexerContext = tuple[
    List[Dict[str, Any]],
    List[str],
    bool,
    Dict[str, Set[str]],
    Set[str],
    Optional[str],
    Dict[str, Dict[str, str]],
    Dict[str, Dict[str, int]],
]

# Stale-while-revalidate cache for _get_pending_indexer_context: serve cached data
# at once and refresh in the background after the TTL, so folder expansion never
# blocks on the database while a scan holds the pool. Guarded by a lock.
_INDEXER_CTX_LOCK = threading.Lock()
_INDEXER_CTX_CACHE: Optional[_IndexerContext] = None
_INDEXER_CTX_CACHE_TS: float = 0.0
_INDEXER_CTX_CACHE_TTL: float = 60.0
_INDEXER_CTX_REFRESH_RUNNING: bool = False


def _store_indexer_context(context: _IndexerContext) -> None:
    global _INDEXER_CTX_CACHE, _INDEXER_CTX_CACHE_TS, _INDEXER_CTX_REFRESH_RUNNING
    with _INDEXER_CTX_LOCK:
        _INDEXER_CTX_CACHE = context
        _INDEXER_CTX_CACHE_TS = time.monotonic()
        _INDEXER_CTX_REFRESH_RUNNING = False


def invalidate_pending_indexer_context() -> None:
    """Drop the cached indexer context (after an upload or an indexer toggle)."""
    global _INDEXER_CTX_CACHE, _INDEXER_CTX_CACHE_TS
    with _INDEXER_CTX_LOCK:
        _INDEXER_CTX_CACHE = None
        _INDEXER_CTX_CACHE_TS = 0.0


def _get_pending_indexer_context() -> _IndexerContext:
    global _INDEXER_CTX_REFRESH_RUNNING
    with _INDEXER_CTX_LOCK:
        cache = _INDEXER_CTX_CACHE
        age = time.monotonic() - _INDEXER_CTX_CACHE_TS
        if cache is not None and age < _INDEXER_CTX_CACHE_TTL:
            return cache
        if cache is not None:
            # Stale but present: return it now and refresh in the background.
            if not _INDEXER_CTX_REFRESH_RUNNING:
                _INDEXER_CTX_REFRESH_RUNNING = True
                threading.Thread(target=_refresh_indexer_ctx_bg, daemon=True).start()
            return cache

    # No cache at all (first call after startup): block once.
    result = _get_pending_indexer_context_fresh()
    if not result[5]:
        _store_indexer_context(result)
    return result


def _refresh_indexer_ctx_bg() -> None:
    """Background thread: refresh the indexer context cache without blocking callers."""
    global _INDEXER_CTX_REFRESH_RUNNING
    try:
        result = _get_pending_indexer_context_fresh()
        if not result[5]:
            _store_indexer_context(result)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("[pending] Background indexer ctx refresh failed")
    finally:
        with _INDEXER_CTX_LOCK:
            _INDEXER_CTX_REFRESH_RUNNING = False


def prewarm_pending_indexer_context() -> None:
    """Warm the indexer context cache in a daemon thread (call once at app startup)."""
    threading.Thread(target=_get_pending_indexer_context, daemon=True).start()


def _get_pending_indexer_context_fresh() -> _IndexerContext:
    from core.registry import get_registry, resolve_indexer_backfill

    conf = get_config()
    registry = get_registry()
    active_indexers = [
        {
            "id": indexer.id,
            "name": indexer.name,
            "color": indexer.color,
            "icon": indexer.icon,
            "favicon_url": indexer.favicon_url,
            "backfill": bool(resolve_indexer_backfill(indexer, conf)),
        }
        for indexer in registry.enabled(conf)
    ]
    active_ids = [indexer["id"] for indexer in active_indexers]
    indexer_status_available = True
    db_error: Optional[str] = None
    failed_map: Dict[str, Dict[str, str]] = {}
    filesize_by_indexer: Dict[str, Dict[str, int]] = {}
    try:
        _fully_done, upload_map, failed_map, filesize_by_indexer = database.get_dashboard_data(active_ids)
        completed_lookup = _normalize_dashboard_lookup_values(_fully_done)
    except database.DatabaseOperationalError as exc:
        db_error = str(exc)
        indexer_status_available = False
        logger.warning(f"Pending scan continuing without indexer completion state due to DB error: {exc}")
        upload_map = {}
        completed_lookup = set()
    return (
        active_indexers,
        active_ids,
        indexer_status_available,
        upload_map,
        completed_lookup,
        db_error,
        failed_map,
        filesize_by_indexer,
    )


def build_external_children_snapshot(
    external_folder_name: str,
    parent_path: Path,
    parent_rel_path: str,
    *,
    folder_category_hint: str = "",
) -> Dict[str, Any]:
    started = time.perf_counter()
    (
        _active_indexers,
        active_ids,
        indexer_status_available,
        upload_map,
        completed_lookup,
        _db_error,
        _failed_map,
        filesize_by_indexer,
    ) = _get_pending_indexer_context()

    children: List[Dict[str, Any]] = []
    try:
        entries = sorted(parent_path.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        entries = []

    for child in entries:
        if child.name.startswith("."):
            continue
        child_rel = f"{parent_rel_path}/{child.name}" if parent_rel_path else child.name
        child_item, _child_size = _build_external_tree_item(
            external_folder_name,
            child,
            child_rel,
            upload_map,
            completed_lookup,
            active_ids if indexer_status_available else [],
            folder_category_hint=folder_category_hint,
            top_level=False,
            include_children=False,
            filesize_by_indexer=filesize_by_indexer,
        )
        child_item["has_filepart"] = child.name.endswith(".filepart") or _has_filepart_path(str(child))
        children.append(child_item)

    if children:
        # One resolution of the expanded folder gives this level its ignore
        # state (extras, samples, sidecars) without building deeper levels.
        try:
            resolution = resolve_explicit_path(
                parent_path,
                category_hint=folder_category_hint,
                anime_lookup=_anime_cache_lookup,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Lazy child resolution failed for {parent_path}: {exc}")
            resolution = None
        if resolution is not None:
            _stamp_lazy_children_selection(children, resolution)
        for child_item in children:
            _clear_non_target_ignored_flags(child_item)
    # Episode files inside a pack named "Show.S01.WEB-DL" must carry their own
    # source token; the parent folder name does not count for individual upload.
    _enforce_child_source_requirement({"children": children})
    for child_item in children:
        _mark_ignored_tree_nodes_completed(child_item, active_ids if indexer_status_available else [])
        _strip_external_helper_fields(child_item)

    log_backend_timing(
        "scan_pending_children",
        started,
        context=f"path={parent_path} children={len(children)}",
        warn_threshold_s=0.5,
    )
    return {"children": children, "child_count": len(children)}


def _resolve_external_child_request(
    data: Dict[str, Any],
    key: str,
    path: str,
) -> Optional[tuple[str, Path, str, str]]:
    """Validate a lazy-child request against the configured snapshot roots."""
    target_key = str(key or "").strip()
    target_path_text = str(path or "").strip()
    if not target_key.startswith("ext:") or not target_path_text:
        return None

    key_parts = target_key.split(":", 2)
    if len(key_parts) != 3:
        return None
    _prefix, external_folder_name, parent_rel_path = key_parts

    items = data.get("items")
    groups = items.get("external") if isinstance(items, dict) else None
    if not isinstance(groups, list):
        return None

    target_path = Path(target_path_text)
    try:
        resolved_target = target_path.resolve(strict=True)
    except OSError:
        return None
    if not resolved_target.is_dir():
        return None

    for group in groups:
        if not isinstance(group, dict) or str(group.get("folder_name") or "") != external_folder_name:
            continue
        root_text = str(group.get("folder_path") or "").strip()
        if not root_text:
            continue
        try:
            root_path = Path(root_text).resolve(strict=True)
            actual_rel_path = resolved_target.relative_to(root_path).as_posix()
        except (OSError, ValueError):
            continue
        if actual_rel_path != parent_rel_path.replace("\\", "/").strip("/"):
            continue

        folder_category_hint = ""
        for top_item in group.get("items", []) or []:
            if not isinstance(top_item, dict):
                continue
            top_path_text = str(top_item.get("path") or "").strip()
            if not top_path_text:
                continue
            try:
                resolved_target.relative_to(Path(top_path_text).resolve(strict=False))
            except (OSError, ValueError):
                continue
            folder_category_hint = str(
                top_item.get("detected_category") or top_item.get("category") or ""
            ).strip().lower()
            break

        return external_folder_name, resolved_target, actual_rel_path, folder_category_hint
    return None


def build_external_children_for_request(data: Dict[str, Any], key: str, path: str) -> Dict[str, Any]:
    """Resolve, authorize, and build one lazy external-directory response."""
    resolved = _resolve_external_child_request(data, key, path)
    if resolved is None:
        return {"children": [], "child_count": 0}
    external_folder_name, parent_path, parent_rel_path, folder_category_hint = resolved
    compact_metadata = _load_external_metadata(data)
    if compact_metadata:
        children: List[Dict[str, Any]] = []
        try:
            entries = sorted(parent_path.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            entries = []
        missing_metadata = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            cached = compact_metadata.get(_selection_path_identity(entry))
            if cached is None:
                missing_metadata = True
                break
            child = dict(cached)
            child["children"] = []
            child["files"] = []
            children.append(child)
        if not missing_metadata:
            return {"children": children, "child_count": len(children)}

    return build_external_children_snapshot(
        external_folder_name,
        parent_path,
        parent_rel_path,
        folder_category_hint=folder_category_hint,
    )
