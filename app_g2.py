# Auto-split from app.py - verbatim symbol bodies, synthesized imports.

from typing import Iterator

from app_base import (
    Any, BaseModel, Depends, Dict, File, Form, HTTPException, List, Optional, Path, Set, UploadFile, UploadService, VIDEO_EXTENSIONS,
    asyncio, database, get_configured_folders, get_upload_service, json, logger, os, processing, settings_router, system_router,
    tempfile, tests_router, time, updater, uploads_router, usenet_stream,
)
from logic.pending.selection import stamp_upload_itype
from app_g1 import (BulkDeleteRequest, CreateBackupRequest, QueuePriorityRequest, QueueRevalidateRequest, QueueScheduleRequest, RemoveQueuedJobItemRequest, RenameJobRequest, ReorderQueueRequest, ReorderQueuedJobItemsRequest, StartQueueRequest, StreamStartResponse, UpdateInstallRequest, _history_database_error, _mask_config_secrets, _normalize_request_strings, _resolved_policy_path)  # noqa: F401

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

@uploads_router.post("/queue/revalidate")
async def revalidate_queue_jobs(
    req: QueueRevalidateRequest,
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Re-scan queued/paused jobs against the current classification rules."""
    result = service.revalidate_queued_jobs(include_paused=bool(req.include_paused))
    return {"status": "success", **result}

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

@uploads_router.delete("/queue/{job_id}/active-items")
async def remove_active_job_item_route(
    job_id: str,
    req: RemoveQueuedJobItemRequest,
    service: UploadService = Depends(get_upload_service),
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
        detail="Invalid item IDs - must include all current queue items",
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

@uploads_router.post("/item/bulk-delete")
async def bulk_delete_upload_items(req: BulkDeleteRequest) -> Dict[str, Any]:
    """Remove multiple items from the history database."""
    if not req.item_names:
        return {"status": "success", "deleted_count": 0}

    try:
        deleted_count = database.bulk_delete_upload_items(req.item_names)
    except database.DatabaseOperationalError as exc:
        raise _history_database_error(exc) from exc
    return {"status": "success", "deleted_count": deleted_count}

@settings_router.get("/raw")
async def get_raw_config() -> Dict[str, Any]:
    """Get the raw YAML content for direct editing, with secrets masked.

    Credentials are replaced with ``SECRET_MASK`` exactly as the structured
    settings endpoints do. ``save_raw_config`` restores any untouched mask from
    the live config, so a round-trip through the raw editor preserves values the
    operator did not edit.
    """
    import yaml

    from core.config import get_config_path

    path = get_config_path()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Config file not found")

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        # Never fall back to echoing the file verbatim: that is the leak.
        raise HTTPException(status_code=500, detail=f"Config file could not be read: {exc}") from exc

    masked = _mask_config_secrets(data)
    # allow_unicode keeps SECRET_MASK readable as bullets in the editor instead
    # of an escaped "•..." sequence.
    content = yaml.safe_dump(masked, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return {"content": content, "path": str(path), "masked": True}

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

def _create_full_backup_archive(skip_tmp_contents: bool = True) -> Dict[str, Any]:
    """Create a thorough tar.gz backup of the important NZBPostarr state.

    The archive holds .env and the config file, so it is written owner-only (0600).
    """
    import io
    import socket
    import tarfile
    from datetime import datetime

    from core.config import APP_ROOT, get_config

    conf = get_config()
    source_root = APP_ROOT
    backup_root = Path(getattr(conf, "backup_folder", APP_ROOT / "backups")).expanduser()
    backup_root.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_name = f"nzbpostarr_full_backup_{timestamp}.tar.gz"
    archive_path = backup_root / archive_name

    log_db = getattr(conf, "log_db", None)
    important_targets = [
        source_root,
        source_root / ".config" / "nzbpostarr",
        source_root / "data",
        source_root / "anime_cache.json",
        *([Path(log_db)] if log_db else []),
        source_root / ".env",
        Path("/etc/systemd/system/nzbpostarr.service"),
    ]
    excluded_roots = [
        source_root / ".git",
        source_root / ".venv",
        source_root / "venv",
        source_root / "tmp_vt",
        source_root / ".local",
        source_root / "backups",
        backup_root,
    ]
    tmp_root = (source_root / "data" / "tmp").resolve()
    skip_named_dirs: Set[str] = set()
    stateful_tmp_suffixes = {
        ".json", ".yaml", ".yml", ".toml", ".ini", ".txt", ".log", ".db", ".sqlite", ".sqlite3",
    }

    def is_within(path: Path, root: Path) -> bool:
        try:
            path.resolve().relative_to(root.resolve())
            return True
        except (ValueError, OSError):
            return False

    def should_skip(path: Path) -> bool:
        resolved = path.resolve() if path.exists() else path
        if any(part in skip_named_dirs for part in resolved.parts):
            return True
        for prefix in excluded_roots:
            if prefix.exists() and is_within(resolved, prefix):
                return True
        if skip_tmp_contents and is_within(resolved, tmp_root):
            if resolved == tmp_root or path.is_dir():
                return False
            return path.suffix.lower() not in stateful_tmp_suffixes
        return False

    def iter_backup_paths(target: Path) -> Iterator[Path]:
        if not target.exists():
            return
        if target.is_file():
            if not should_skip(target):
                yield target
            return
        yield target
        for path in sorted(target.rglob("*")):
            if should_skip(path):
                continue
            yield path

    def archive_name_for(path: Path) -> str:
        if is_within(path, source_root):
            return str(Path(source_root.name) / path.resolve().relative_to(source_root.resolve()))
        return str(Path("system") / path.relative_to(path.anchor))

    file_count = 0
    added_names: set[str] = set()
    # Create the file owner-only before any secret is written into it.
    fd = os.open(str(archive_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as raw_archive, tarfile.open(fileobj=raw_archive, mode="w:gz") as tar:
        for target in important_targets:
            for path in iter_backup_paths(target):
                arcname = archive_name_for(path)
                if arcname in added_names:
                    continue
                tar.add(path, arcname=arcname, recursive=False)
                added_names.add(arcname)
                file_count += 1

        manifest = {
            "created_at": datetime.now().isoformat(),
            "hostname": socket.gethostname(),
            "source_root": str(source_root),
            "backup_root": str(backup_root),
            "archive_name": archive_name,
            "skip_tmp_contents": skip_tmp_contents,
            "included_targets": [str(path) for path in important_targets if path.exists()],
            "excluded_roots": [str(path) for path in excluded_roots],
            "tmp_stateful_suffixes": sorted(stateful_tmp_suffixes),
        }
        payload = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name=f"{source_root.name}/backup-manifest.json")
        info.size = len(payload)
        info.mtime = int(time.time())
        tar.addfile(info, io.BytesIO(payload))
        file_count += 1
    try:
        os.chmod(archive_path, 0o600)
    except OSError:
        pass

    size_bytes = archive_path.stat().st_size if archive_path.exists() else 0
    return {
        "status": "success",
        "archive_path": str(archive_path),
        "archive_name": archive_name,
        "size_bytes": size_bytes,
        "file_count": file_count,
        "skip_tmp_contents": skip_tmp_contents,
        "backup_root": str(backup_root),
        "included_targets": [str(path) for path in important_targets if path.exists()],
    }

@system_router.post("/backup/create")
async def create_full_backup(req: CreateBackupRequest) -> Dict[str, Any]:
    """Create a full NZBPostarr backup archive in the configured backup folder."""
    try:
        return await asyncio.to_thread(
            _create_full_backup_archive,
            skip_tmp_contents=req.skip_tmp_contents,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create backup: {exc}") from exc

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

def _pending_watch_folders(conf: Any) -> list[Path]:
    """Return existing configured folders that should invalidate pending index on change."""
    return get_configured_folders(conf, must_exist=True)

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
