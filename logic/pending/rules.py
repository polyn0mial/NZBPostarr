"""Pending-tree rules: category inheritance, child validation and the source matrix."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from logic.classify.anime import cached_lookup
from core.media import EXTENSION_FIRST_VIDEO
from logic.classify.names import classify_video_name_result
from logic.classify.patterns import ANIME_BONUS_RE


_EPISODE_TAG_RE = re.compile(r"S\d{1,2}[.\s_&-]*E\d{1,3}", re.IGNORECASE)

_SOURCE_EXEMPT_NAME_RE = re.compile(
    r"(?i)(?:\.(?:mp3|flac|m4a|aac|ogg|opus|wav|wma|aif|aiff|alac|ape|mka|cue)(?:$|\b)|\b(?:music|audiobook|audiobooks|ebook|ebooks|disc|cd|vinyl|lossless|podcast)\b)"
)

_SEASON_MARKER_RE = re.compile(r"(?:\bS\d{1,2}\b|\bS\d{4}\b|\bS\d{1,2}X?E\d{1,3}\b|\bSeason\b|\b\d{1,2}x\d{1,3}\b|\bEp(?:isode)?\.?\s?\d{1,3}\b)", re.IGNORECASE)

_SCENE_VIDEO_TAG_RE = re.compile(
    r"(?:\b(?:19|20)\d{2}\b|\b(?:480|576|720|1080|1440|2160|4320)[pi]\b|\b(?:bluray|bdrip|brrip|webrip|web[-_.\s]?dl|remux|hdtv|dvdrip|x26[45]|h\.?26[45])\b)",
    re.IGNORECASE,
)

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
    return suffix in EXTENSION_FIRST_VIDEO and bool(
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
    if ANIME_BONUS_RE.search(f"{name} {ptxt}".lower()):
        return
    if not _has_video_container_and_source_signal(name, ptxt):
        return
    classification = classify_video_name_result(
        name,
        fallback_folder_hint,
        anime_lookup=cached_lookup,
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
                if ANIME_BONUS_RE.search(name_text):
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
        if ANIME_BONUS_RE.search(str(c.get("name") or "").lower())
    )
    parent_anime_cached = cached_lookup(str(node.get("name") or "")) is True
    has_desc_anime_cached = any(
        cached_lookup(str(c.get("name") or "")) is True for c in children
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
