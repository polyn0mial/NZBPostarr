"""Pending completion: per-indexer upload state stamped onto the tree and rolled up."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from core.db import engine as db_engine
from core.db import ledger as db_ledger
from core.config import get_config
from core.utils import should_skip_file


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
                    if db_ledger.size_matches(sizes.get(idx_id), current_size) is not False
                }
        matches.update(idx_ids)

    return matches

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
