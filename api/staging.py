"""The staging queue: /api/uploads/queue/items, /queue/preview, /queue/start."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from api.deps import _filter_bulk_selectable_items, _log_selected_payload, _normalize_request_strings
from core.config import get_config
from logic.pipeline import runner
from logic.jobs.models import ProcessingJobRequest
from logic.services import get_upload_service, UploadService


router = APIRouter(prefix="/api/uploads", tags=["uploads"])

@router.post("/queue/items")
async def add_queue_items(
    req: AddQueueItemsRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Add items to the upload queue (from the pending page)."""
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
    _log_selected_payload("QUEUE", selected_items)
    added = service.add_queue_items(selected_items)
    logger.info(
        f"[QUEUE] Staging request received {len(req.items)} item(s); "
        f"added={len(added)} skipped={len(selected_items) - len(added)} bulk_excluded={excluded_count}"
    )
    return {
        "status": "added",
        "added": len(added),
        "items": added,
        "total": len(service.get_queue_items()),
        "bulk_excluded": excluded_count,
    }

@router.post("/queue/preview")
async def preview_queue(
    req: StartQueueRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Preview the staged queue without removing items or creating a job."""
    items = service.get_queue_items()
    preview = await _preview_selected_items(
        list(items),
        enable_duplicate_check=req.enable_duplicate_check,
        test_mode=req.test_mode,
        indexer_id=req.indexer_id,
    )
    preview["selection"] = {
        "matched": len(items),
        "bulk_excluded": 0,
        "overlapping_paths": 0,
        "excluded_roots": [],
    }
    return preview

class AddQueueItemsRequest(BaseModel):
    """API request model for adding items to the upload queue."""

    items: List[Dict[str, str]]  # [{path, category, itype, name?}]
    bulk_selection: bool = False

class ReorderQueueRequest(BaseModel):
    """API request model for reordering queue items."""

    item_ids: List[int]

class StartQueueRequest(BaseModel):
    """API request model for starting queue processing."""

    enable_duplicate_check: bool = True
    test_mode: bool = False
    indexer_id: Optional[str] = None

@router.get("/queue/items")
def get_queue_items(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve all items in the upload queue."""
    items = service.get_queue_items()
    return {"items": items, "count": len(items)}

@router.delete("/queue/items/{item_id}")
async def remove_queue_item(
    item_id: int,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Remove a single item from the queue."""
    if service.remove_queue_item(item_id):
        return {"status": "removed", "item_id": item_id}
    raise HTTPException(status_code=404, detail="Queue item not found")

@router.post("/queue/items/clear")
async def clear_queue_items(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Clear all items from the upload queue."""
    cleared = service.clear_queue_items()
    return {"cleared": cleared}

@router.post("/queue/items/{item_id}/start")
async def force_start_queue_item(
    item_id: int,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Remove a single item from the queue and immediately start a force-upload job for it."""
    # Find and remove the item from the queue
    items = service.get_queue_items()
    target = None
    for qi in items:
        if qi["id"] == item_id:
            target = qi
            break
    if not target:
        raise HTTPException(status_code=404, detail="Queue item not found")

    # Start a force-upload job for this single item
    jid = service.start_processing_job_request(
        ProcessingJobRequest(
            category=str(target["category"]),
            test_mode=False,
            paths=(str(target["path"]),),
            item_hints=(dict(target),),
            enable_duplicate_check=True,
        ),
        reuse_running=False,
        source="queue-item-start",
    )
    if not service.remove_queue_item(item_id):
        logger.warning(f"[QUEUE-ITEM-START] Started job {jid} but could not remove staged item {item_id}")
    return {"status": "started", "job_id": jid, "item": target}

async def _preview_selected_items(
    items: List[Dict[str, Any]],
    *,
    enable_duplicate_check: bool,
    test_mode: bool,
    indexer_id: Optional[str] = None,
    indexer_ids: Optional[List[str]] = None,
    force: Optional[bool] = None,
    skip_packs: bool = False,
    skip_episodes: bool = False,
) -> Dict[str, Any]:
    normalized_indexer_ids = _normalize_request_strings(indexer_ids or ([indexer_id] if indexer_id else []))
    return await asyncio.to_thread(
        runner.preview_processing_items,
        items,
        target_indexer_id=normalized_indexer_ids[0] if len(normalized_indexer_ids) == 1 else None,
        target_indexer_ids=normalized_indexer_ids,
        enable_duplicate_check=enable_duplicate_check,
        force=force,
        test_mode=test_mode,
        skip_packs=skip_packs,
        skip_episodes=skip_episodes,
    )

@router.put("/queue/items/reorder")
async def reorder_queue_items(
    req: ReorderQueueRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Reorder queue items. Provide all item IDs in the desired order."""
    if service.reorder_queue_items(req.item_ids):
        return {"status": "reordered"}
    raise HTTPException(
        status_code=400,
        detail="Invalid item IDs - must include all current queue items",
    )

@router.post("/queue/start")
async def start_queue(
    req: StartQueueRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Process all queued items as one unified upload job."""
    try:
        result = service.start_queue_with_details(
            enable_duplicate_check=req.enable_duplicate_check,
            test_mode=req.test_mode,
            indexer_id=req.indexer_id,
            source="queue-start",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    job_ids = result.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="Queue is empty")
    return {
        "status": "started",
        "job_ids": job_ids,
        "jobs_created": result.get("jobs_created", len(job_ids)),
        "started_items": result.get("started_items", 0),
        "skipped_items": result.get("skipped_items", 0),
        "remaining_staged": result.get("remaining_staged", 0),
    }
