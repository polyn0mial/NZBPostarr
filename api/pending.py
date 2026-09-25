"""The Pending page API: /api/pending."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.deps import _filter_bulk_selectable_items, _log_selected_payload
from core.db import uploads as db_uploads
from core.config import get_config
from core.utils import VIDEO_EXTENSIONS
from logic.pending import children as pending_children
from logic.pending import index as pending_index
from logic.pending import tree as pending_tree
from logic.pending import view as pending_view
from logic.pending.selection import stamp_upload_itype
from logic.pending.index import get_pending_index_manager
from logic.pending.roots import get_configured_folders
from logic.jobs.models import ProcessingJobRequest
from logic.services import get_upload_service, UploadService


router = APIRouter(prefix="/api/pending", tags=["pending"])

_pending_index = get_pending_index_manager()

_anime_check_lock = threading.Lock()

_pending_refresh_lock = threading.Lock()

_anime_check_inflight: bool = False  # True while Jikan background check is running

_anime_check_thread: Optional[threading.Thread] = None

def _wait_for_pending_snapshot(max_wait_s: float = 2.0, poll_s: float = 0.1) -> Dict[str, Any]:
    """Briefly wait for the background pending index to become ready.

    This avoids a cold-start UX where clients cache an all-zero response before
    the first background refresh has finished.
    """
    deadline = time.time() + max(0.0, max_wait_s)
    state = _pending_index.get_state()
    while time.time() < deadline:
        if state.get("snapshot") is not None:
            break
        time.sleep(max(0.01, poll_s))
        state = _pending_index.get_state()
    return state

def _scan_pending_all() -> Dict[str, Any]:
    return pending_tree.scan_pending_snapshot()

def _refresh_pending_snapshot_now(reason: str = "manual") -> Dict[str, Any]:
    """Rebuild the pending snapshot synchronously and replace the cache."""
    if not _pending_refresh_lock.acquire(blocking=True, timeout=30):
        # Timed out waiting for lock - return cached data.
        state = _pending_index.get_state()
        cached = state.get("snapshot")
        if isinstance(cached, dict):
            return cached
        return {}
    start = time.perf_counter()
    try:
        data = _scan_pending_all()
        try:
            _pending_index.set_snapshot(data)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Pending snapshot cache update failed ({reason}); returning live scan: {exc}")
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        logger.debug(
            "Pending index synchronous refresh completed "
            f"({reason}, total={summary.get('total', 0)}, elapsed={time.perf_counter() - start:.3f}s)"
        )
        return data
    finally:
        _pending_refresh_lock.release()

def _background_anime_check(data: Dict[str, Any]) -> None:
    """Check candidate video titles against Jikan and refresh cache if needed.

    Runs in a daemon thread.  Rate-limit-safe - ``check_titles_batch``
    respects 3/sec and 60/min limits internally.
    """
    global _anime_check_inflight, _anime_check_thread
    from logic.classify.anime import check_titles_batch

    names = pending_index.collect_uncached_anime_check_names(data)
    if not names:
        return

    with _anime_check_lock:
        _anime_check_inflight = True
    try:
        # Check if anime checking is enabled in config
        if not getattr(get_config(), "enable_anime_checking", False):
            logger.debug("Anime check: disabled in settings - skipping Jikan lookups")
            return

        logger.debug(f"Anime check: querying Jikan for {len(names)} candidate title(s)")
        results = check_titles_batch(names)

        found = [n for n, v in results.items() if v is True]
        if found:
            logger.info(f"Anime cache: {len(found)} title(s) confirmed as anime - re-scanning pending list")
            try:
                fresh_data = _scan_pending_all()
                _pending_index.set_snapshot(fresh_data)
                logger.debug("Anime check: pending cache updated with anime classifications")
            except Exception as _exc:
                logger.debug(f"Anime check re-scan failed: {_exc}")
        else:
            logger.debug("Anime check: no new anime found among candidate titles")
    finally:
        with _anime_check_lock:
            _anime_check_inflight = False
            _anime_check_thread = None

@router.get("/summary")
def get_pending_summary() -> Dict[str, Any]:
    """Return lightweight summary counts from cache (no filesystem scan)."""
    state = _pending_index.get_state()
    data = state.get("snapshot")

    if not data:
        _pending_index.request_refresh(reason="summary-cold")
        state = _wait_for_pending_snapshot(max_wait_s=2.0, poll_s=0.1)
        data = state.get("snapshot")

    if not data:
        return {
            "summary": pending_view.empty_pending_summary(),
            "categories": [],
            "indexers": [],
            "cached_at": None,
            "indexer_status_available": True,
            "db_error": None,
            "ready": False,
            "refreshing": bool(state.get("refreshing", False)),
        }

    return {
        "summary": data.get("summary", {}),
        "categories": data.get("categories", []),
        "indexers": data.get("indexers", []),
        "cached_at": data.get("cached_at"),
        "indexer_status_available": data.get("indexer_status_available", True),
        "db_error": data.get("db_error"),
        "ready": bool(state.get("ready", True)),
        "refreshing": bool(state.get("refreshing", False)),
    }

@router.get("/order")
def get_pending_group_order() -> Dict[str, Any]:
    """Return the shared pending-directory order and lock state."""
    conf = get_config()
    order = getattr(conf, "pending_external_group_order", []) or []
    if not isinstance(order, list):
        order = []
    return {
        "order": [str(key) for key in order if str(key).strip()],
        "locked": bool(getattr(conf, "pending_external_group_order_locked", False)),
    }

@router.get("/items")
def get_pending_items(
    search: Optional[str] = None,
    category: str = "all",
    literal: bool = False,
    refresh: bool = False,
    known_cached_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Return pending items with a server-side cache."""
    global _anime_check_thread
    state = _pending_index.get_state()
    data = state.get("snapshot")

    if refresh:
        # Synchronous refresh so the Refresh button returns fresh data immediately.
        _refresh_pending_snapshot_now(reason="items-refresh")
        state = _pending_index.get_state()
        data = state.get("snapshot")

    if not data:
        _pending_index.request_refresh(reason="items-cold")
        state = _wait_for_pending_snapshot(max_wait_s=2.0, poll_s=0.1)
        data = state.get("snapshot")

    if not data:
        res = pending_view.filter_pending_snapshot({}, search, category, literal)
        res["ready"] = False
        res["refreshing"] = True
        res["anime_detecting"] = _anime_check_inflight
        return res

    current_cached_at = data.get("cached_at")
    if (
        not refresh
        and known_cached_at is not None
        and current_cached_at is not None
        and known_cached_at == current_cached_at
    ):
        return {
            "not_modified": True,
            "cached_at": current_cached_at,
            "ready": bool(state.get("ready", True)),
            "refreshing": bool(state.get("refreshing", False)),
            "anime_detecting": _anime_check_inflight,
        }

    anime_enabled = bool(getattr(get_config(), "enable_anime_checking", False))
    if anime_enabled and pending_index.collect_uncached_anime_check_names(data):
        with _anime_check_lock:
            can_start = not _anime_check_inflight and (_anime_check_thread is None or not _anime_check_thread.is_alive())
            if can_start:
                _anime_check_thread = threading.Thread(target=_background_anime_check, args=(data,), daemon=True, name="pending-anime-check")
                _anime_check_thread.start()

    if not search and category == "all" and not literal:
        res = dict(data)
    else:
        res = pending_view.filter_pending_snapshot(data, search, category, literal)

    # Return only top-level pending rows.
    # Build fresh containers so the shared cached snapshot remains untouched.
    res["items"] = _slim_pending_items(res.get("items"))
    for private_key in [key for key in res if str(key).startswith("_")]:
        res.pop(private_key, None)

    if refresh or bool(state.get("refreshing", False)):
        res["stale"] = True
    res["ready"] = bool(state.get("ready", True))
    res["refreshing"] = bool(state.get("refreshing", False))
    res["anime_detecting"] = _anime_check_inflight
    return res

@router.get("/children")
def get_pending_children(
    key: str = "",
    path: str = "",
) -> Dict[str, Any]:
    """Fetch children lazily for a specific pending directory node."""
    state = _pending_index.get_state()
    data = state.get("snapshot") or {}
    return pending_children.build_external_children_for_request(data, key, path)

@router.post("/anime-cache")
def correct_pending_anime_cache(req: AnimeCacheCorrectionRequest) -> Dict[str, Any]:
    """Store a user-confirmed anime verdict and refresh pending classifications."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A title name is required")

    from logic.classify.anime import set_cached

    if not set_cached(name, req.is_anime):
        raise HTTPException(status_code=400, detail="The title could not be normalized")

    from logic.classify.names import classify_video_name

    itype = classify_video_name(
        name,
        assume_movie_if_unknown=False,
        anime_lookup=lambda _name: req.is_anime,
    )
    category = {
        "Anime": "anime",
        "TV Show": "tv",
        "Movie": "movies",
    }.get(itype, "misc")
    _pending_index.request_refresh(reason="anime-cache-correction")
    return {
        "status": "success",
        "name": name,
        "is_anime": req.is_anime,
        "category": category,
    }

@router.post("/force-upload")
async def force_upload_items(
    req: ForceUploadRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Force-upload specific items from the pending queue.

    Starts one unified job for the selected items.
    """
    if not req.items:
        raise HTTPException(status_code=400, detail="No items specified")

    selected_items: List[Dict[str, Any]] = list(req.items)
    excluded_count = 0
    if req.bulk_selection:
        selected_items, excluded_count = _filter_bulk_selectable_items(selected_items, get_config())
    if not selected_items:
        raise HTTPException(
            status_code=400,
            detail="All selected items belong to folders excluded from mass selection",
        )

    collapsed_items = _collapse_force_upload_items(selected_items)
    valid_items = [item for item in collapsed_items if str(item.get("path") or "").strip() and str(item.get("category") or "").strip()]
    if len(valid_items) != len(collapsed_items):
        raise HTTPException(status_code=400, detail="One or more items are missing a valid category")
    _log_selected_payload("FORCE-UPLOAD", valid_items)

    categories = {str(item.get("category") or "").strip().lower() for item in valid_items if str(item.get("category") or "").strip()}
    request_category = next(iter(categories)) if len(categories) == 1 else "mixed"
    job_ids = service.start_processing_job_requests(
        [
            ProcessingJobRequest(
                category=request_category,
                test_mode=req.test_mode,
                target_indexer_id=req.indexer_id,
                paths=tuple(str(item["path"]) for item in valid_items),
                item_hints=tuple(dict(item) for item in valid_items),
                enable_duplicate_check=req.enable_duplicate_check,
                # Preserve an explicit API force override; otherwise respect enable_duplicate_check.
                force=None if req.force is None else bool(req.force),
                # Skip pre-flight pack expansion - paths are already explicit;
                # expansion happens lazily inside the job thread instead of here.
                skip_pack_expansion=True,
            )
        ],
        source="pending-force-upload",
        reuse_running=False,
    )

    return {
        "status": "started",
        "job_ids": job_ids,
        "items_count": len(collapsed_items),
        "bulk_excluded": excluded_count,
    }

class PendingGroupOrderRequest(BaseModel):
    """API request model for shared pending-directory ordering."""

    order: List[str] = Field(default_factory=list)

class PendingGroupOrderLockedRequest(BaseModel):
    """API request model for shared pending-directory lock state."""

    locked: bool = False

class MarkUploadedRequest(BaseModel):
    """API request model for marking items as uploaded."""

    item_keys: List[str]
    indexer_ids: List[str]
    itype: str = "Misc"

class ForceUploadRequest(BaseModel):
    """API request model for force-uploading specific pending items."""

    items: List[Dict[str, str]]  # [{path, category, itype}]
    enable_duplicate_check: bool = True
    test_mode: bool = False
    indexer_id: Optional[str] = None
    force: Optional[bool] = None
    bulk_selection: bool = False

class CategoryOverrideRequest(BaseModel):
    """API request model for persisting a manual category override for a pending item."""

    key: str
    category: Optional[str] = None

class AnimeCacheCorrectionRequest(BaseModel):
    """Persist a user correction for one title's anime detector result."""

    name: str
    is_anime: bool

def _normalize_force_upload_path(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        normalized = Path(text).resolve().as_posix()
    except OSError:
        normalized = Path(text).as_posix()
    normalized = normalized.rstrip("/")
    return normalized.casefold() if os.name == "nt" else normalized

def _force_upload_dir_direct_video_count(path: Path) -> int:
    try:
        return sum(1 for child in path.iterdir() if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS)
    except OSError:
        return 0

def _force_upload_dir_recursive_video_count(path: Path) -> int:
    try:
        return sum(1 for child in path.rglob("*") if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS)
    except OSError:
        return 0

def _pending_watch_folders(conf: Any) -> list[Path]:
    """Return existing configured folders that should invalidate pending index on change."""
    return get_configured_folders(conf, must_exist=True)

def _slim_pending_node(node: Any) -> Any:
    """Strip a pending-tree node down to its top-level fields.

    Descendants are fetched through the lazy /children route, so copying
    megabytes of nested data here only to render collapsed rows wastes
    network, JSON, Vue reactivity, and memory.
    """
    if not isinstance(node, dict):
        return node
    slim = {key: value for key, value in node.items() if not str(key).startswith("_")}
    stamp_upload_itype(slim)
    raw_children = node.get("children")
    raw_files = node.get("files")
    child_source = raw_children if isinstance(raw_children, list) else raw_files
    if isinstance(child_source, list):
        if child_source or "child_count" not in slim:
            slim["child_count"] = len([child for child in child_source if isinstance(child, dict)])
        if "children" in slim:
            slim["children"] = []
        if "files" in slim:
            slim["files"] = []
    return slim

def _should_preserve_force_upload_dir(item: dict[str, Any], category: str, raw_path: Path) -> bool:
    """Keep real TV season-pack directories even when selected alongside child episodes."""
    if category != "tv" or not raw_path.exists() or not raw_path.is_dir():
        return False

    direct_video_count = _force_upload_dir_direct_video_count(raw_path)
    if direct_video_count >= 1:
        return True

    folder_name = raw_path.name.lower()
    season_like_name = bool(re.search(r"(?:^|[^a-z0-9])s\d{2}(?:[^a-z0-9]|$)", folder_name)) or any(
        token in folder_name for token in ("season", "complete")
    )
    if not season_like_name:
        return False

    return _force_upload_dir_recursive_video_count(raw_path) >= 2

def _collapse_force_upload_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer the most specific selected paths when a batch contains ancestor + child entries."""
    grouped: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for idx, item in enumerate(items):
        category = str(item.get("category") or "").strip().lower()
        normalized_path = _normalize_force_upload_path(item.get("path"))
        if not category or not normalized_path:
            continue
        grouped.setdefault(category, []).append((idx, item, normalized_path))

    dropped_indices: set[int] = set()
    for category, entries in grouped.items():
        kept_descendants: list[str] = []
        dropped_here = 0
        for idx, item, normalized_path in sorted(entries, key=lambda entry: len(entry[2]), reverse=True):
            raw_path = Path(str(item.get("path") or "").strip())
            if raw_path.exists() and not raw_path.is_dir():
                kept_descendants.append(normalized_path)
                continue

            if any(child_path.startswith(f"{normalized_path}/") for child_path in kept_descendants) and not (
                raw_path.exists() and _should_preserve_force_upload_dir(item, category, raw_path)
            ):
                dropped_indices.add(idx)
                dropped_here += 1
                continue

            kept_descendants.append(normalized_path)

        if dropped_here:
            logger.info(f"[FORCE-UPLOAD] Category '{category}' collapsed {dropped_here} overlapping ancestor path(s)")

    return [item for idx, item in enumerate(items) if idx not in dropped_indices]

@router.put("/order")
async def update_pending_group_order(req: PendingGroupOrderRequest) -> Dict[str, Any]:
    """Persist the shared pending-directory order for all browsers/users."""
    from core.config import save_config

    order = [str(key).strip() for key in req.order if str(key).strip()]
    if save_config({"pending_external_group_order": order}):
        return {"status": "success", "order": order}
    raise HTTPException(status_code=500, detail="Failed to save pending order")

@router.put("/order/locked")
async def update_pending_group_order_locked(req: PendingGroupOrderLockedRequest) -> Dict[str, Any]:
    """Persist the shared pending-directory lock state."""
    from core.config import save_config

    locked = bool(req.locked)
    if save_config({"pending_external_group_order_locked": locked}):
        return {"status": "success", "locked": locked}
    raise HTTPException(status_code=500, detail="Failed to save pending order lock state")

@router.get("/category-overrides")
def get_category_overrides() -> Dict[str, Any]:
    """Return all persisted manual category overrides for pending items."""
    from logic.pending import overrides as pending_overrides

    return {"overrides": pending_overrides.get_all()}

@router.post("/category-overrides")
def set_category_override(req: CategoryOverrideRequest) -> Dict[str, Any]:
    """Persist (or clear, when category is empty) a manual category override."""
    from logic.pending import overrides as pending_overrides

    key = (req.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Missing item key")
    category = (req.category or "").strip().lower() or None
    pending_overrides.set_override(key, category)
    return {"status": "success", "key": key, "category": category}

def _slim_pending_items(items: Any) -> Any:
    """Apply `_slim_pending_node` across every section of a pending `items` payload."""
    if not isinstance(items, dict):
        return items
    slim_items: Dict[str, Any] = {}
    for section_name, section in items.items():
        if not isinstance(section, list):
            slim_items[section_name] = section
            continue
        if section_name != "external":
            slim_items[section_name] = [_slim_pending_node(item) for item in section]
            continue
        groups = []
        for group in section:
            if not isinstance(group, dict):
                groups.append(group)
                continue
            slim_group = dict(group)
            group_items = group.get("items")
            if isinstance(group_items, list):
                slim_group["items"] = [_slim_pending_node(item) for item in group_items]
            groups.append(slim_group)
        slim_items[section_name] = groups
    return slim_items

@router.post("/mark-uploaded")
async def mark_items_uploaded(req: MarkUploadedRequest) -> Dict[str, Any]:
    """Record items as already uploaded without posting them.

    Backs the queue page's "Mark Uploaded" action; core.db.uploads.mark_as_uploaded
    already existed but had no route, so the button 404'd.
    """
    if not req.item_keys:
        raise HTTPException(status_code=400, detail="No items specified")
    if not req.indexer_ids:
        raise HTTPException(status_code=400, detail="No indexers specified")

    created = db_uploads.mark_as_uploaded(req.item_keys, req.indexer_ids, itype=req.itype)
    if created > 0:
        from logic.queue_metrics import request_live_queue_refresh

        request_live_queue_refresh(reason="manual-mark-uploaded")
    return {
        "status": "success",
        "records_created": created,
        "items": len(req.item_keys),
        "indexers": len(req.indexer_ids),
    }
