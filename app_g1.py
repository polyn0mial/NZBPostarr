# Auto-split from app.py - verbatim symbol bodies, synthesized imports.

from app_base import (
    Any, BaseModel, Depends, Dict, Field, FileResponse, HTTPException, List, Optional, Path, ProcessingJobRequest, Request, Response, SECRET_MASK, Set,
    UploadService, asyncio, console, console_router, copy, dashboard_router, database, get_upload_service, indexers_router, logger, os, settings_router,
    uploads_router, usenet_stream,
)

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

class StreamStartResponse(BaseModel):
    """API response model for starting a streamed NZB repost job."""

    job_id: Optional[str] = None
    job_ids: List[str] = Field(default_factory=list)
    status: str
    mode: str = "job"
    message: Optional[str] = None
    monitor: Optional[Dict[str, Any]] = None

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

class PendingGroupOrderRequest(BaseModel):
    """API request model for shared pending-directory ordering."""

    order: List[str] = Field(default_factory=list)

class PendingGroupOrderLockedRequest(BaseModel):
    """API request model for shared pending-directory lock state."""

    locked: bool = False

class QueueRevalidateRequest(BaseModel):
    """API request model for revalidating queued jobs against current rules."""

    include_paused: bool = True

class UpdateInstallRequest(BaseModel):
    """API request model for installing an update from GitHub."""

    version: Optional[str] = None
    restart: bool = True

class UpdateRollbackRequest(BaseModel):
    """API request model for rolling back to a backup snapshot."""

    backup_id: str
    restart: bool = True

class RestartRequest(BaseModel):
    """API request model for scheduling a process restart."""

    delay_seconds: float = 2.0
    stop_before_restart: bool = True
    clear_staged_items: bool = True
    wait_timeout_seconds: float = 15.0

class CreateBackupRequest(BaseModel):
    """API request model for generating a full NZBPostarr backup archive."""

    skip_tmp_contents: bool = True

class StopAllRequest(BaseModel):
    """API request model for stopping all work and waiting for quiescence."""

    clear_staged_items: bool = True
    wait_timeout_seconds: float = 15.0

def _resolved_policy_path(value: Any) -> Path:
    path = Path(str(value or "").strip())
    try:
        return path.resolve()
    except OSError:
        return path.absolute()

def _normalize_request_strings(values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return normalized

def _default_itype_for_category(path: Path, category: str) -> str:
    normalized = str(category or "").strip().lower()
    if normalized == "tv":
        return "TV Show" if path.is_dir() else "TV Episode"
    return {
        "movies": "Movie",
        "anime": "Anime",
        "music": "Music",
        "audiobooks": "Audiobook",
        "books": "Ebook",
        "apps": "App",
    }.get(normalized, "Misc")

def _mask_config_secrets(value: Any, *, key: str = "") -> Any:
    normalized_key = str(key or "").strip().lower()
    secret_key = (
        normalized_key in {"api_keys", "usernames", "web_password", "password", "pass", "user", "username"}
        or normalized_key.endswith("_api_key")
    )
    if secret_key:
        if isinstance(value, dict):
            return {str(child_key): SECRET_MASK if child_value not in (None, "") else child_value for child_key, child_value in value.items()}
        return SECRET_MASK if value not in (None, "") else value
    if isinstance(value, dict):
        return {
            str(child_key): _mask_config_secrets(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_mask_config_secrets(item) for item in value]
    return value

def _merge_masked_secret_updates(updates: Dict[str, Any], conf: Any) -> Dict[str, Any]:
    """Replace unchanged UI mask markers with the current private values."""
    merged = copy.deepcopy(updates)
    if merged.get("web_password") == SECRET_MASK:
        merged["web_password"] = str(getattr(conf, "web_password", "") or "")

    for field_name in ("api_keys", "usernames"):
        incoming = merged.get(field_name)
        current = getattr(conf, field_name, {}) or {}
        if not isinstance(incoming, dict) or not isinstance(current, dict):
            continue
        for item_key, item_value in list(incoming.items()):
            if item_value == SECRET_MASK:
                incoming[item_key] = current.get(item_key, "")

    incoming_servers = merged.get("nntp_servers")
    current_servers = list(getattr(conf, "nntp_servers", []) or [])
    if isinstance(incoming_servers, list):
        current_by_name = {
            str(getattr(server, "name", "")): server
            for server in current_servers
            if str(getattr(server, "name", ""))
        }
        normalized_servers: list[Any] = []
        for index, incoming_server in enumerate(incoming_servers):
            if not isinstance(incoming_server, dict):
                normalized_servers.append(incoming_server)
                continue
            server = dict(incoming_server)
            current_server = current_by_name.get(str(server.get("name") or ""))
            if current_server is None and index < len(current_servers):
                current_server = current_servers[index]

            incoming_user = server.get("user")
            if incoming_user == SECRET_MASK and current_server is not None:
                server["user"] = str(getattr(current_server, "user", ""))

            incoming_password = server.pop("password", server.get("pass"))
            if incoming_password == SECRET_MASK and current_server is not None:
                incoming_password = str(getattr(current_server, "password", ""))
            if incoming_password is not None:
                server["pass"] = incoming_password
            normalized_servers.append(server)
        merged["nntp_servers"] = normalized_servers
    return merged

@uploads_router.get("/jobs")
def get_jobs(
    service: UploadService = Depends(get_upload_service),
) -> List[Dict[str, Any]]:
    """Retrieve compact upload-job snapshots for frequent dashboard polling."""
    return service.get_active_jobs(compact=True)

@uploads_router.get("/stream-monitors")
async def get_stream_monitors() -> Dict[str, Any]:
    """List configured stream folder monitors."""
    return {"monitors": usenet_stream.list_stream_monitors()}

@uploads_router.delete("/stream-monitors/{monitor_id}")
async def delete_stream_monitor(monitor_id: str) -> Dict[str, Any]:
    """Remove a configured stream folder monitor."""
    if not usenet_stream.remove_stream_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="Stream monitor not found")
    await usenet_stream.restart_stream_monitors()
    return {"status": "deleted", "monitor_id": monitor_id}

@uploads_router.post("/jobs/{job_id}/stop")
async def stop_upload(job_id: str, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
    """Request a specific job to stop."""
    if service.stop_job(job_id):
        return {"status": "stopping", "message": "Termination signal sent to job."}
    raise HTTPException(status_code=404, detail="Job not found")

@uploads_router.post("/jobs/{job_id}/stop-clear")
async def stop_clear_upload(job_id: str, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
    """Stop a specific job and remove it from the visible queue as soon as possible."""
    if service.stop_and_clear_job(job_id):
        return {"status": "clearing", "message": "Job stopping and clearing from the queue."}
    raise HTTPException(status_code=404, detail="Job not found")

@uploads_router.post("/jobs/{job_id}/pause")
async def pause_upload(job_id: str, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
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

@uploads_router.post("/jobs/{job_id}/resume")
async def resume_upload(job_id: str, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
    """Resume a paused upload job."""
    if service.resume_job(job_id):
        job = service.get_job(job_id) or {}
        queued = bool(job.get("resume_requested"))
        return {
            "status": job.get("status", "running"),
            "message": "Job queued to resume when the scheduler lane is available." if queued else "Job resumed.",
        }
    raise HTTPException(status_code=404, detail="Job not found or not paused")

@uploads_router.post("/jobs/{job_id}/retry")
async def retry_upload(job_id: str, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
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

@uploads_router.delete("/jobs/completed")
async def clear_completed(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Clear all finished jobs from memory."""
    cleared = service.clear_completed_jobs()
    return {"cleared": cleared}

@uploads_router.delete("/jobs/{job_id}")
async def delete_job_route(job_id: str, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
    """Remove a job from memory."""
    if service.delete_job(job_id):
        return {"status": "deleted"}
    raise HTTPException(status_code=404, detail="Job not found")

@uploads_router.get("/queue")
def get_queue(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve structured queue data: running, queued, and recently finished jobs."""
    from logic.services import build_queue_snapshot

    return build_queue_snapshot(service)

@uploads_router.post("/queue/pause")
async def pause_queue_processing(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Pause queue processing and pause the currently running job (if any)."""
    service.pause_queue(pause_active=True)
    return {
        "status": "paused",
        "message": "Queue processing paused. New jobs remain queued until resumed.",
        "control": service.get_queue_control_state(),
    }

@uploads_router.post("/queue/resume")
async def resume_queue_processing(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Resume queue processing and continue with paused/waiting jobs."""
    service.resume_queue()
    return {
        "status": "running",
        "message": "Queue processing resumed.",
        "control": service.get_queue_control_state(),
    }

@uploads_router.post("/queue/stop")
async def stop_queue_processing(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Stop the active job and pause queue processing until resumed."""
    service.stop_queue()
    return {
        "status": "stopped",
        "message": "Active job stopping. Queue processing is paused.",
        "control": service.get_queue_control_state(),
    }

@uploads_router.post("/queue/stop-clear")
async def stop_clear_queue_processing(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Stop the active job, clear waiting jobs, and hide the active row while it shuts down."""
    result = service.stop_queue_and_clear()
    return {
        "status": "clearing",
        "message": "Active job stopping and queue clearing.",
        **result,
    }

@uploads_router.post("/queue/clear")
async def clear_queue(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Cancel all queued (not yet running) jobs."""
    cleared = service.clear_queued_jobs()
    return {"cleared": cleared}

@uploads_router.post("/queue/{job_id}/promote")
async def promote_queued_job(
    job_id: str,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Move a queued job to the front of the queue (next to run)."""
    if service.promote_job(job_id):
        return {"status": "promoted", "job_id": job_id}
    raise HTTPException(status_code=404, detail="Job not found or not queued")

@uploads_router.get("/queue/{job_id}/items")
def get_queued_job_items(
    job_id: str,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve target paths for a queued job so the UI can edit it."""
    items = service.get_queued_job_items(job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Queued job not found")
    return {"job_id": job_id, "items": items, "count": len(items)}

@uploads_router.get("/queue/{job_id}/active-items")
def get_active_job_items(
    job_id: str,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve remaining target paths only while an active editor is open."""
    items = service.get_active_job_items(job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Active job not found")
    return {"job_id": job_id, "items": items, "count": len(items)}

@uploads_router.get("/queue/{job_id}/completed-items")
def get_completed_job_items(
    job_id: str,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve item paths for a recently finished job (current session only)."""
    items = service.get_finished_job_items(job_id)
    if items is None:
        raise HTTPException(status_code=404, detail="Finished job not found")
    return {"job_id": job_id, "items": items, "count": len(items)}

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

@uploads_router.get("/queue/items")
def get_queue_items(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve all items in the upload queue."""
    items = service.get_queue_items()
    return {"items": items, "count": len(items)}

@uploads_router.delete("/queue/items/{item_id}")
async def remove_queue_item(
    item_id: int,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Remove a single item from the queue."""
    if service.remove_queue_item(item_id):
        return {"status": "removed", "item_id": item_id}
    raise HTTPException(status_code=404, detail="Queue item not found")

@uploads_router.post("/queue/items/clear")
async def clear_queue_items(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Clear all items from the upload queue."""
    cleared = service.clear_queue_items()
    return {"cleared": cleared}

@uploads_router.post("/queue/items/{item_id}/start")
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

def _history_database_error(exc: Exception) -> HTTPException:
    """Readable 503 for History routes when the upload database cannot be used.

    The History page shows ``detail`` as-is, so it must stay a plain string.
    """
    logger.warning(f"[HISTORY] Upload database error: {exc}")
    return HTTPException(
        status_code=503,
        detail=f"The upload history database is unavailable ({exc}). Try again in a moment.",
    )

@uploads_router.get("/recent")
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
        return database.get_recent_uploads(
            limit=limit,
            offset=offset,
            search=search,
            destination=destination,
            literal=literal,
            sort_by=sort_by,
            order=order,
        )
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@uploads_router.get("/grouped")
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
        return database.get_grouped_uploads(
            page=page,
            per_page=per_page,
            search=search,
            destination=destination,
            literal=literal,
            sort_by=sort_by,
            order=order,
        )
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@uploads_router.get("/grouped/items")
def get_grouped_items(title_key: str, destination: str = "all") -> Dict[str, Any]:
    """Full upload rows behind one grouped-history row.

    Backs the History page's group expansion; database.get_group_upload_items
    already existed but had no route, so expanding a group 404'd.
    """
    try:
        return database.get_group_upload_items(title_key, destination=destination)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@uploads_router.get("/errors/grouped")
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
        return database.get_grouped_upload_errors(indexer_id=destination, limit=limit, since_days=since_days, include_muted=include_muted)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

class MuteIssueRequest(BaseModel):
    """API request model for muting/unmuting a known-issue group.

    A group has no id of its own; (indexer_id, signature) is the same pair
    get_grouped_upload_errors groups by, so it is also the mute key.
    """

    indexer_id: str
    signature: str

@uploads_router.post("/errors/mute")
def mute_grouped_error(body: MuteIssueRequest) -> Dict[str, Any]:
    """Silence a known-issue group so it stops standing out in the default view."""
    try:
        database.mute_upload_issue(body.indexer_id, body.signature)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "muted": True}

@uploads_router.post("/errors/unmute")
def unmute_grouped_error(body: MuteIssueRequest) -> Dict[str, Any]:
    """Restore a previously muted known-issue group to the default view."""
    try:
        database.unmute_upload_issue(body.indexer_id, body.signature)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "muted": False}

@uploads_router.get("/history")
async def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Listing of completed upload jobs."""
    try:
        return database.get_job_history(limit)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@uploads_router.get("/history/{job_id}/uploads")
async def get_job_uploads(job_id: str) -> List[Dict[str, Any]]:
    """Fetch individual items for a given job ID."""
    try:
        return database.get_uploads_for_job(job_id)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc

@uploads_router.delete("/history")
async def delete_job_history(request: Request) -> Dict[str, Any]:
    """Delete one or more job history records."""
    body = await request.json()
    job_ids = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")
    try:
        deleted = database.delete_job_history(job_ids)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "deleted": deleted}

@uploads_router.get("/hourly-stats")
async def get_hourly_stats() -> Dict[str, Any]:
    """Fetch recent performance metrics."""
    return database.get_hourly_upload_stats()

@uploads_router.delete("/item/{item_name}")
async def delete_upload_item(item_name: str) -> Dict[str, Any]:
    """Remove a single item from the history database."""
    try:
        deleted = database.delete_upload_item(item_name)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    if deleted:
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to delete item")

class BulkDeleteRequest(BaseModel):
    """API request model for bulk deleting items."""

    item_names: List[str]

@dashboard_router.get("/summary")
def get_summary(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Get a summary of current stats and queue sizes for the dashboard."""
    return service.get_dashboard_summary()

@dashboard_router.get("/database-health")
async def database_health_check() -> Dict[str, Any]:
    """Check database connection, tables, and recent activity."""
    return await asyncio.to_thread(database.get_database_health)

@console_router.get("/logs")
async def get_logs(
    after: int = 0,
    after_seq: Optional[int] = None,
    count: Optional[int] = None,
    tail: Optional[int] = None,
) -> Dict[str, Any]:
    """Fetch log messages from the console buffer.

    Pass ``tail=N`` on the first request to jump to the most recent N
    entries instead of replaying the entire buffer from the start.
    """
    if tail is not None:
        logs, last_seq = console.get_tail(tail)
        return {"logs": logs, "last_seq": last_seq, "seq": last_seq, "count": len(logs)}

    seq = after_seq if after_seq is not None else after
    logs, last_seq = console.get_logs(after_seq=seq, limit=count)
    return {
        "logs": logs,
        "last_seq": last_seq,
        "seq": last_seq,
        "count": len(logs),
    }

@console_router.get("/log-file")
async def download_log_file() -> Response:
    """Serve the raw log file for viewing / download."""
    log_path = Path(__file__).resolve().parent / "data" / "logs" / "nzbpostarr.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(
        str(log_path),
        media_type="text/plain",
        filename="nzbpostarr-log.txt",
        headers={"Content-Disposition": "inline; filename=nzbpostarr-log.txt"},
    )

@indexers_router.post("/reload")
async def reload_indexers_route() -> Dict[str, Any]:
    """Reload all indexer definitions from YAML files."""
    from core.registry import get_all_indexers, reload_indexers
    from logic.pending_snapshot import invalidate_pending_indexer_context

    reload_indexers()
    invalidate_pending_indexer_context()
    return {"status": "success", "count": len(get_all_indexers())}

@settings_router.get("/browse")
async def browse_folders(path: str = "/") -> Dict[str, Any]:
    """Browse local directories for the folder picker.

    Returns a list of subdirectories at the given path,
    along with the resolved parent path for navigation.
    """
    import platform

    target = Path(path) if path and path != "/" else Path("/")

    # On Windows, list drive letters when at root
    if platform.system() == "Windows" and (str(target) == "/" or str(target) == "\\"):
        drives = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append({"name": f"{letter}:", "path": drive})
        return {"path": "/", "parent": None, "dirs": drives}

    try:
        target = target.resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid path") from exc

    if not target.exists():
        raise HTTPException(status_code=404, detail="Path does not exist")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    parent = str(target.parent) if target.parent != target else None

    dirs = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": str(entry)})
    except PermissionError:
        pass  # Return empty list for inaccessible dirs

    return {"path": str(target), "parent": parent, "dirs": dirs}

