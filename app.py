"""
📦 NZBPostarr - Web Application
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FastAPI application: API routes and app setup.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from app_base import (
    APIRouter as APIRouter, ASSETS_DIR as ASSETS_DIR, Any as Any, AsyncExitStack as AsyncExitStack, AsyncGenerator as AsyncGenerator,
    BaseModel as BaseModel, Callable as Callable, Depends as Depends, Dict as Dict, FastAPI as FastAPI, Field as Field, File as File,
    FileResponse as FileResponse, FileSystemEventHandler as FileSystemEventHandler, Form as Form, GZipMiddleware as GZipMiddleware,
    HTMLResponse as HTMLResponse, HTTPException as HTTPException, JSONResponse as JSONResponse, Jinja2Templates as Jinja2Templates, List as List,
    Observer as Observer, Optional as Optional, Path as Path, ProcessingJobRequest as ProcessingJobRequest, RedirectResponse as RedirectResponse,
    Request as Request, Response as Response, SECRET_MASK as SECRET_MASK, Set as Set, Settings as Settings, StaticFiles as StaticFiles,
    UploadFile as UploadFile, UploadService as UploadService, VIDEO_EXTENSIONS as VIDEO_EXTENSIONS, WEBUI_ROOT as WEBUI_ROOT,
    _ANIME_CHECK_COOLDOWN_S as _ANIME_CHECK_COOLDOWN_S, _AUTH_COOKIE as _AUTH_COOKIE, _AUTH_COOKIE_MAX_AGE as _AUTH_COOKIE_MAX_AGE,
    _AUTH_PUBLIC_PATHS as _AUTH_PUBLIC_PATHS, _AUTH_PUBLIC_PREFIXES as _AUTH_PUBLIC_PREFIXES, _MCP_ASGI_APP as _MCP_ASGI_APP, _MCP_PATH as _MCP_PATH,
    _PENDING_BUILD_SUMMARY as _PENDING_BUILD_SUMMARY, _PENDING_FILTER as _PENDING_FILTER, _anime_check_inflight as _anime_check_inflight,
    _anime_check_last_attempt as _anime_check_last_attempt, _anime_check_lock as _anime_check_lock, _anime_check_thread as _anime_check_thread,
    _boot_reaper_task as _boot_reaper_task, _pending_index as _pending_index, _pending_refresh_lock as _pending_refresh_lock,
    _shared_dashboard_stats_enabled as _shared_dashboard_stats_enabled, _shared_history_tracking_enabled as _shared_history_tracking_enabled,
    _shared_stats_page_enabled as _shared_stats_page_enabled, asynccontextmanager as asynccontextmanager, asyncio as asyncio, console as console,
    console_router as console_router, copy as copy, dashboard_router as dashboard_router, database as database, get_config as get_config,
    get_configured_category_folders as get_configured_category_folders, get_configured_folders as get_configured_folders,
    get_pending_index_manager as get_pending_index_manager, get_upload_service as get_upload_service, hashlib as hashlib, hmac as hmac,
    indexers_router as indexers_router, json as json, logger as logger, os as os, pending_router as pending_router,
    pending_snapshot_mod as pending_snapshot_mod, processing as processing, re as re, scan_configured_items as scan_configured_items,
    settings_router as settings_router, start_watchdog_observer as start_watchdog_observer, stats_router as stats_router,
    stop_watchdog_observer as stop_watchdog_observer, system_router as system_router, tempfile as tempfile, templates as templates,
    tests_router as tests_router, threading as threading, time as time, updater as updater, uploads_router as uploads_router, urllib as urllib,
    usenet_stream as usenet_stream,
)
from app_g1 import (
    AddQueueItemsRequest as AddQueueItemsRequest, BulkDeleteRequest as BulkDeleteRequest,
    PendingGroupOrderLockedRequest as PendingGroupOrderLockedRequest, PendingGroupOrderRequest as PendingGroupOrderRequest,
    QueuePriorityRequest as QueuePriorityRequest, QueueRevalidateRequest as QueueRevalidateRequest, QueueScheduleRequest as QueueScheduleRequest,
    MuteIssueRequest as MuteIssueRequest, RemoveQueuedJobItemRequest as RemoveQueuedJobItemRequest, RenameJobRequest as RenameJobRequest,
    ReorderQueueRequest as ReorderQueueRequest,
    ReorderQueuedJobItemsRequest as ReorderQueuedJobItemsRequest, RestartRequest as RestartRequest, StartQueueRequest as StartQueueRequest,
    StopAllRequest as StopAllRequest, StreamStartResponse as StreamStartResponse, UpdateInstallRequest as UpdateInstallRequest,
    UpdateRollbackRequest as UpdateRollbackRequest, UploadRequest as UploadRequest, _default_itype_for_category as _default_itype_for_category,
    _mask_config_secrets as _mask_config_secrets, _merge_masked_secret_updates as _merge_masked_secret_updates,
    _normalize_request_strings as _normalize_request_strings, _resolved_policy_path as _resolved_policy_path, browse_folders as browse_folders,
    clear_completed as clear_completed, clear_queue as clear_queue, clear_queue_items as clear_queue_items,
    database_health_check as database_health_check, delete_job_history as delete_job_history, delete_job_route as delete_job_route,
    delete_stream_monitor as delete_stream_monitor, delete_upload_item as delete_upload_item, download_log_file as download_log_file,
    force_start_queue_item as force_start_queue_item, get_active_job_items as get_active_job_items, get_completed_job_items as get_completed_job_items,
    get_grouped as get_grouped, get_grouped_errors as get_grouped_errors, get_grouped_items as get_grouped_items,
    get_history as get_history, get_hourly_stats as get_hourly_stats,
    get_job_uploads as get_job_uploads, get_jobs as get_jobs, get_logs as get_logs, get_queue as get_queue, get_queue_items as get_queue_items,
    get_queued_job_items as get_queued_job_items, get_recent as get_recent, get_stream_monitors as get_stream_monitors, get_summary as get_summary,
    mute_grouped_error as mute_grouped_error, pause_queue_processing as pause_queue_processing, pause_upload as pause_upload,
    promote_queued_job as promote_queued_job,
    reload_indexers_route as reload_indexers_route, remove_queue_item as remove_queue_item, resume_queue_processing as resume_queue_processing,
    resume_upload as resume_upload, retry_upload as retry_upload, stop_clear_queue_processing as stop_clear_queue_processing,
    stop_clear_upload as stop_clear_upload, stop_queue_processing as stop_queue_processing, stop_upload as stop_upload,
    unmute_grouped_error as unmute_grouped_error,
)
from app_g2 import (
    AnimeCacheCorrectionRequest as AnimeCacheCorrectionRequest, ForceUploadRequest as ForceUploadRequest, MarkUploadedRequest as MarkUploadedRequest,
    _build_pending_summary as _build_pending_summary, _bulk_selection_excluded_roots as _bulk_selection_excluded_roots,
    _classify_video_name as _classify_video_name, _collect_anime_check_names as _collect_anime_check_names,
    _collect_uncached_anime_check_names as _collect_uncached_anime_check_names, _detect_content_itype as _detect_content_itype,
    _detect_external_category as _detect_external_category, _filter_pending as _filter_pending,
    _force_upload_dir_direct_video_count as _force_upload_dir_direct_video_count,
    _force_upload_dir_recursive_video_count as _force_upload_dir_recursive_video_count, _log_selected_payload as _log_selected_payload,
    _normalize_force_upload_path as _normalize_force_upload_path, _normalize_selection_path as _normalize_selection_path,
    _normalize_upload_categories as _normalize_upload_categories, _path_is_at_or_below as _path_is_at_or_below,
    _pending_watch_folders as _pending_watch_folders, _preview_selected_items as _preview_selected_items, _run_startup_reaper as _run_startup_reaper,
    _runtime_revision as _runtime_revision, _slim_pending_node as _slim_pending_node, bulk_delete_upload_items as bulk_delete_upload_items,
    check_for_updates_now as check_for_updates_now, get_raw_config as get_raw_config, get_readme_file as get_readme_file,
    get_runtime_revision as get_runtime_revision, get_update_backups as get_update_backups, get_update_releases as get_update_releases,
    get_update_status as get_update_status, health as health, install_update_from_github as install_update_from_github,
    install_update_from_upload as install_update_from_upload, ping as ping, remove_queued_job_item_route as remove_queued_job_item_route,
    rename_upload_job as rename_upload_job, reorder_active_job_items_route as reorder_active_job_items_route, reorder_queue_items as reorder_queue_items,
    reorder_queued_job_items_route as reorder_queued_job_items_route, revalidate_queue_jobs as revalidate_queue_jobs,
    save_readme_file as save_readme_file, set_queued_job_priority as set_queued_job_priority, set_queued_job_schedule as set_queued_job_schedule,
    start_queue as start_queue, start_streamed_nzb_upload as start_streamed_nzb_upload,
)
from app_g3 import (
    _collapse_force_upload_items as _collapse_force_upload_items, _filter_bulk_selectable_items as _filter_bulk_selectable_items,
    _should_preserve_force_upload_dir as _should_preserve_force_upload_dir, _slim_pending_items as _slim_pending_items,
    mark_items_uploaded as mark_items_uploaded, restart_service as restart_service, rollback_update as rollback_update,
    stop_all_service_activity as stop_all_service_activity, update_pending_group_order as update_pending_group_order,
    update_pending_group_order_locked as update_pending_group_order_locked,
)

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

    Reads from the in-memory ring buffer - zero DB hits.
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

@indexers_router.get("")
@indexers_router.get("/")
async def get_all_indexers_route() -> List[Dict[str, Any]]:
    """Retrieve all loaded indexer definitions."""
    from core.registry import get_all_indexers

    conf = get_config()
    return [idx.to_ui_dict(conf) for idx in get_all_indexers()]

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

    # get_raw_config masks credentials, so restore any mask the operator left
    # untouched before writing; otherwise saving would overwrite real secrets
    # with the mask string.
    import yaml

    try:
        parsed = yaml.safe_load(content) or {}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {str(e)}") from e

    if isinstance(parsed, dict):
        restored = _merge_masked_secret_updates(parsed, conf)
        content = yaml.safe_dump(restored, default_flow_style=False, sort_keys=False, allow_unicode=True)

    try:
        new_config = replace_config_content(content)
        await _sync_stats_collector_state(new_config)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save: {str(e)}") from e

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

def _background_anime_check(data: Dict[str, Any]) -> None:
    """Check candidate video titles against Jikan and refresh cache if needed.

    Runs in a daemon thread.  Rate-limit-safe - ``check_titles_batch``
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
            _anime_check_last_attempt = time.time()
            logger.debug("Anime check: no new anime found among candidate titles - next check in 1h")
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

@pending_router.get("/children")
def get_pending_children(
    key: str = "",
    path: str = "",
) -> Dict[str, Any]:
    """Fetch children lazily for a specific pending directory node."""
    state = _pending_index.get_state()
    data = state.get("snapshot") or {}
    return pending_snapshot_mod.build_external_children_for_request(data, key, path)

@pending_router.post("/anime-cache")
def correct_pending_anime_cache(req: AnimeCacheCorrectionRequest) -> Dict[str, Any]:
    """Store a user-confirmed anime verdict and refresh pending classifications."""
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A title name is required")

    from logic.anime_cache import set_cached

    if not set_cached(name, req.is_anime):
        raise HTTPException(status_code=400, detail="The title could not be normalized")

    from logic.pending_scan import classify_video_name

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

    # Folder Monitor (experimental) - auto-upload on new content
    from logic.folder_monitor import start_folder_monitor

    await start_folder_monitor()
    logger.debug(f"  [3/3] Folder Monitor checked ({time.time() - start:.3f}s)")

    await usenet_stream.start_stream_monitors()
    logger.debug(f"  [3.25/4] Stream Monitor checked ({time.time() - start:.3f}s)")

    # Pending index manager (request-path offload): watcher invalidation + periodic reconcile.
    _pending_index.configure(_scan_pending_all)
    _pending_index.start(_pending_watch_folders(conf))
    logger.debug(f"  [3.5/4] Pending index manager started ({time.time() - start:.3f}s)")

    # Process reaper - periodic cleanup of hung/orphaned tool processes
    _arm_process_reaper()
    logger.debug(f"  [4/4] Process Reaper armed ({time.time() - start:.3f}s)")

    logger.info(f"✨ Startup complete in {time.time() - start:.3f}s")

    # A mounted sub-app does not get its lifespan run by the parent, and the MCP
    # streamable-HTTP handler needs its session manager task group started or
    # every request fails with "Task group is not initialized".
    async with AsyncExitStack() as _mcp_stack:
        if _MCP_ASGI_APP is not None:
            await _mcp_stack.enter_async_context(_MCP_ASGI_APP.router.lifespan_context(_MCP_ASGI_APP))
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

def _verify_mcp_token(request: Request) -> bool:
    """Constant-time bearer-token check for the MCP endpoint."""
    expected = str(getattr(get_config(), "mcp_token", "") or "").strip()
    if not expected:
        return False

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(presented.strip(), expected)

@app.middleware("http")
async def session_auth_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    """Cookie-session auth: redirects unauthenticated browsers to /login, returns 401 for API."""
    path = request.url.path

    # Always allow public paths
    if path in _AUTH_PUBLIC_PATHS or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
        return await call_next(request)

    # The MCP endpoint is not a browser and will never carry the session cookie,
    # so it authenticates with its own bearer token instead. It is never simply
    # exempted: no valid token means no access, whether or not web login is on.
    if path == _MCP_PATH or path.startswith(_MCP_PATH + "/"):
        if not _verify_mcp_token(request):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
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

def get_settings() -> Any:
    return get_config()

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

app.include_router(uploads_router)

app.include_router(dashboard_router)

app.include_router(settings_router)

app.include_router(console_router)

app.include_router(stats_router)

app.include_router(tests_router)

app.include_router(system_router)

app.include_router(indexers_router)

app.include_router(pending_router)

def _mount_mcp_endpoint() -> bool:
    """Mount the optional MCP endpoint. No-op unless enabled, tokenized and installed."""
    global _MCP_ASGI_APP
    from logic.mcp_server import MCP_PATH, build_mcp_asgi_app

    try:
        mcp_app = build_mcp_asgi_app(get_config())
    except Exception as exc:  # never let an optional extra break startup
        logger.warning(f"MCP endpoint could not be built: {exc}")
        return False
    if mcp_app is None:
        return False
    app.mount(MCP_PATH, mcp_app)
    _MCP_ASGI_APP = mcp_app
    return True

_MCP_MOUNTED = _mount_mcp_endpoint()

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

@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request) -> Response:
    """Login page - only shown when auth is enabled; otherwise redirects home."""
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

