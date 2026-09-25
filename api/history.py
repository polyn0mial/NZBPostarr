"""Upload history, grouped results and known issues under /api/uploads."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from core.db import engine as db_engine
from core.db import history as db_history
from core.db import issues as db_issues
from core.db import job_history as db_job_history
from core.db import stats as db_stats
from core.db import uploads as db_uploads


router = APIRouter(prefix="/api/uploads", tags=["uploads"])

def _history_database_error(exc: Exception) -> HTTPException:
    """Readable 503 for History routes when the upload database cannot be used.

    The History page shows ``detail`` as-is, so it must stay a plain string.
    """
    logger.warning(f"[HISTORY] Upload database error: {exc}")
    return HTTPException(
        status_code=503,
        detail=f"The upload history database is unavailable ({exc}). Try again in a moment.",
    )

@router.get("/recent")
def get_recent(
    limit: int = 50,
    offset: int = 0,
    search: Optional[str] = None,
    destination: str = "all",
    literal: bool = False,
    sort_by: str = "when",
    order: str = "desc",
) -> Dict[str, Any]:
    """Fetch paginated upload history from the database."""
    try:
        return db_history.get_recent_uploads(
            limit=limit,
            offset=offset,
            search=search,
            destination=destination,
            literal=literal,
            sort_by=sort_by,
            order=order,
        )
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@router.get("/grouped")
def get_grouped(
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
    destination: str = "all",
    literal: bool = False,
    sort_by: str = "when",
    order: str = "desc",
) -> Dict[str, Any]:
    """Fetch upload history grouped by show name, paginated by group count."""
    try:
        return db_history.get_grouped_uploads(
            page=page,
            per_page=per_page,
            search=search,
            destination=destination,
            literal=literal,
            sort_by=sort_by,
            order=order,
        )
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@router.get("/grouped/items")
def get_grouped_items(title_key: str, destination: str = "all") -> Dict[str, Any]:
    """Full upload rows behind one grouped-history row.

    Backs the History page's group expansion; core.db.history.get_group_upload_items
    already existed but had no route, so expanding a group 404'd.
    """
    try:
        return db_history.get_group_upload_items(title_key, destination=destination)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@router.get("/errors/grouped")
def get_grouped_errors(
    destination: str = "all",
    limit: int = 50,
    since_days: Optional[int] = None,
    include_muted: bool = True,
) -> Dict[str, Any]:
    """Known-issues view: failed indexer submissions grouped by error signature.

    Turns the flat per-attempt failure log into a "this has happened N times
    across these items, most recently at T" list, one row per indexer per
    distinct underlying error.
    """
    try:
        return db_issues.get_grouped_upload_errors(indexer_id=destination, limit=limit, since_days=since_days, include_muted=include_muted)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

class MuteIssueRequest(BaseModel):
    """API request model for muting/unmuting a known-issue group.

    A group has no id of its own; (indexer_id, signature) is the same pair
    get_grouped_upload_errors groups by, so it is also the mute key.
    """

    indexer_id: str
    signature: str

@router.post("/errors/mute")
def mute_grouped_error(body: MuteIssueRequest) -> Dict[str, Any]:
    """Silence a known-issue group so it stops standing out in the default view."""
    try:
        db_issues.mute_upload_issue(body.indexer_id, body.signature)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "muted": True}

@router.post("/errors/unmute")
def unmute_grouped_error(body: MuteIssueRequest) -> Dict[str, Any]:
    """Restore a previously muted known-issue group to the default view."""
    try:
        db_issues.unmute_upload_issue(body.indexer_id, body.signature)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "muted": False}

@router.get("/history")
async def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Listing of completed upload jobs."""
    try:
        return db_job_history.get_job_history(limit)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@router.get("/history/{job_id}/uploads")
async def get_job_uploads(job_id: str) -> List[Dict[str, Any]]:
    """Fetch individual items for a given job ID."""
    try:
        return db_history.get_uploads_for_job(job_id)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@router.delete("/history")
async def delete_job_history(request: Request) -> Dict[str, Any]:
    """Delete one or more job history records."""
    body = await request.json()
    job_ids = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")
    try:
        deleted = db_job_history.delete_job_history(job_ids)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "deleted": deleted}

@router.get("/hourly-stats")
async def get_hourly_stats() -> Dict[str, Any]:
    """Fetch recent performance metrics."""
    return db_stats.get_hourly_upload_stats()

@router.delete("/item/{item_name}")
async def delete_upload_item(item_name: str) -> Dict[str, Any]:
    """Remove a single item from the history database."""
    try:
        deleted = db_uploads.delete_upload_item(item_name)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    if deleted:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to delete item")

class BulkDeleteRequest(BaseModel):
    """API request model for bulk deleting items."""

    item_names: List[str]

@router.post("/item/bulk-delete")
async def bulk_delete_upload_items(req: BulkDeleteRequest) -> Dict[str, Any]:
    """Remove multiple items from the history database."""
    if not req.item_names:
        return {"status": "success", "deleted_count": 0}

    try:
        deleted_count = db_uploads.bulk_delete_upload_items(req.item_names)
    except db_engine.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "deleted_count": deleted_count}
