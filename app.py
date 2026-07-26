"""
📦 NZBPostarr - Web Application
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FastAPI application: API routes and app setup.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import copy
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import BaseModel, Field
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core import database
from core.config import get_config
from core.redaction import SECRET_MASK
from core.utils import VIDEO_EXTENSIONS, start_watchdog_observer, stop_watchdog_observer
from logic import pending_snapshot as pending_snapshot_mod
from logic import processing, updater, usenet_stream
from logic.pending_index import get_pending_index_manager
from logic.pending_scan import (
    get_configured_category_folders,
    get_configured_folders,
    scan_configured_items,
)
from logic.queueing import ProcessingJobRequest
from logic.services import UploadService, console, get_upload_service
from logic.stats_engine import (
    dashboard_stats_enabled as _shared_dashboard_stats_enabled,
    history_tracking_enabled as _shared_history_tracking_enabled,
    stats_page_enabled as _shared_stats_page_enabled,
)

_PENDING_BUILD_SUMMARY = pending_snapshot_mod.build_pending_summary
_PENDING_FILTER = pending_snapshot_mod.filter_pending_snapshot

# ============================================================
# MODELS
# ============================================================


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


class StopAllRequest(BaseModel):
    """API request model for stopping all work and waiting for quiescence."""

    clear_staged_items: bool = True
    wait_timeout_seconds: float = 15.0


# ============================================================
# ROUTERS
# ============================================================

uploads_router = APIRouter(prefix="/api/uploads", tags=["uploads"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])
console_router = APIRouter(prefix="/api/console", tags=["console"])
stats_router = APIRouter(prefix="/api/stats", tags=["stats"])
tests_router = APIRouter(prefix="/api/tests", tags=["tests"])
system_router = APIRouter(prefix="/api/system", tags=["system"])


def _stats_page_enabled(conf: Optional[Any] = None) -> bool:
    current = conf or get_config()
    return _shared_stats_page_enabled(current)


def _dashboard_server_stats_enabled(conf: Optional[Any] = None) -> bool:
    current = conf or get_config()
    return _shared_dashboard_stats_enabled(current)


def _stats_history_enabled(conf: Optional[Any] = None) -> bool:
    current = conf or get_config()
    return _shared_history_tracking_enabled(current)


def _stats_collector_required(conf: Optional[Any] = None) -> bool:
    return _stats_history_enabled(conf)


def _resolved_policy_path(value: Any) -> Path:
    path = Path(str(value or "").strip())
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _bulk_selection_excluded_roots(conf: Any) -> tuple[Path, ...]:
    """Return configured roots which must not participate in mass selection."""
    get_entries = getattr(conf, "get_folder_path_entries", None)
    entries = get_entries() if callable(get_entries) else getattr(conf, "folder_paths", [])
    roots: list[Path] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or bool(entry.get("allow_bulk_selection", True)):
            continue
        raw_path = str(entry.get("path") or "").strip()
        if raw_path:
            roots.append(_resolved_policy_path(raw_path))
    return tuple(roots)


def _path_is_at_or_below(path_value: Any, root: Path) -> bool:
    candidate = _resolved_policy_path(path_value)
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


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


def _normalize_request_strings(values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return normalized


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
        processing.preview_processing_items,
        items,
        target_indexer_id=normalized_indexer_ids[0] if len(normalized_indexer_ids) == 1 else None,
        target_indexer_ids=normalized_indexer_ids,
        enable_duplicate_check=enable_duplicate_check,
        force=force,
        test_mode=test_mode,
        skip_packs=skip_packs,
        skip_episodes=skip_episodes,
    )


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


def _require_stats_page_enabled() -> None:
    if not _stats_page_enabled():
        raise HTTPException(status_code=404, detail="Stats page is disabled")


def _require_dashboard_server_stats_enabled() -> None:
    if not _dashboard_server_stats_enabled():
        raise HTTPException(status_code=404, detail="Dashboard server stats are disabled")


def _require_stats_history_enabled() -> None:
    if not _stats_history_enabled():
        raise HTTPException(status_code=404, detail="Stats history is disabled")


async def _sync_stats_collector_state(conf: Optional[Any] = None) -> None:
    current = conf or get_config()
    from logic.stats_engine import (
        start_collector,
        stop_collector,
        sync_collector_schedule,
    )

    if _stats_collector_required(current):
        await start_collector()
    else:
        await stop_collector()
    sync_collector_schedule()


@uploads_router.get("/jobs")
def get_jobs(
    service: UploadService = Depends(get_upload_service),
) -> List[Dict[str, Any]]:
    """Retrieve compact upload-job snapshots for frequent dashboard polling."""
    return service.get_active_jobs(compact=True)


@uploads_router.post("/start")
async def start_upload(req: UploadRequest, service: UploadService = Depends(get_upload_service)) -> Dict[str, Any]:
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


@uploads_router.post("/preview")
async def preview_upload(req: UploadRequest) -> Dict[str, Any]:
    """Preview bulk filters and upload eligibility without creating a job."""
    selected_items, selection_meta = _select_upload_request_items(req, get_config())
    preview = await _preview_selected_items(
        list(selected_items),
        enable_duplicate_check=req.enable_duplicate_check,
        test_mode=req.test_mode,
        indexer_id=req.indexer_id,
        indexer_ids=req.indexer_ids,
        skip_packs=req.skip_packs,
        skip_episodes=req.skip_episodes,
    )
    preview["selection"] = selection_meta
    return preview


@uploads_router.post("/stream-nzb", response_model=StreamStartResponse)
async def start_streamed_nzb_upload(
    file: Optional[UploadFile] = File(None),
    source_path: Optional[str] = Form(None),
    monitor_folder: bool = Form(False),
    category: Optional[str] = Form(None),
    release_name: Optional[str] = Form(None),
    submit_mode: str = Form("post_and_submit"),
    posting_server_name: Optional[str] = Form(None),
    test_mode: bool = Form(False),
    enable_duplicate_check: bool = Form(True),
    indexer_id: Optional[str] = Form(None),
    service: UploadService = Depends(get_upload_service),
) -> StreamStartResponse:
    """Upload an NZB manifest and queue a direct Usenet-to-Usenet stream job."""
    try:
        request_options = usenet_stream.normalize_stream_request(
            upload_filename=file.filename if file else None,
            source_path=source_path,
            monitor_folder=monitor_folder,
            category=category,
            submit_mode=submit_mode,
        )
    except usenet_stream.StreamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source_path = request_options.source_path
    category = request_options.category
    normalized_submit_mode = request_options.submit_mode

    tmp_path: Optional[Path] = None
    try:
        if monitor_folder:
            monitor = usenet_stream.add_stream_monitor(
                folder_path=source_path,
                category=category,
                posting_server_name=posting_server_name,
                submit_mode=normalized_submit_mode,
                indexer_id=indexer_id,
                enable_duplicate_check=enable_duplicate_check,
                test_mode=test_mode,
            )
            await usenet_stream.restart_stream_monitors()
            return StreamStartResponse(
                status="monitoring",
                mode="monitor",
                message=f"Watching {monitor['folder_path']} for new NZB files",
                monitor=monitor,
            )

        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".nzb") as tmp:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise HTTPException(status_code=400, detail="Uploaded NZB was empty")

            resolved_category = usenet_stream.resolve_stream_category(tmp_path, category)
            job_id = service.start_usenet_stream_job(
                category=resolved_category,
                stream_source_path=str(tmp_path),
                stream_source_name=file.filename,
                release_name=release_name,
                test_mode=test_mode,
                enable_duplicate_check=enable_duplicate_check,
                indexer_id=indexer_id,
                posting_server_name=posting_server_name,
                submit_mode=normalized_submit_mode,
                cleanup_paths=[str(tmp_path)],
            )
            return StreamStartResponse(
                job_id=job_id,
                job_ids=[job_id],
                status="started",
                mode="job",
                message=f"Queued stream job for {file.filename}",
            )

        resolved_paths = usenet_stream.resolve_source_nzb_paths(source_path)
        if len(resolved_paths) > 1 and release_name:
            logger.info("Ignoring custom release name for multi-file server-path stream request")

        job_ids: list[str] = []
        for path in resolved_paths:
            resolved_category = usenet_stream.resolve_stream_category(path, category)
            job_ids.append(
                service.start_usenet_stream_job(
                    category=resolved_category,
                    stream_source_path=str(path),
                    stream_source_name=path.name,
                    release_name=release_name if len(resolved_paths) == 1 else None,
                    test_mode=test_mode,
                    enable_duplicate_check=enable_duplicate_check,
                    indexer_id=indexer_id,
                    posting_server_name=posting_server_name,
                    submit_mode=normalized_submit_mode,
                )
            )

        return StreamStartResponse(
            job_id=job_ids[0] if len(job_ids) == 1 else None,
            job_ids=job_ids,
            status="started",
            mode="batch" if len(job_ids) > 1 else "job",
            message=f"Queued {len(job_ids)} stream job(s)",
        )
    except usenet_stream.StreamError as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.exception(f"Failed to queue streamed NZB upload: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to queue streamed NZB upload: {exc}") from exc
    finally:
        if file is not None:
            await file.close()


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


@uploads_router.patch("/jobs/{job_id}/name")
async def rename_upload_job(
    job_id: str,
    req: RenameJobRequest,
    service: UploadService = Depends(get_upload_service),
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
    all_jobs = service.get_active_jobs(compact=True)

    def is_resumable_stopped(job: Dict[str, Any]) -> bool:
        if job.get("status") != "stopped":
            return False
        if str(job.get("job_type") or "processing") != "processing":
            return False
        return bool(job.get("has_explicit_paths"))

    running = [j for j in all_jobs if j.get("status") in ("running", "stopping", "paused")]
    queued = sorted(
        [j for j in all_jobs if j.get("status") == "queued" or is_resumable_stopped(j)],
        key=lambda j: (-int(j.get("priority") or 0), j.get("started_at", "")),
    )
    finished = [
        j
        for j in all_jobs
        if j.get("status") in ("completed", "failed", "cancelled")
        or (j.get("status") == "stopped" and not is_resumable_stopped(j))
    ]
    return {
        "running": running,
        "queued": queued,
        "finished": finished,
        "control": service.get_queue_control_state(),
        "counts": {
            "running": len(running),
            "queued": len(queued),
            "finished": len(finished),
        },
    }


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


@uploads_router.post("/queue/revalidate")
async def revalidate_queue_jobs(
    req: QueueRevalidateRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Re-scan queued/paused jobs against the current classification rules."""
    result = service.revalidate_queued_jobs(include_paused=bool(req.include_paused))
    return {"status": "success", **result}


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


@uploads_router.patch("/queue/{job_id}/schedule")
async def set_queued_job_schedule(
    job_id: str,
    req: QueueScheduleRequest,
    service: UploadService = Depends(get_upload_service),
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


@uploads_router.patch("/queue/{job_id}/priority")
async def set_queued_job_priority(
    job_id: str,
    req: QueuePriorityRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Set durable scheduler priority for a queued or paused job."""
    ok, priority = service.set_job_priority(job_id, req.priority)
    if not ok:
        if service.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(status_code=409, detail="Only queued or paused jobs can change priority")
    return {"status": "updated", "job_id": job_id, "priority": priority}


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


@uploads_router.put("/queue/{job_id}/items/reorder")
async def reorder_queued_job_items_route(
    job_id: str,
    req: ReorderQueuedJobItemsRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Reorder queued-job target paths by providing the full desired path order."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="No paths provided")

    if service.reorder_queued_job_items(job_id, req.paths):
        return {"status": "reordered", "job_id": job_id}

    if service.get_queued_job_items(job_id) is None:
        raise HTTPException(status_code=404, detail="Queued job not found")
    raise HTTPException(status_code=400, detail="Invalid path order for queued job")


@uploads_router.put("/queue/{job_id}/active-items/reorder")
async def reorder_active_job_items_route(
    job_id: str,
    req: ReorderQueuedJobItemsRequest,
    service: UploadService = Depends(get_upload_service),
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


@uploads_router.delete("/queue/{job_id}/items")
async def remove_queued_job_item_route(
    job_id: str,
    req: RemoveQueuedJobItemRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Remove a single target path from a queued job."""
    if not req.path:
        raise HTTPException(status_code=400, detail="No path provided")

    if service.remove_queued_job_item(job_id, req.path):
        return {"status": "removed", "job_id": job_id, "path": req.path}

    if service.get_queued_job_items(job_id) is None:
        raise HTTPException(status_code=404, detail="Queued job not found")
    raise HTTPException(status_code=404, detail="Path not found in queued job")


# ── Item-level queue endpoints ──────────────────────────────────────


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


@uploads_router.post("/queue/items")
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


@uploads_router.put("/queue/items/reorder")
async def reorder_queue_items(
    req: ReorderQueueRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Reorder queue items. Provide all item IDs in the desired order."""
    if service.reorder_queue_items(req.item_ids):
        return {"status": "reordered"}
    raise HTTPException(
        status_code=400,
        detail="Invalid item IDs — must include all current queue items",
    )


@uploads_router.post("/queue/start")
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


@uploads_router.post("/queue/preview")
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
    return database.get_recent_uploads(
        limit=limit,
        offset=offset,
        search=search,
        destination=destination,
        literal=literal,
        sort_by=sort_by,
        order=order,
    )


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
    return database.get_grouped_uploads(
        page=page,
        per_page=per_page,
        search=search,
        destination=destination,
        literal=literal,
        sort_by=sort_by,
        order=order,
    )


@uploads_router.get("/history")
async def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    """Listing of completed upload jobs."""
    return database.get_job_history(limit)


@uploads_router.get("/history/{job_id}/uploads")
async def get_job_uploads(job_id: str) -> List[Dict[str, Any]]:
    """Fetch individual items for a given job ID."""
    return database.get_uploads_for_job(job_id)


@uploads_router.delete("/history")
async def delete_job_history(request: Request) -> Dict[str, Any]:
    """Delete one or more job history records."""
    body = await request.json()
    job_ids = body.get("job_ids", [])
    if not job_ids:
        raise HTTPException(status_code=400, detail="No job IDs provided")
    deleted = database.delete_job_history(job_ids)
    return {"status": "success", "deleted": deleted}


@uploads_router.get("/hourly-stats")
async def get_hourly_stats() -> Dict[str, Any]:
    """Fetch recent performance metrics."""
    return database.get_hourly_upload_stats()


@uploads_router.delete("/item/{item_name}")
async def delete_upload_item(item_name: str) -> Dict[str, Any]:
    """Remove a single item from the history database."""
    if database.delete_upload_item(item_name):
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to delete item")


class BulkDeleteRequest(BaseModel):
    """API request model for bulk deleting items."""

    item_names: List[str]


@uploads_router.post("/item/bulk-delete")
async def bulk_delete_upload_items(req: BulkDeleteRequest) -> Dict[str, Any]:
    """Remove multiple items from the history database."""
    if not req.item_names:
        return {"status": "success", "deleted_count": 0}

    deleted_count = database.bulk_delete_upload_items(req.item_names)
    return {"status": "success", "deleted_count": deleted_count}


@dashboard_router.get("/summary")
def get_summary(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Get a summary of current stats and queue sizes for the dashboard."""
    return service.get_dashboard_summary()


@dashboard_router.get("/system-stats")
def get_system_stats() -> Dict[str, Any]:
    """Retrieve live system resource usage."""
    _require_dashboard_server_stats_enabled()
    from logic.stats_engine import get_full_system_info, mark_ui_active

    mark_ui_active(mode="mini")
    info = get_full_system_info()
    cpu = info["cpu"]
    mem = info["memory"]
    disk = info["disk"]
    net = info["network"]

    return {
        "hostname": info["hostname"],
        "platform": info["platform"],
        "uptime_seconds": info["uptime_seconds"],
        "cpu_percent": cpu["percent"],
        "memory_percent": mem["percent"],
        "memory_used_gb": round(mem["used_gb"], 2),
        "memory_total_gb": round(mem["total_gb"], 2),
        "disk_percent": disk["percent"],
        "disk_used_gb": round(disk["used_gb"], 2),
        "disk_total_gb": round(disk["total_gb"], 2),
        "disk_free_gb": round(disk["free_gb"], 2),
        "network_upload_mbps": net["upload_mbps"],
        "network_download_mbps": net["download_mbps"],
        "conns": net["connections"],
        "errin": net["errin"],
        "errout": net["errout"],
        "dropin": net["dropin"],
        "dropout": net["dropout"],
    }


@dashboard_router.get("/health")
async def health_check() -> Dict[str, Any]:
    """Check the health of tools and directory structure."""
    import shutil

    conf = get_config()
    tools = {t: bool(shutil.which(t)) for t in ["rar", "parpar", "nyuu"]}
    configured_folders = get_configured_folders(conf)
    folders = {
        "configured": any(folder.exists() for folder in configured_folders),
        "missing": sum(1 for folder in configured_folders if not folder.exists()),
        "nzb_output": conf.get_nzb_path("test").parent.exists(),
    }
    return {
        "status": "Healthy" if all(tools.values()) and any(folders.values()) else "Warning",
        "tools": tools,
        "folders": folders,
        "nntp": bool(conf.nntp_servers),
    }


@dashboard_router.get("/database-health")
async def database_health_check() -> Dict[str, Any]:
    """Check database connection, tables, and recent activity."""
    return await asyncio.to_thread(database.get_database_health)


@stats_router.get("/summary")
async def get_stats_summary(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve summarized historical statistics."""
    _require_stats_page_enabled()
    return await asyncio.to_thread(service.get_statistics)


@stats_router.get("/full")
async def get_full_stats(collapsed: str = "") -> Dict[str, Any]:
    """Retrieve comprehensive system statistics from the engine."""
    _require_stats_page_enabled()
    from logic.stats_engine import get_full_system_info, mark_ui_active

    # Convert comma-separated string to list
    collapsed_list = [c.strip() for c in collapsed.split(",") if c.strip()]
    mark_ui_active(mode="full", collapsed=collapsed_list)

    return await asyncio.to_thread(get_full_system_info)


@stats_router.get("/mini")
async def get_mini_stats() -> Dict[str, Any]:
    """Lightweight stats endpoint for dashboard polling."""
    _require_stats_history_enabled()
    from logic.stats_engine import get_full_system_info, mark_ui_active

    mark_ui_active(mode="mini")
    info = await asyncio.to_thread(get_full_system_info)

    mem = info["memory"]
    disk = info["disk"]
    net = info["network"]

    return {
        "hostname": info["hostname"],
        "platform": info["platform"],
        "uptime_seconds": info["uptime_seconds"],
        "cpu_percent": info["cpu"]["percent"],
        "memory_used_gb": round(mem["used_gb"], 2),
        "memory_total_gb": round(mem["total_gb"], 2),
        "memory_percent": mem["percent"],
        "disk_used_gb": round(disk["used_gb"], 2),
        "disk_total_gb": round(disk["total_gb"], 2),
        "disk_free_gb": round(disk["free_gb"], 2),
        "disk_percent": disk["percent"],
        "network_upload_mbps": net["upload_mbps"],
        "network_download_mbps": net["download_mbps"],
    }


@stats_router.get("/top-directories")
async def get_top_directories(limit: int = 25) -> Dict[str, Any]:
    """Retrieve top-level storage usage data based on processed items."""
    _require_stats_page_enabled()
    return await asyncio.to_thread(database.get_top_directories, limit=limit)


@stats_router.get("/history")
def get_stats_history(response: Response, limit: int = 100) -> Dict[str, Any]:
    """Retrieve historical system performance data for the sparklines.

    Reads from the in-memory ring buffer — zero DB hits.
    """
    _require_stats_history_enabled()
    from logic.stats_engine import (
        get_iface_history,
    )
    from logic.stats_engine import (
        get_stats_history as _mem_hist,
    )

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    history = _mem_hist(limit)
    iface_history = get_iface_history(limit)
    return {"history": history, "interfaces": iface_history}


@stats_router.post("/history/record")
async def record_stats(
    cpu: float,
    memory: float,
    upload_mbps: float,
    download_mbps: float,
    disk_percent: float = 0,
    disk_free_gb: float = 0,
    disk_read: float = 0,
    disk_write: float = 0,
    total_sent: float = 0,
    total_recv: float = 0,
    connections: int = 0,
    errors_in: int = 0,
    errors_out: int = 0,
    drops_in: int = 0,
    drops_out: int = 0,
) -> Dict[str, Any]:
    """Record current performance stats to the database."""
    _require_stats_page_enabled()
    database.record_system_stats(
        cpu=cpu,
        mem=memory,
        up=upload_mbps,
        down=download_mbps,
        total_sent=total_sent,
        total_recv=total_recv,
        connections=connections,
        disk_percent=disk_percent,
        disk_free_gb=disk_free_gb,
        disk_read=disk_read,
        disk_write=disk_write,
        errors_in=errors_in,
        errors_out=errors_out,
        drops_in=drops_in,
        drops_out=drops_out,
    )
    return {"status": "ok"}


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


# ============================================================
# INDEXER ROUTES (Dynamic Plugin System)
# ============================================================

indexers_router = APIRouter(prefix="/api/indexers", tags=["indexers"])


@indexers_router.get("")
@indexers_router.get("/")
async def get_all_indexers_route() -> List[Dict[str, Any]]:
    """Retrieve all loaded indexer definitions."""
    from core.registry import get_all_indexers

    conf = get_config()
    return [idx.to_ui_dict(conf) for idx in get_all_indexers()]


@indexers_router.post("/reload")
async def reload_indexers_route() -> Dict[str, Any]:
    """Reload all indexer definitions from YAML files."""
    from core.registry import get_all_indexers, reload_indexers

    reload_indexers()
    return {"status": "success", "count": len(get_all_indexers())}


@indexers_router.get("/{indexer_id}")
async def get_indexer_route(indexer_id: str) -> Dict[str, Any]:
    """Get a specific indexer definition."""
    from core.registry import get_indexer

    idx = get_indexer(indexer_id)
    if not idx:
        raise HTTPException(status_code=404, detail=f"Indexer '{indexer_id}' not found")

    conf = get_config()
    data = idx.to_ui_dict(conf)

    # Add extra fields only needed for detail view
    data.update(
        {
            "categories": idx.categories.model_dump(),
            "submit_url": idx.submit_url,
            "method": idx.method,
        }
    )
    return data


@settings_router.get("")
@settings_router.get("/")
async def get_current_settings() -> Dict[str, Any]:
    """Retrieve the current active configuration grouped for the UI."""
    from core.registry import (
        get_all_indexers,
        get_available_categories,
        resolve_indexer_backfill,
        resolve_indexer_enabled,
        resolve_indexer_priority,
    )

    conf = get_config()

    # Build destinations dynamically from loaded indexers
    destinations = {
        "enable_backfill": conf.enable_backfill,
        "enable_duplicate_bypass": getattr(conf, "enable_duplicate_bypass", False),
    }

    for idx in get_all_indexers():
        destinations[f"enable_{idx.id}"] = resolve_indexer_enabled(idx, conf)
        destinations[f"backfill_{idx.id}"] = resolve_indexer_backfill(idx, conf)
        destinations[f"priority_{idx.id}"] = resolve_indexer_priority(idx, conf)

    return {
        "destinations": destinations,
        "processing": {
            "verbose": conf.verbose,
            "process_tv_episodes": getattr(conf, "process_tv_episodes", True),
            "enable_duplicate_checking": conf.enable_duplicate_checking,
            "enable_anime_checking": getattr(conf, "enable_anime_checking", False),
            "item_limit_per_category": getattr(conf, "item_limit_per_category", None),
            "folder_size_limit_gb": getattr(conf, "folder_size_limit_gb", 99),
            "folder_size_limit_enabled": getattr(conf, "folder_size_limit_enabled", True),
            "file_size_limit_gb": getattr(conf, "file_size_limit_gb", 0),
            "file_size_limit_enabled": getattr(conf, "file_size_limit_enabled", True),
            "dynamic_packs": getattr(conf, "dynamic_packs", True),
            "tv_pack_ignore": getattr(
                conf,
                "tv_pack_ignore",
                {
                    "enabled": True,
                    "ignore_non_video": True,
                    "ignore_extras": True,
                    "require_sxxexx": True,
                    "require_resolution": True,
                    "require_source": True,
                },
            ),
        },
        "upload": {
            "poster_name": conf.poster_name,
            "poster_email": conf.poster_email,
            "rar_size": conf.rar_size,
            "article_size": conf.article_size,
            "include_readme": getattr(conf, "include_readme", True),
            "upload_max_retries": getattr(conf, "upload_max_retries", 3),
            "upload_retry_delay_seconds": getattr(conf, "upload_retry_delay_seconds", 5),
            "alt_bins": getattr(conf, "alt_bins", []),
        },
        "folders": {
            "base_folder": str(conf.base_folder),
            "folder_paths": conf.get_folder_path_entries(),
        },
        "nntp_servers": [_mask_config_secrets(s.model_dump()) for s in conf.nntp_servers],
        "api_keys": _mask_config_secrets(conf.api_keys, key="api_keys"),
        "usernames": _mask_config_secrets(conf.usernames, key="usernames"),
        "ui": {
            "dashboard_stats_enabled": getattr(conf, "dashboard_stats_enabled", True),
            "ui_refresh_seconds": getattr(conf, "ui_refresh_seconds", 2),
            "dashboard_stats_modules": getattr(
                conf,
                "dashboard_stats_modules",
                ["cpu", "memory", "disk", "free_space", "upload", "download"],
            ),
            "stats_page_enabled": getattr(conf, "stats_page_enabled", True),
        },
        "skip_files": getattr(
            conf,
            "skip_files",
            {"enabled": False, "display_mode": "disabled", "patterns": []},
        ),
        "auth": {
            "enable_password": getattr(conf, "enable_password", False),
            "web_username": getattr(conf, "web_username", "admin"),
            # web_password intentionally NOT exposed
        },
        "categories": get_available_categories(),
        # Include list of indexers for the UI to render
        "indexers": [idx.to_ui_dict(conf) for idx in get_all_indexers()],
    }


@settings_router.post("/reset")
async def reset_settings_route() -> Dict[str, Any]:
    """Reset configuration to defaults from config.defaults.yaml."""
    from core.config import get_defaults_config_path, replace_config_content

    defaults_path = get_defaults_config_path()

    try:
        if defaults_path.exists():
            new_config = replace_config_content(defaults_path.read_text(encoding="utf-8"))
            await _sync_stats_collector_state(new_config)
            return {
                "status": "success",
                "message": "Settings reset to defaults from config.defaults.yaml",
            }
        raise HTTPException(status_code=404, detail="Defaults file not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reset: {str(e)}") from e


@settings_router.get("/config")
async def get_current_config() -> Dict[str, Any]:
    """Retrieve the current active configuration (flat)."""
    return _mask_config_secrets(get_config().model_dump(by_alias=True))


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


@settings_router.put("/{_section}")
async def update_settings(_section: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update a specific section of the configuration."""
    from core.config import save_config

    updates = _merge_masked_secret_updates(updates, get_config())
    if save_config(updates):
        if _section == "ui":
            await _sync_stats_collector_state()

        # Restart folder monitor if folder settings changed (monitor flags may have toggled)
        if _section == "folders":
            monitor_status: Dict[str, Any] = {"attempted": True, "ok": True}
            try:
                from logic.folder_monitor import restart_folder_monitor

                await restart_folder_monitor()
            except Exception as exc:
                logger.warning(f"Folder monitor restart failed after settings update: {exc}")
                monitor_status = {
                    "attempted": True,
                    "ok": False,
                    "error": str(exc),
                }

            try:
                conf = get_config()
                _pending_index.restart_watched_folders(_pending_watch_folders(conf))
            except Exception as exc:
                logger.warning(f"Pending index watcher restart failed after settings update: {exc}")

            if not monitor_status["ok"]:
                return {
                    "status": "partial_success",
                    "message": "Settings saved, but folder monitor restart failed",
                    "monitor_restart": monitor_status,
                }

            return {
                "status": "success",
                "monitor_restart": monitor_status,
            }

        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed to save settings")


@settings_router.get("/raw")
async def get_raw_config() -> Dict[str, Any]:
    """Get the raw YAML content for direct editing."""
    from core.config import get_config_path

    path = get_config_path()
    if path.exists():
        return {"content": path.read_text(encoding="utf-8"), "path": str(path)}
    raise HTTPException(status_code=404, detail="Config file not found")


@settings_router.get("/readme")
async def get_readme_file() -> Dict[str, Any]:
    """Get the contents of the readme.txt file included in uploads."""
    from core.config import APP_ROOT

    path = APP_ROOT / "indexers" / "readme" / "readme.txt"
    example_path = path.with_name("readme.example.txt")
    content_path = path if path.exists() else example_path
    content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
    return {"content": content, "path": str(path)}


@settings_router.post("/readme")
async def save_readme_file(req: Dict[str, Any]) -> Dict[str, Any]:
    """Save (or create) the readme.txt file included in uploads."""
    from core.config import APP_ROOT

    content = req.get("content", "")
    if not isinstance(content, str):
        content = ""

    path = APP_ROOT / "indexers" / "readme" / "readme.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"status": "success", "path": str(path)}


@settings_router.post("/preview")
async def get_config_preview(updates: Dict[str, Any]) -> Dict[str, str]:
    """Generate a YAML preview of what the config would look like with these updates."""
    import yaml

    from core.config import get_config_path

    path = get_config_path()
    try:
        # Load current data
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        safe_updates = _merge_masked_secret_updates(updates, get_config())
        for k, v in safe_updates.items():
            data[k] = v

        return {"content": yaml.dump(data, default_flow_style=False, sort_keys=False)}
    except Exception as e:
        return {"content": f"# Error generating preview: {str(e)}"}


@settings_router.post("/raw")
async def save_raw_config(req: Dict[str, Any]) -> Dict[str, Any]:
    """Save raw YAML content."""
    from core.config import replace_config_content

    content = req.get("content")
    if not isinstance(content, str):
        content = ""
    password = req.get("password")

    # If a web_password is configured, enforce it. Otherwise, access is open
    # (relying on the user to secure the port at the network/host level).
    conf = get_config()
    expected = getattr(conf, "web_password", None)
    if expected:
        if password != expected:
            raise HTTPException(status_code=403, detail="Invalid password")

    try:
        new_config = replace_config_content(content)
        await _sync_stats_collector_state(new_config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}") from e


@tests_router.get("/ping")
async def ping() -> Dict[str, Any]:
    """Simple connection test."""
    return {"status": "pong"}


def _runtime_revision() -> Dict[str, Any]:
    revision_path = Path(__file__).resolve().parent / "data" / "deployed_revision.json"
    if revision_path.exists():
        try:
            payload = json.loads(revision_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("revision"):
                return {
                    "revision": str(payload["revision"]),
                    "dirty": bool(payload.get("dirty", False)),
                    "deployed_at": payload.get("deployed_at"),
                }
        except (OSError, ValueError, TypeError):
            pass
    env_revision = str(os.getenv("NZBPOSTARR_REVISION") or "").strip()
    return {"revision": env_revision or "unknown", "dirty": False, "deployed_at": None}


@tests_router.get("/health")
async def health() -> Dict[str, Any]:
    """Basic health check."""
    return {"status": "ok", "time": time.time(), **_runtime_revision()}


@system_router.get("/revision")
async def get_runtime_revision() -> Dict[str, Any]:
    """Return the exact source revision recorded by the deployment workflow."""
    return _runtime_revision()


@system_router.get("/update/status")
async def get_update_status(force: bool = False) -> Dict[str, Any]:
    """Return current updater status and latest known version info."""
    return await asyncio.to_thread(updater.get_update_status, force=force)


@system_router.post("/update/check")
async def check_for_updates_now() -> Dict[str, Any]:
    """Force an immediate GitHub update check."""
    return await asyncio.to_thread(updater.check_for_updates)


@system_router.get("/update/releases")
async def get_update_releases(limit: int = 20) -> Dict[str, Any]:
    """List available GitHub releases/tags for install selection."""
    try:
        releases = await asyncio.to_thread(updater.get_releases, limit=limit)
        return {"releases": releases}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch releases: {exc}") from exc


@system_router.get("/update/backups")
async def get_update_backups(limit: int = 20) -> Dict[str, Any]:
    """List local update snapshots available for rollback."""
    backups = await asyncio.to_thread(updater.list_backups, limit=limit)
    return {"backups": backups}


@system_router.post("/update/install/github")
async def install_update_from_github(req: UpdateInstallRequest) -> Dict[str, Any]:
    """Install the latest (or selected) update directly from GitHub."""
    try:
        return await asyncio.to_thread(
            updater.install_from_github,
            version=req.version,
            restart=req.restart,
        )
    except updater.UpdateError as exc:
        message = str(exc)
        status = 409 if "already in progress" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception(f"GitHub update failed: {exc}")
        raise HTTPException(status_code=500, detail=f"GitHub update failed: {exc}") from exc


@system_router.post("/update/install/upload")
async def install_update_from_upload(
    file: UploadFile = File(...),
    restart: bool = Form(True),
) -> Dict[str, Any]:
    """Install an update from a user-provided ZIP archive."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are supported")

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                tmp.write(chunk)
            tmp_path = Path(tmp.name)

        return await asyncio.to_thread(updater.install_from_uploaded_zip, tmp_path, restart=restart)
    except updater.UpdateError as exc:
        message = str(exc)
        status = 409 if "already in progress" in message.lower() else 400
        raise HTTPException(status_code=status, detail=message) from exc
    except Exception as exc:
        logger.exception(f"Uploaded update failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Uploaded update failed: {exc}") from exc
    finally:
        await file.close()
        if tmp_path:
            tmp_path.unlink(missing_ok=True)


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


# ============================================================
# PENDING QUEUE ROUTES
# ============================================================

pending_router = APIRouter(prefix="/api/pending", tags=["pending"])


class MarkUploadedRequest(BaseModel):
    """API request model for marking items as uploaded."""

    item_keys: List[str]
    indexer_ids: List[str]


class ForceUploadRequest(BaseModel):
    """API request model for force-uploading specific pending items."""

    items: List[Dict[str, str]]  # [{path, category, itype}]
    enable_duplicate_check: bool = True
    test_mode: bool = False
    indexer_id: Optional[str] = None
    force: Optional[bool] = None
    bulk_selection: bool = False


def _log_selected_payload(prefix: str, items: List[Dict[str, Any]]) -> None:
    """Emit concise selection logs for queued/forced uploads."""
    for item in items:
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        name = str(item.get("name") or Path(raw_path).name)
        category = str(item.get("category") or "").strip().lower() or "unknown"
        detected = str(item.get("detected_category") or category or "unknown").strip().lower()
        method = str(item.get("detection_method") or "UI selection").strip()
        reason = str(item.get("selection_reason") or "").strip()
        logger.info(
            f"[{prefix}] Selected '{name}' -> category={category} detected={detected} method={method}"
            f"{f' reason={reason}' if reason else ''}"
        )


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


# ── Pending index state ─────────────────────────────────────
_pending_index = get_pending_index_manager()
_anime_check_inflight: bool = False  # True while Jikan background check is running
_anime_check_lock = threading.Lock()
_anime_check_thread: Optional[threading.Thread] = None
_pending_refresh_lock = threading.Lock()
_anime_check_last_attempt: float = 0.0
_ANIME_CHECK_COOLDOWN_S: float = 3600.0
_boot_reaper_task: Optional[asyncio.Task[Any]] = None


def _pending_watch_folders(conf: Any) -> list[Path]:
    """Return existing configured folders that should invalidate pending index on change."""
    return get_configured_folders(conf, must_exist=True)


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


async def _run_startup_reaper() -> None:
    """Run the initial orphan-process scan without blocking app startup."""
    from logic.process_reaper import reap_orphans

    try:
        result = await asyncio.to_thread(reap_orphans, force=True)
        logger.debug(
            "[startup] Boot reaper scan finished "
            f"(stale={result.get('stale_found', 0)} killed={result.get('killed', 0)} failed={result.get('failed', 0)})"
        )
    except asyncio.CancelledError:
        logger.debug("[startup] Boot reaper scan cancelled")
        raise
    except Exception as exc:
        logger.error(f"[startup] Boot reaper scan failed: {exc}")


def _arm_process_reaper() -> None:
    """Start periodic cleanup immediately and offload the boot scan to the background."""
    global _boot_reaper_task

    from logic.process_reaper import schedule_reaper, schedule_wal_checkpoint

    schedule_reaper()
    schedule_wal_checkpoint()

    if _boot_reaper_task is not None and not _boot_reaper_task.done():
        _boot_reaper_task.cancel()

    _boot_reaper_task = asyncio.create_task(_run_startup_reaper(), name="startup-process-reaper")


async def _stop_startup_reaper() -> None:
    """Cancel or drain the background boot reaper task during shutdown."""
    global _boot_reaper_task

    task = _boot_reaper_task
    _boot_reaper_task = None
    if task is None:
        return

    if not task.done():
        task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def _collect_anime_check_names(data: Dict[str, Any]) -> list[str]:
    return pending_snapshot_mod.collect_anime_check_names(data)


def _collect_uncached_anime_check_names(data: Dict[str, Any]) -> list[str]:
    return pending_snapshot_mod.collect_uncached_anime_check_names(data)


def _classify_video_name(name: str, folder_category: str = "", assume_movie_if_unknown: bool = True) -> str:
    return pending_snapshot_mod.classify_video_name(name, folder_category, assume_movie_if_unknown)


def _detect_external_category(name: str, entry_path: Path) -> str:
    return pending_snapshot_mod.detect_external_category(name, entry_path)


def _detect_content_itype(name: str, entry_path: Path, folder_category: str) -> str:
    return pending_snapshot_mod.detect_content_itype(name, entry_path, folder_category)


def _build_pending_summary(items: Dict[str, Any], indexers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    return _PENDING_BUILD_SUMMARY(items, indexers)


def _scan_pending_all() -> Dict[str, Any]:
    pending_snapshot_mod.get_config = get_config
    pending_snapshot_mod.get_configured_category_folders = get_configured_category_folders
    pending_snapshot_mod.database = database
    return pending_snapshot_mod.scan_pending_snapshot()


def _refresh_pending_snapshot_now(reason: str = "manual") -> Dict[str, Any]:
    """Rebuild the pending snapshot synchronously and replace the cache."""
    if not _pending_refresh_lock.acquire(blocking=False):
        # A refresh is already running; avoid duplicate heavy scans.
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


def _filter_pending(
    data: Dict[str, Any], search: Optional[str], category: str, literal: bool = False
) -> Dict[str, Any]:
    return _PENDING_FILTER(data, search, category, literal)


def _background_anime_check(data: Dict[str, Any]) -> None:
    """Check candidate video titles against Jikan and refresh cache if needed.

    Runs in a daemon thread.  Rate-limit-safe — ``check_titles_batch``
    respects 3/sec and 60/min limits internally.
    """
    global _anime_check_inflight, _anime_check_thread, _anime_check_last_attempt
    from logic.anime_cache import check_titles_batch

    names = _collect_uncached_anime_check_names(data)
    if not names:
        return

    with _anime_check_lock:
        _anime_check_inflight = True
    try:
        # Check if anime checking is enabled in config
        if not getattr(get_config(), "enable_anime_checking", False):
            logger.debug("Anime check: disabled in settings — skipping Jikan lookups")
            return

        logger.debug(f"Anime check: querying Jikan for {len(names)} candidate title(s)")
        results = check_titles_batch(names)

        found = [n for n, v in results.items() if v is True]
        if found:
            logger.info(f"Anime cache: {len(found)} title(s) confirmed as anime — re-scanning pending list")
            try:
                fresh_data = _scan_pending_all()
                _pending_index.set_snapshot(fresh_data)
                logger.debug("Anime check: pending cache updated with anime classifications")
            except Exception as _exc:
                logger.debug(f"Anime check re-scan failed: {_exc}")
        else:
            _anime_check_last_attempt = time.time()
            logger.debug("Anime check: no new anime found among candidate titles — next check in 1h")
    finally:
        with _anime_check_lock:
            _anime_check_inflight = False
            _anime_check_thread = None


@pending_router.get("/summary")
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
            "summary": pending_snapshot_mod.empty_pending_summary(),
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


@pending_router.get("/order")
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


@pending_router.get("/items")
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
        # Non-blocking refresh: on large libraries, synchronous snapshot rebuilds
        # can stall request threads and make the queue UI appear frozen.
        _pending_index.request_refresh(reason="items-refresh")
        state = _pending_index.get_state()
        data = state.get("snapshot")

    if not data:
        _pending_index.request_refresh(reason="items-cold")
        state = _wait_for_pending_snapshot(max_wait_s=2.0, poll_s=0.1)
        data = state.get("snapshot")

    if not data:
        res = _filter_pending({}, search, category, literal)
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
    _anime_cooldown_ok = (time.time() - _anime_check_last_attempt) >= _ANIME_CHECK_COOLDOWN_S
    if anime_enabled and _anime_cooldown_ok and _collect_uncached_anime_check_names(data):
        with _anime_check_lock:
            can_start = not _anime_check_inflight and (_anime_check_thread is None or not _anime_check_thread.is_alive())
            if can_start:
                _anime_check_thread = threading.Thread(target=_background_anime_check, args=(data,), daemon=True, name="pending-anime-check")
                _anime_check_thread.start()

    if not search and category == "all" and not literal:
        res = dict(data)
    else:
        res = _filter_pending(data, search, category, literal)

    # Return only top-level pending rows. Descendants are fetched through the
    # lazy /children route, so copying megabytes of nested data here only to
    # render collapsed rows wastes network, JSON, Vue reactivity, and memory.
    # Build fresh containers so the shared cached snapshot remains untouched.
    def _slim_node(node: Any) -> Any:
        if not isinstance(node, dict):
            return node
        slim = dict(node)
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

    def _slim_items(items: Any) -> Any:
        if not isinstance(items, dict):
            return items
        slim_items: Dict[str, Any] = {}
        for section_name, section in items.items():
            if not isinstance(section, list):
                slim_items[section_name] = section
                continue
            if section_name != "external":
                slim_items[section_name] = [_slim_node(item) for item in section]
                continue
            groups = []
            for group in section:
                if not isinstance(group, dict):
                    groups.append(group)
                    continue
                slim_group = dict(group)
                group_items = group.get("items")
                if isinstance(group_items, list):
                    slim_group["items"] = [_slim_node(item) for item in group_items]
                groups.append(slim_group)
            slim_items[section_name] = groups
        return slim_items

    res["items"] = _slim_items(res.get("items"))
    for private_key in [key for key in res if str(key).startswith("_")]:
        res.pop(private_key, None)

    if refresh or bool(state.get("refreshing", False)):
        res["stale"] = True
    res["ready"] = bool(state.get("ready", True))
    res["refreshing"] = bool(state.get("refreshing", False))
    res["anime_detecting"] = _anime_check_inflight
    return res


@pending_router.get("/children")
def get_pending_children(
    key: str = "",
    path: str = "",
) -> Dict[str, Any]:
    """Fetch children lazily for a specific pending directory node."""
    state = _pending_index.get_state()
    data = state.get("snapshot") or {}
    return pending_snapshot_mod.build_external_children_for_request(data, key, path)


@pending_router.post("/force-upload")
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
                force=req.force,
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


@pending_router.post("/preview-upload")
async def preview_force_upload_items(req: ForceUploadRequest) -> Dict[str, Any]:
    """Preview pending-item eligibility without creating an upload job."""
    selected_items: List[Dict[str, Any]] = list(req.items)
    excluded_count = 0
    excluded_roots: list[str] = []
    if req.bulk_selection:
        conf = get_config()
        excluded_roots = [str(root) for root in _bulk_selection_excluded_roots(conf)]
        selected_items, excluded_count = _filter_bulk_selectable_items(selected_items, conf)

    collapsed_items = _collapse_force_upload_items(selected_items)
    overlap_count = len(selected_items) - len(collapsed_items)
    preview = await _preview_selected_items(
        collapsed_items,
        enable_duplicate_check=req.enable_duplicate_check,
        test_mode=req.test_mode,
        indexer_id=req.indexer_id,
        force=req.force,
    )
    preview["selection"] = {
        "matched": len(req.items),
        "bulk_excluded": excluded_count,
        "overlapping_paths": overlap_count,
        "excluded_roots": excluded_roots,
    }
    return preview


# ============================================================
# FASTAPI APPLICATION SETUP
# ============================================================

# Root directory for web assets (webui/ subfolder relative to this file)
WEBUI_ROOT = Path(__file__).parent / "webui"
ASSETS_DIR = WEBUI_ROOT / "assets"

templates = Jinja2Templates(directory=str(WEBUI_ROOT))
# Change delimiters to avoid conflict with Vue.js {{ }}
templates.env.variable_start_string = "[["
templates.env.variable_end_string = "]]"


# Cache-bust token: event-driven asset stamp with a slow fallback reconcile.
# This avoids scanning the entire JS/CSS tree on normal template renders.
class _AssetCacheBustEventHandler(FileSystemEventHandler):
    def __init__(self, owner: "_DynamicCacheBust") -> None:
        super().__init__()
        self._owner = owner

    def _note(self, raw_path: str) -> None:
        if raw_path:
            self._owner.note_asset_path(Path(raw_path))

    def on_created(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "src_path", ""))

    def on_modified(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "src_path", ""))

    def on_moved(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "dest_path", getattr(event, "src_path", "")))

    def on_deleted(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "src_path", ""))


class _DynamicCacheBust:
    """Tracks a monotonic cache-bust token for frontend assets."""

    _ASSET_SUFFIXES = {".js", ".css"}

    def __init__(self, ttl_seconds: float = 5.0, reconcile_interval_s: float = 300.0) -> None:
        self._ttl_seconds = max(0.5, float(ttl_seconds))
        self._reconcile_interval_s = max(self._ttl_seconds, float(reconcile_interval_s))
        self._cached_value = str(int(time.time()))
        self._cached_at = 0.0
        self._observer: Optional[Any] = None
        self._lock = threading.RLock()

    def _current_token_int(self) -> int:
        try:
            return int(self._cached_value)
        except (TypeError, ValueError):
            return 0

    def _compute_value(self) -> int:
        from itertools import chain as _ic

        latest = 0
        for path in _ic(ASSETS_DIR.rglob("*.js"), ASSETS_DIR.rglob("*.css")):
            if not path.is_file():
                continue
            try:
                latest = max(latest, int(path.stat().st_mtime))
            except OSError:
                continue
        return latest or int(time.time())

    def _refresh_from_scan_locked(self) -> None:
        self._cached_value = str(max(self._compute_value(), self._current_token_int()))
        self._cached_at = time.time()

    def note_asset_path(self, path: Path) -> None:
        if path.suffix.lower() not in self._ASSET_SUFFIXES:
            return
        try:
            stamp = int(path.stat().st_mtime)
        except OSError:
            stamp = int(time.time())
        with self._lock:
            self._cached_value = str(max(stamp, self._current_token_int() + 1, int(time.time())))
            self._cached_at = time.time()

    def start(self) -> None:
        with self._lock:
            if self._observer is not None:
                return
            try:
                self._refresh_from_scan_locked()
            except Exception:
                self._cached_value = str(int(time.time()))
                self._cached_at = time.time()

        try:
            observer, _scheduled = start_watchdog_observer(
                [(_AssetCacheBustEventHandler(self), ASSETS_DIR, True)],
                observer_factory=Observer,
            )
        except Exception as exc:
            logger.debug(f"Asset cache-bust watchdog unavailable: {exc}")
            observer = None
        with self._lock:
            self._observer = observer

    def stop(self) -> None:
        with self._lock:
            observer = self._observer
            self._observer = None
        stop_watchdog_observer(observer)

    def __str__(self) -> str:
        now = time.time()
        with self._lock:
            observer_running = self._observer is not None
            cached_value = self._cached_value
            cached_at = self._cached_at

        refresh_interval = self._reconcile_interval_s if observer_running else self._ttl_seconds
        if (now - cached_at) < refresh_interval:
            return cached_value

        try:
            with self._lock:
                self._refresh_from_scan_locked()
                return self._cached_value
        except Exception:
            with self._lock:
                self._cached_value = str(max(int(now), self._current_token_int()))
                self._cached_at = now
                return self._cached_value


_asset_cache_bust = _DynamicCacheBust()
templates.env.globals["cache_bust"] = _asset_cache_bust
templates.env.globals["auth_enabled"] = lambda: getattr(get_config(), "enable_password", False)

# ── Cookie-based Auth Helpers ──────────────────────────────────────────

_AUTH_COOKIE = "nzbp_auth"
_AUTH_COOKIE_MAX_AGE = 30 * 86400  # 30 days


def _auth_secret() -> str:
    """Derive a signing secret from the current web_password."""
    pw = getattr(get_config(), "web_password", None) or ""
    return hashlib.sha256(f"nzbpostarr-auth:{pw}".encode()).hexdigest()


def _sign_auth_cookie(username: str) -> str:
    ts = str(int(time.time()))
    msg = f"{username}:{ts}".encode()
    sig = hmac.new(_auth_secret().encode(), msg, hashlib.sha256).hexdigest()
    return f"{ts}.{username}.{sig}"


def _verify_auth_cookie(token: str) -> bool:
    try:
        ts_str, username, sig = token.split(".", 2)
        if time.time() - int(ts_str) > _AUTH_COOKIE_MAX_AGE:
            return False
        msg = f"{username}:{ts_str}".encode()
        expected = hmac.new(_auth_secret().encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    start = time.time()
    logger.info("🚀 WebUI Initializing...")

    # Shared init: database, indexer registry, console buffer, tmp cleanup
    from logic.services import init_app

    init_app()
    conf = get_config()
    _asset_cache_bust.start()

    logger.info(f"  DB path: {conf.log_db}")
    logger.debug(f"  [1/2] Core systems ready ({time.time() - start:.3f}s)")

    await _sync_stats_collector_state(conf)
    if _stats_collector_required(conf):
        logger.debug(f"  [2/2] Stats Collector started ({time.time() - start:.3f}s)")
    else:
        logger.debug(f"  [2/2] Stats Collector skipped ({time.time() - start:.3f}s)")

    # Folder Monitor (experimental) — auto-upload on new content
    from logic.folder_monitor import start_folder_monitor

    await start_folder_monitor()
    logger.debug(f"  [3/3] Folder Monitor checked ({time.time() - start:.3f}s)")

    await usenet_stream.start_stream_monitors()
    logger.debug(f"  [3.25/4] Stream Monitor checked ({time.time() - start:.3f}s)")

    # Pending index manager (request-path offload): watcher invalidation + periodic reconcile.
    _pending_index.configure(_scan_pending_all)
    _pending_index.start(_pending_watch_folders(conf))
    logger.debug(f"  [3.5/4] Pending index manager started ({time.time() - start:.3f}s)")

    # Process reaper — periodic cleanup of hung/orphaned tool processes
    _arm_process_reaper()
    logger.debug(f"  [4/4] Process Reaper armed ({time.time() - start:.3f}s)")

    logger.info(f"✨ Startup complete in {time.time() - start:.3f}s")
    yield

    from core.database import checkpoint_wal
    from logic.folder_monitor import stop_folder_monitor
    from logic.process_reaper import shutdown_scheduler
    from logic.stats_engine import stop_collector

    await _stop_startup_reaper()
    _asset_cache_bust.stop()
    _pending_index.stop()
    await usenet_stream.stop_stream_monitors()
    await stop_folder_monitor()
    await stop_collector()
    checkpoint_wal()  # flush WAL before process exits
    shutdown_scheduler()


app = FastAPI(title="NZBPostarr", lifespan=lifespan, docs_url="/swagger", redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def add_cache_control_header(request: Request, call_next: Callable[[Request], Any]) -> Response:
    response: Response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        import re as _re
        # Only use immutable for fingerprinted (content-hashed) assets
        if _re.search(r'\.[a-f0-9]{8,}\.(js|css)$', request.url.path):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # Non-fingerprinted assets (queue.js etc): always revalidate
            response.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/api/"):
        # NEVER cache API responses - critical for live job progress
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    elif request.url.path.endswith((".html", "/")):
        # Don't cache HTML to ensure updates
        response.headers["Cache-Control"] = "no-cache"
    return response


# Paths that skip authentication entirely
_AUTH_PUBLIC_PREFIXES = ("/login", "/assets/", "/favicon", "/robots.txt")
_AUTH_PUBLIC_PATHS = {"/api/system/revision"}


@app.middleware("http")
async def session_auth_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    """Cookie-session auth: redirects unauthenticated browsers to /login, returns 401 for API."""
    path = request.url.path

    # Always allow public paths
    if path in _AUTH_PUBLIC_PATHS or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
        return await call_next(request)

    conf = get_config()
    if getattr(conf, "enable_password", False) and getattr(conf, "web_password", None):
        token = request.cookies.get(_AUTH_COOKIE, "")
        if not _verify_auth_cookie(token):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            next_url = urllib.parse.quote(path, safe="")
            return RedirectResponse(url=f"/login?next={next_url}", status_code=302)

    return await call_next(request)


# Load settings
def get_settings() -> Any:
    return get_config()


# Set an alias for use in launcher
Settings = get_config

# Mount static files
if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

# Include API routes
app.include_router(uploads_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(console_router)
app.include_router(stats_router)
app.include_router(tests_router)
app.include_router(system_router)
app.include_router(indexers_router)
app.include_router(pending_router)


# ── Page Routes ──────────────────────────────────────────────


@app.exception_handler(404)
async def not_found_exception_handler(request: Request, _exc: Exception) -> Response:
    if request.url.path.startswith("/api/"):
        detail = getattr(_exc, "detail", "Not Found")
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "detail": str(detail),
            },
        )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "error_title": "Page Not Found",
            "error_message": "Oops! The page you're looking for doesn't exist.",
            "icon_name": "search-x",
            "icon_bg_class": "bg-notion-accent-muted",
            "icon_color_class": "text-notion-accent",
        },
        status_code=404,
    )


@app.exception_handler(500)
@app.exception_handler(Exception)
async def server_error_exception_handler(request: Request, exc: Exception) -> Response:
    logger.exception(f"Internal Server Error on {request.url.path}: {exc}")

    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "detail": "Internal server error",
            },
        )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "error_title": "Server Error",
            "error_message": "Something went wrong on our end. We've logged the error and are looking into it.",
            "icon_name": "alert-triangle",
            "icon_bg_class": "bg-notion-error/10",
            "icon_color_class": "text-notion-error",
        },
        status_code=500,
    )


@app.get("/robots.txt", response_class=Response)
async def robots_txt() -> Response:
    return Response(content="User-agent: *\nAllow: /\n", media_type="text/plain")


# ── Auth Routes ───────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request) -> Response:
    """Login page — only shown when auth is enabled; otherwise redirects home."""
    if not (getattr(get_config(), "enable_password", False) and getattr(get_config(), "web_password", None)):
        return RedirectResponse(url="/", status_code=302)
    error = request.query_params.get("error", "")
    next_url = request.query_params.get("next", "/")
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": error, "next_url": next_url})


@app.post("/login", response_class=HTMLResponse)
async def post_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next_url: str = Form("/"),
) -> Response:
    """Process login form; set auth cookie on success."""
    conf = get_config()
    expected_username = getattr(conf, "web_username", "admin")
    expected_password = getattr(conf, "web_password", None) or ""

    if hmac.compare_digest(username, expected_username) and hmac.compare_digest(password, expected_password):
        safe_next = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"
        response = RedirectResponse(url=safe_next, status_code=302)
        response.set_cookie(
            _AUTH_COOKIE,
            _sign_auth_cookie(username),
            max_age=_AUTH_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    # Invalid credentials: redirect back to login with error flag
    next_encoded = urllib.parse.quote(next_url, safe="")
    return RedirectResponse(url=f"/login?error=invalid&next={next_encoded}", status_code=302)


@app.get("/logout")
async def logout() -> Response:
    """Clear auth cookie and redirect to /login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(_AUTH_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request) -> HTMLResponse:
    index_path = WEBUI_ROOT / "index.html"
    if index_path.exists():
        return templates.TemplateResponse(request, "index.html", {"request": request})
    return HTMLResponse("index.html not found", status_code=404)


@app.get("/pending", response_class=HTMLResponse)
@app.get("/pending.html", response_class=HTMLResponse)
async def redirect_pending_to_queue(request: Request) -> Response:
    """Redirect legacy pending-page URLs to the unified /queue page."""
    return RedirectResponse(url="/queue", status_code=301)


@app.get("/uploads", response_class=HTMLResponse)
@app.get("/uploads.html", response_class=HTMLResponse)
async def redirect_uploads_to_history(request: Request) -> Response:
    """Redirect legacy uploads-page URLs to /history."""
    return RedirectResponse(url="/history", status_code=301)


@app.get("/{page_name}", response_class=HTMLResponse)
async def get_page(request: Request, page_name: str) -> Response:
    """
    Dynamic page router that maps friendly URLs to .html files.
    Example: /history -> history.html
    """
    # Security: prevent path traversal
    if ".." in page_name or "\\" in page_name:
        raise HTTPException(status_code=404)

    if page_name in {"stats", "stats.html"} and not _stats_page_enabled():
        raise HTTPException(status_code=404, detail="Stats page is disabled")

    # 1. Check for exact file match (e.g., favicon.ico, or history.html directly)
    exact_path = WEBUI_ROOT / page_name
    if exact_path.exists() and exact_path.is_file():
        # If it's an HTML file, use template response, otherwise FileResponse
        if page_name.endswith(".html"):
            return templates.TemplateResponse(request, page_name, {"request": request})
        return FileResponse(exact_path)

    # 2. Check for friendly URL -> .html match (e.g., /history -> history.html)
    html_name = f"{page_name}.html"
    html_path = WEBUI_ROOT / html_name
    if html_path.exists() and html_path.is_file():
        return templates.TemplateResponse(request, html_name, {"request": request})

    raise HTTPException(status_code=404, detail=f"Page '{page_name}' not found")
