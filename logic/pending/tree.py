"""The pending tree: a lazy one-level scan of the configured roots with deferred completion."""

from __future__ import annotations

import json
import os
import time
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from core.db import engine as db_engine
from core.db import ledger as db_ledger
from core.config import get_config
from core.fs import compute_size_uncached
from core.logging import log_backend_timing
from core.paths import path_key
from logic.classify.anime import anime_lookup_candidates, cached_lookup
from logic.classify.content import (
    category_for_itype,
    classify_standalone_file_category,
    detect_content_itype,
)
from logic.classify.explicit import resolve_explicit_path
from logic.classify.patterns import ANIME_BONUS_RE
from logic.classify.walk import begin_scan_cache, end_scan_cache
from logic.pending.completion import (
    _clear_non_target_ignored_flags,
    _compute_external_item_indexer_status,
    _log_pack_completion_state,
    _lookup_upload_map_indexers,
    _mark_ignored_tree_nodes_completed,
    _normalize_dashboard_lookup_values,
    _rollup_external_completion,
    _stamp_failed_indexer_flags,
    _store_indexer_context,
    stamp_skip_flags,
)
from logic.pending.roots import get_configured_category_folders, relative_key
from logic.pending.rules import (
    _apply_source_matrix_guard,
    _enforce_child_source_requirement,
    _force_tree_category,
    _force_video_processing_state,
    _inherit_anime_context_to_children,
    _inherit_category_from_children,
    _inherit_parent_valid_state,
    _validate_child_video_items,
)
from logic.pending.selection import _stamp_tree_selection_state
from logic.pending.view import (
    _compact_external_groups,
    _sort_external_groups,
    _strip_external_helper_fields,
    build_pending_summary,
)


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
    ignored = {path_key(entry.path) for entry in getattr(resolution, "ignored_paths", ()) or ()}
    required = [indexers for identity, indexers in per_child.items() if identity not in ignored]
    item["_deferred_children_done"] = {
        idx_id: bool(required) and all(idx_id in indexers for indexers in required) for idx_id in active_ids
    }

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
        anime_lookup=cached_lookup,
    )
    ignored_reason = next(
        (ignored.reason for ignored in getattr(resolution, "ignored_paths", ()) if ignored.path == entry),
        "",
    )
    if ignored_reason:
        detected_category = resolution.category.strip().lower()
        detected_itype = resolution.itype.strip().lower()
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
    if ANIME_BONUS_RE.search(raw_text):
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
                deferred_child_indexers[path_key(child)] = set(child_indexers)
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

def _apply_nested_external_item_category(item: Dict[str, Any], node: Path, folder_category_hint: str) -> None:
    """Populate detection/category fields for a non-top-level external tree item.

    Extracted from _build_external_tree_item to keep its own branching down;
    mutates item in place.
    """
    hint_category = folder_category_hint.strip().lower()
    if node.is_file():
        item["itype"] = detect_content_itype(node.name, node, folder_category_hint)
        # Nested rows keep their parent's category (as on the server) so a
        # child pill never falls back to another type.
        nested_category = hint_category or category_for_itype(item["itype"])
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
            anime_lookup=cached_lookup,
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
    forced_category = classify_standalone_file_category(node)
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
        "allow_bulk_selection": bulk_selection_by_folder.get(path_key(folder), True),
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
    from core.indexers.categories import get_available_categories
    from core.indexers.registry import get_registry
    from core.indexers.models import resolve_indexer_backfill

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
        _fully_done, upload_map, failed_map, filesize_by_indexer = db_ledger.completion_index(active_ids)
        completed_lookup = _normalize_dashboard_lookup_values(_fully_done)
    except db_engine.DatabaseOperationalError as exc:
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
        bulk_selection_by_folder[path_key(folder_path)] = bool(
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
