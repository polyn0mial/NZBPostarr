"""Upload jobs and the job queue: /api/uploads/start, /jobs, /queue."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import (
    _bulk_selection_excluded_roots,
    _normalize_request_strings,
    _path_is_at_or_below,
    _resolved_policy_path,
)
from core.config import get_config
from logic.pending.roots import scan_configured_items
from logic.pending.selection import category_upload_itype
from logic.jobs.models import ProcessingJobRequest
from logic.jobs.engine import JobEngine
from logic.jobs.views import active_job_items, build_queue_snapshot, finished_job_items, queued_job_items
from logic.runtime import ensure_engine_started


router = APIRouter(prefix="/api/uploads", tags=["uploads"])

def _select_upload_request_items(req: UploadRequest, conf: Any) -> tuple[List[Dict[str, str]], Dict[str, Any]]:
    """Resolve one upload request into the canonical bulk-selection candidates."""
    requested_categories = _normalize_upload_categories(req.categories or [req.category])
    selected_folder_keys = {
        _normalize_selection_path(path)
        for path in _normalize_request_strings(req.folder_paths)
    }
    selected_items: List[Dict[str, str]] = []
    excluded_roots = _bulk_selection_excluded_roots(conf)
    excluded_count = 0
    duplicate_path_count = 0
    matched_count = 0

    if req.file_path:
        resolved_file_path = _resolved_policy_path(req.file_path)
        if not resolved_file_path.exists():
            raise HTTPException(status_code=400, detail="The requested file path was not found")

        upload_type = str(req.upload_type or "").strip().lower()
        selected_category = (
            "tv"
            if upload_type.startswith("tv")
            else (
                requested_categories[0]
                if len(requested_categories) == 1 and requested_categories[0] != "all"
                else "movies"
            )
        )
        selected_items.append(
            {
                "path": str(resolved_file_path),
                "category": selected_category,
                "itype": _default_itype_for_category(resolved_file_path, selected_category),
                "name": resolved_file_path.name,
            }
        )
        matched_count = 1
    else:
        configured_items = scan_configured_items(conf, must_exist=True)
        if selected_folder_keys:
            configured_items = [
                item
                for item in configured_items
                if _normalize_selection_path(str(item.folder)) in selected_folder_keys
            ]
        if requested_categories != ["all"]:
            configured_items = [
                item for item in configured_items if item.category in requested_categories
            ]
        matched_count = len(configured_items)

        seen_paths: Set[str] = set()
        for item in configured_items:
            raw_path = str(item.path)
            if excluded_roots and any(_path_is_at_or_below(item.path, root) for root in excluded_roots):
                excluded_count += 1
                continue
            key = _normalize_selection_path(raw_path)
            if key in seen_paths:
                duplicate_path_count += 1
                continue
            seen_paths.add(key)
            item_path = Path(raw_path)
            selected_items.append(
                {
                    "path": raw_path,
                    "category": str(item.category),
                    "itype": _default_itype_for_category(item_path, str(item.category)),
                    "name": str(getattr(item, "name", item_path.name)),
                }
            )

    return selected_items, {
        "matched": matched_count,
        "bulk_excluded": excluded_count,
        "duplicate_paths": duplicate_path_count,
        "excluded_roots": [str(root) for root in excluded_roots],
    }

@router.post("/start")
async def start_upload(req: UploadRequest, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Start one or more bulk upload jobs based on the selected filters."""
    conf = get_config()
    selected_items, _selection_meta = _select_upload_request_items(req, conf)
    selected_indexer_ids = _normalize_request_strings(
        req.indexer_ids or ([req.indexer_id] if req.indexer_id else [])
    )

    requests: List[ProcessingJobRequest] = []
    if not selected_items:
        raise HTTPException(status_code=400, detail="No matching items were found for the selected filters")
    categories = {item["category"] for item in selected_items if item.get("category")}
    request_category = next(iter(categories)) if len(categories) == 1 else "mixed"
    requests.append(
        ProcessingJobRequest(
            category=request_category,
            limit=req.limit,
            skip_packs=req.skip_packs,
            skip_episodes=req.skip_episodes,
            test_mode=req.test_mode,
            target_indexer_id=selected_indexer_ids[0] if len(selected_indexer_ids) == 1 else None,
            target_indexer_ids=tuple(selected_indexer_ids),
            paths=tuple(item["path"] for item in selected_items),
            item_hints=tuple(dict(item) for item in selected_items),
            enable_duplicate_check=req.enable_duplicate_check,
        )
    )

    job_ids = service.start_processing_job_requests(requests, source=req.source or "api-bulk-start")

    return {
        "job_id": job_ids[0] if len(job_ids) == 1 else None,
        "job_ids": job_ids,
        "status": "started",
    }

class UploadRequest(BaseModel):
    """API request model for starting an upload job."""

    category: str = "all"
    categories: List[str] = Field(default_factory=list)
    test_mode: bool = False
    limit: Optional[int] = None
    file_path: Optional[str] = None
    upload_type: Optional[str] = None
    enable_duplicate_check: bool = True
    skip_packs: bool = False
    skip_episodes: bool = False
    indexer_id: Optional[str] = None
    indexer_ids: List[str] = Field(default_factory=list)
    folder_paths: List[str] = Field(default_factory=list)
    source: Optional[str] = None

class ReorderQueuedJobItemsRequest(BaseModel):
    """API request model for reordering queued job target paths."""

    paths: List[str]

class RemoveQueuedJobItemRequest(BaseModel):
    """API request model for removing one path from a queued job."""

    path: str

class RenameJobRequest(BaseModel):
    """API request model for setting/clearing a custom job display name."""

    name: Optional[str] = None

class QueueScheduleRequest(BaseModel):
    """API request model for deferred queued-job scheduling."""

    run_after: Optional[str] = None

class QueuePriorityRequest(BaseModel):
    """API request model for setting a queued or paused job's priority."""

    priority: int = 0

class QueueRevalidateRequest(BaseModel):
    """API request model for revalidating queued jobs against current rules."""

    include_paused: bool = True

def _default_itype_for_category(path: Path, category: str) -> str:
    return category_upload_itype(category, is_dir=path.is_dir())

@router.get("/jobs")
def get_jobs(
    service: JobEngine = Depends(ensure_engine_started),
) -> List[Dict[str, Any]]:
    """Retrieve compact upload-job snapshots for frequent dashboard polling."""
    return service.get_active_jobs(compact=True)

@router.post("/jobs/{job_id}/stop")
async def stop_upload(job_id: str, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Request a specific job to stop."""
    if service.stop_job(job_id):
        return {"status": "stopping", "message": "Termination signal sent to job."}
    raise HTTPException(status_code=404, detail="Job not found")

@router.post("/jobs/{job_id}/stop-clear")
async def stop_clear_upload(job_id: str, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Stop a specific job and remove it from the visible queue as soon as possible."""
    if service.stop_and_clear_job(job_id):
        return {"status": "clearing", "message": "Job stopping and clearing from the queue."}
    raise HTTPException(status_code=404, detail="Job not found")

@router.post("/jobs/{job_id}/pause")
async def pause_upload(job_id: str, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Pause an actively running upload job."""
    if service.pause_job(job_id):
        job = service.get_job(job_id) or {}
        pause_pending = bool(job.get("pause_requested")) and job.get("status") == "running"
        return {
            "status": job.get("status", "paused"),
            "message": (
                "Pause requested; waiting for the current safe checkpoint."
                if pause_pending
                else "Job paused; the scheduler lane is available."
            ),
        }
    raise HTTPException(status_code=404, detail="Job not found or not running")

@router.post("/jobs/{job_id}/resume")
async def resume_upload(job_id: str, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Resume a paused upload job."""
    if service.resume_job(job_id):
        job = service.get_job(job_id) or {}
        queued = bool(job.get("resume_requested"))
        return {
            "status": job.get("status", "running"),
            "message": "Job queued to resume when the scheduler lane is available." if queued else "Job resumed.",
        }
    raise HTTPException(status_code=404, detail="Job not found or not paused")

@router.post("/jobs/{job_id}/retry")
async def retry_upload(job_id: str, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Queue a new attempt from a failed processing job's saved request."""
    ok, new_job_id, reason = service.retry_job(job_id)
    if not ok:
        if reason == "not-found":
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=409, detail="Job is not eligible for retry")
    return {
        "status": "queued",
        "job_id": new_job_id,
        "retry_of": job_id,
        "message": f"Retry queued as {new_job_id}",
    }

@router.delete("/jobs/completed")
async def clear_completed(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Clear all finished jobs from memory."""
    cleared = service.clear_completed_jobs()
    return {"cleared": cleared}

@router.delete("/jobs/{job_id}")
async def delete_job_route(job_id: str, service: JobEngine = Depends(ensure_engine_started)) -> Dict[str, Any]:
    """Remove a job from memory."""
    if service.delete_job(job_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Job not found")

@router.get("/queue")
def get_queue(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Retrieve structured queue data: running, queued, and recently finished jobs."""
    return build_queue_snapshot(service)

@router.post("/queue/pause")
async def pause_queue_processing(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Pause queue processing and pause the currently running job (if any)."""
    service.pause_queue(pause_active=True)
    return {
        "status": "paused",
        "message": "Queue processing paused. New jobs remain queued until resumed.",
        "control": service.get_queue_control_state(),
    }

@router.post("/queue/resume")
async def resume_queue_processing(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Resume queue processing and continue with paused/waiting jobs."""
    service.resume_queue()
    return {
        "status": "running",
        "message": "Queue processing resumed.",
        "control": service.get_queue_control_state(),
    }

@router.post("/queue/stop")
async def stop_queue_processing(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Stop the active job and pause queue processing until resumed."""
    service.stop_queue()
    return {
        "status": "stopped",
        "message": "Active job stopping. Queue processing is paused.",
        "control": service.get_queue_control_state(),
    }

@router.post("/queue/stop-clear")
async def stop_clear_queue_processing(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Stop the active job, clear waiting jobs, and hide the active row while it shuts down."""
    result = service.stop_queue_and_clear()
    return {
        "status": "clearing",
        "message": "Active job stopping and queue clearing.",
        **result,
    }

@router.post("/queue/clear")
async def clear_queue(
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Cancel all queued (not yet running) jobs."""
    cleared = service.clear_queued_jobs()
    return {"cleared": cleared}

@router.post("/queue/{job_id}/promote")
async def promote_queued_job(
    job_id: str,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Move a queued job to the front of the queue (next to run)."""
    if service.promote_job(job_id):
        return {"status": "promoted", "job_id": job_id}
    raise HTTPException(status_code=404, detail="Job not found or not queued")

@router.get("/queue/{job_id}/items")
def get_queued_job_items(
    job_id: str,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Retrieve target paths for a queued job so the UI can edit it."""
    items = queued_job_items(service, job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Queued job not found")
    return {"job_id": job_id, "items": items, "count": len(items)}

@router.get("/queue/{job_id}/active-items")
def get_active_job_items(
    job_id: str,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Retrieve remaining target paths only while an active editor is open."""
    items = active_job_items(service, job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Active job not found")
    return {"job_id": job_id, "items": items, "count": len(items)}

@router.get("/queue/{job_id}/completed-items")
def get_completed_job_items(
    job_id: str,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Retrieve item paths for a recently finished job (current session only)."""
    items = finished_job_items(service, job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Finished job not found")
    return {"job_id": job_id, "items": items, "count": len(items)}

def _normalize_upload_categories(values: List[str]) -> List[str]:
    requested = _normalize_request_strings(values)
    if not requested or "all" in requested:
        return ["all"]

    normalized: List[str] = []
    seen: Set[str] = set()
    for value in requested:
        key = str(value).strip().lower()
        for category in (["movies", "tv"] if key == "both" else [key]):
            if category and category != "external" and category not in seen:
                seen.add(category)
                normalized.append(category)
    return normalized or ["all"]

def _normalize_selection_path(value: Any) -> str:
    normalized = str(_resolved_policy_path(value))
    return normalized.casefold() if os.name == "nt" else normalized

@router.patch("/jobs/{job_id}/name")
async def rename_upload_job(
    job_id: str,
    req: RenameJobRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Set or clear a human-friendly job name."""
    found, display_name = service.rename_job(job_id, req.name)
    if not found:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "status": "updated",
        "job_id": job_id,
        "display_name": display_name,
    }

@router.post("/queue/revalidate")
async def revalidate_queue_jobs(
    req: QueueRevalidateRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Re-scan queued/paused jobs against the current classification rules."""
    result = service.revalidate_queued_jobs(include_paused=bool(req.include_paused))
    return {"status": "success", **result}

@router.patch("/queue/{job_id}/schedule")
async def set_queued_job_schedule(
    job_id: str,
    req: QueueScheduleRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Set or clear the deferred run timestamp for a queued job."""
    ok, run_after, reason = service.set_queued_job_schedule(job_id, req.run_after)
    if not ok:
        if reason == "not-found":
            raise HTTPException(status_code=404, detail="Job not found")
        if reason == "not-queued":
            raise HTTPException(status_code=409, detail="Job is not queued")
        if reason == "invalid-datetime":
            raise HTTPException(status_code=400, detail="Invalid run_after datetime")
        raise HTTPException(status_code=400, detail="Unable to set schedule")

    return {
        "status": "updated",
        "job_id": job_id,
        "run_after": run_after,
    }

@router.patch("/queue/{job_id}/priority")
async def set_queued_job_priority(
    job_id: str,
    req: QueuePriorityRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Set durable scheduler priority for a queued or paused job."""
    ok, priority = service.set_job_priority(job_id, req.priority)
    if not ok:
        if service.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=409, detail="Only queued or paused jobs can change priority")
    return {"status": "updated", "job_id": job_id, "priority": priority}

@router.put("/queue/{job_id}/items/reorder")
async def reorder_queued_job_items_route(
    job_id: str,
    req: ReorderQueuedJobItemsRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Reorder queued-job target paths by providing the full desired path order."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    if service.reorder_queued_job_items(job_id, req.paths):
        return {"status": "reordered", "job_id": job_id}

    if queued_job_items(service, job_id) is None:
        raise HTTPException(status_code=404, detail="Queued job not found")
    raise HTTPException(status_code=400, detail="Invalid path order for queued job")

@router.put("/queue/{job_id}/active-items/reorder")
async def reorder_active_job_items_route(
    job_id: str,
    req: ReorderQueuedJobItemsRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Reorder the remaining target paths for a running or paused job."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    if service.reorder_active_job_items(job_id, req.paths):
        return {"status": "reordered", "job_id": job_id}

    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="Job is not active")
    raise HTTPException(status_code=400, detail="Invalid remaining-item order for active job")

@router.delete("/queue/{job_id}/active-items")
async def remove_active_job_item_route(
    job_id: str,
    req: RemoveQueuedJobItemRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Remove a single target path from a running or paused job."""
    if not req.path:
        raise HTTPException(status_code=400, detail="No path provided")

    if service.remove_active_job_item(job_id, req.path):
        return {"status": "removed", "job_id": job_id, "path": req.path}

    job = service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.get("status") not in ("running", "paused"):
        raise HTTPException(status_code=409, detail="Job is not active")
    raise HTTPException(status_code=404, detail="Path not found in active job")

@router.delete("/queue/{job_id}/items")
async def remove_queued_job_item_route(
    job_id: str,
    req: RemoveQueuedJobItemRequest,
    service: JobEngine = Depends(ensure_engine_started),
) -> Dict[str, Any]:
    """Remove a single target path from a queued job."""
    if not req.path:
        raise HTTPException(status_code=400, detail="No path provided")

    if service.remove_queued_job_item(job_id, req.path):
        return {"status": "removed", "job_id": job_id, "path": req.path}

    if queued_job_items(service, job_id) is None:
        raise HTTPException(status_code=404, detail="Queued job not found")
    raise HTTPException(status_code=404, detail="Path not found in queued job")
