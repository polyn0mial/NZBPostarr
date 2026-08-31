# Auto-split from app.py - verbatim symbol bodies, synthesized imports.

from app_base import (
    Any, Dict, HTTPException, List, Optional, Path, asyncio, database, get_upload_service, logger, pending_router, re, system_router, updater,
)
from app_g1 import (PendingGroupOrderLockedRequest, PendingGroupOrderRequest, RestartRequest, StopAllRequest, UpdateRollbackRequest)  # noqa: F401
from app_g2 import (MarkUploadedRequest, _bulk_selection_excluded_roots, _force_upload_dir_direct_video_count, _force_upload_dir_recursive_video_count, _normalize_force_upload_path, _path_is_at_or_below, _slim_pending_node)  # noqa: F401

def _filter_bulk_selectable_items(
    items: List[Dict[str, Any]],
    conf: Any,
) -> tuple[List[Dict[str, Any]], int]:
    """Apply per-root mass-selection policy without restricting manual actions."""
    excluded_roots = _bulk_selection_excluded_roots(conf)
    if not excluded_roots:
        return list(items), 0

    allowed: List[Dict[str, Any]] = []
    excluded_count = 0
    for item in items:
        raw_path = str(item.get("path") or "").strip()
        if raw_path and any(_path_is_at_or_below(raw_path, root) for root in excluded_roots):
            excluded_count += 1
            continue
        allowed.append(item)
    return allowed, excluded_count

@system_router.post("/update/rollback")
async def rollback_update(req: UpdateRollbackRequest) -> Dict[str, Any]:
    """Restore files from a previous updater snapshot."""
    try:
        return await asyncio.to_thread(
            updater.rollback_to_backup,
            backup_id=req.backup_id,
            restart=req.restart,
        )
    except updater.UpdateError as exc:
        message = str(exc)
        status = 409 if "already in progress" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception(f"Rollback failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Rollback failed: {exc}") from exc

@system_router.post("/restart")
async def restart_service(req: RestartRequest) -> Dict[str, Any]:
    """Schedule a process restart without changing files."""
    service = get_upload_service()
    stop_result: Optional[Dict[str, Any]] = None

    if req.stop_before_restart:
        stop_result = await asyncio.to_thread(
            service.stop_all_jobs_and_wait,
            clear_staged_items=req.clear_staged_items,
            wait_timeout_s=req.wait_timeout_seconds,
        )

    updater.schedule_restart(delay_seconds=req.delay_seconds)
    return {
        "status": "scheduled",
        "delay_seconds": req.delay_seconds,
        "stop": stop_result,
    }

@system_router.post("/stop-all")
async def stop_all_service_activity(req: StopAllRequest) -> Dict[str, Any]:
    """Stop active jobs, clear waiting work, and wait until quiet."""
    service = get_upload_service()
    stop_result = await asyncio.to_thread(
        service.stop_all_jobs_and_wait,
        clear_staged_items=req.clear_staged_items,
        wait_timeout_s=req.wait_timeout_seconds,
    )
    status = "stopped"
    message = "All uploads stopped and queue cleared."
    if stop_result.get("timed_out"):
        status = "partial"
        message = "Stop requested, but some work was still shutting down when the timeout expired."

    return {
        "status": status,
        "message": message,
        "stop": stop_result,
    }

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

@pending_router.put("/order")
async def update_pending_group_order(req: PendingGroupOrderRequest) -> Dict[str, Any]:
    """Persist the shared pending-directory order for all browsers/users."""
    from core.config import save_config

    order = [str(key).strip() for key in req.order if str(key).strip()]
    if save_config({"pending_external_group_order": order}):
        return {"status": "success", "order": order}
    raise HTTPException(status_code=500, detail="Failed to save pending order")

@pending_router.put("/order/locked")
async def update_pending_group_order_locked(req: PendingGroupOrderLockedRequest) -> Dict[str, Any]:
    """Persist the shared pending-directory lock state."""
    from core.config import save_config

    locked = bool(req.locked)
    if save_config({"pending_external_group_order_locked": locked}):
        return {"status": "success", "locked": locked}
    raise HTTPException(status_code=500, detail="Failed to save pending order lock state")

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

@pending_router.post("/mark-uploaded")
async def mark_items_uploaded(req: MarkUploadedRequest) -> Dict[str, Any]:
    """Record items as already uploaded without posting them.

    Backs the queue page's "Mark Uploaded" action; database.mark_as_uploaded
    already existed but had no route, so the button 404'd.
    """
    if not req.item_keys:
        raise HTTPException(status_code=400, detail="No items specified")
    if not req.indexer_ids:
        raise HTTPException(status_code=400, detail="No indexers specified")

    created = database.mark_as_uploaded(req.item_keys, req.indexer_ids, itype=req.itype)
    return {
        "status": "success",
        "records_created": created,
        "items": len(req.item_keys),
        "indexers": len(req.indexer_ids),
    }

