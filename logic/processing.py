"""
📦 NZBPostarr - Orchestrator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unified module for batch processing and orchestration.
Includes RAR/PAR2 pre-processing and media info extraction.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from logic.processing_base import (
    AUDIOBOOK_EXTENSIONS as AUDIOBOOK_EXTENSIONS, Any as Any, Callable as Callable, Dict as Dict, EBOOK_EXTENSIONS as EBOOK_EXTENSIONS,
    FutureTimeoutError as FutureTimeoutError, Iterator as Iterator, List as List, Lock as Lock, MUSIC_EXTENSIONS as MUSIC_EXTENSIONS,
    Optional as Optional, Path as Path, ThreadPoolExecutor as ThreadPoolExecutor, VIDEO_EXTENSIONS as VIDEO_EXTENSIONS,
    _APP_EXTENSIONS as _APP_EXTENSIONS, _ITEM_VALIDATION_TIMEOUT_SECONDS as _ITEM_VALIDATION_TIMEOUT_SECONDS, _ONE_GIB as _ONE_GIB,
    _tv_pack_episode_rejection_reason as _tv_pack_episode_rejection_reason, as_completed as as_completed, cast as cast,
    compute_size_uncached as compute_size_uncached, copy as copy, dataclass as dataclass, defaultdict as defaultdict,
    extract_percentage as extract_percentage, extract_speed as extract_speed, field as field, find_configured_root as find_configured_root,
    get_config as get_config, get_configured_folders as get_configured_folders, get_thread_job as get_thread_job,
    has_clear_movie_year as has_clear_movie_year, has_multi_file_episode_pattern as has_multi_file_episode_pattern, humanfriendly as humanfriendly,
    log_completed as log_completed, log_info as log_info, log_success as log_success, log_verbose as log_verbose, logger as logger,
    looks_like_tv_name as looks_like_tv_name, mp as mp, normalize_submission_category as normalize_submission_category, os as os,
    pin_folder_ts_to_children as pin_folder_ts_to_children, purge_item_data as purge_item_data, re as re, record_nntp_success as record_nntp_success, resolve_explicit_path as resolve_explicit_path,
    run_command as run_command, scan_configured_items as scan_configured_items, set_thread_job as set_thread_job, should_skip_file as should_skip_file,
    shutil as shutil, stdlib_queue as stdlib_queue, submit_api as submit_api, subprocess as subprocess, update_db_destination as update_db_destination,
    update_job_progress as update_job_progress, upload_item as upload_item, uuid as uuid, wait_for_job_resume as wait_for_job_resume,
)
from logic.processing_g1 import (
    QueueItemValidation as QueueItemValidation, SupportAssetScan as SupportAssetScan, _SingleUploadContext as _SingleUploadContext,
    _SingleUploadState as _SingleUploadState, _already_exists_skip_message as _already_exists_skip_message,
    _append_cleanup_path as _append_cleanup_path, _build_item_key as _build_item_key,
    _build_upload_sets as _build_upload_sets, _classify_preview_validation as _classify_preview_validation,
    _collect_release_keywords as _collect_release_keywords, _descendant_files as _descendant_files, _find_nfo_path as _find_nfo_path,
    _folder_log_itype as _folder_log_itype, _has_enough_temp_space as _has_enough_temp_space, _has_tv_season_pack_name as _has_tv_season_pack_name,
    _is_movie_pack_release as _is_movie_pack_release, _is_tv_pack_release as _is_tv_pack_release,
    _iter_folder_ancestor_entries as _iter_folder_ancestor_entries, _link_or_copy_filtered_file as _link_or_copy_filtered_file,
    _mediainfo_output_path as _mediainfo_output_path, _normalize_processing_category as _normalize_processing_category,
    _normalize_processing_type as _normalize_processing_type, _normalize_runtime_path as _normalize_runtime_path,
    _normalize_runtime_target_paths as _normalize_runtime_target_paths, _persist_runtime_job_checkpoint as _persist_runtime_job_checkpoint,
    _processing_cached_anime_lookup as _processing_cached_anime_lookup, _processing_tool_commands as _processing_tool_commands,
    _resolve_ambiguous_submission_category as _resolve_ambiguous_submission_category, _resolve_targeted_path as _resolve_targeted_path,
    _runtime_checkpoint_path_key as _runtime_checkpoint_path_key, _safe_fs_component as _safe_fs_component, _safe_mtime as _safe_mtime,
    _mediainfo_sidecar_has_escaped_names as _mediainfo_sidecar_has_escaped_names,
    _sanitize_mediainfo_output as _sanitize_mediainfo_output, _scan_release_media as _scan_release_media, _select_upload_server as _select_upload_server,
    _selected_indexers as _selected_indexers, _should_skip_completed_item as _should_skip_completed_item,
    _split_parallel_server_connections as _split_parallel_server_connections, _submission_category_label as _submission_category_label,
    _summarize_preview_details as _summarize_preview_details, _tool_exists as _tool_exists, _upload_target_display as _upload_target_display,
    get_tv_sort_key as get_tv_sort_key,
)
from logic.processing_g2 import (
    _JobExecutionContext as _JobExecutionContext, _JobRunState as _JobRunState, _begin_runtime_item_checkpoint as _begin_runtime_item_checkpoint,
    _build_duplicate_prefetch_state as _build_duplicate_prefetch_state, _build_item_hint_map as _build_item_hint_map,
    _classify_preview_source_item as _classify_preview_source_item, _complete_runtime_item_checkpoint as _complete_runtime_item_checkpoint,
    _create_filtered_tv_pack_staging as _create_filtered_tv_pack_staging, _find_mediainfo_path as _find_mediainfo_path,
    _guard_no_raw_items as _guard_no_raw_items, _has_direct_child_season_pack_dirs as _has_direct_child_season_pack_dirs,
    _inject_inferred_tv_pack_entries as _inject_inferred_tv_pack_entries, _item_exceeds_size_limit as _item_exceeds_size_limit,
    _iter_work_items as _iter_work_items, _log_explicit_resolution as _log_explicit_resolution,
    _looks_like_tv_season_pack_folder as _looks_like_tv_season_pack_folder, _new_prepare_tmp_path as _new_prepare_tmp_path,
    _plan_upload_runs as _plan_upload_runs, _prefetched_validation_state as _prefetched_validation_state,
    _resolve_job_categories as _resolve_job_categories, _resolve_submission_category as _resolve_submission_category,
    _resolve_target_indexers_for_single as _resolve_target_indexers_for_single, _season_pack_dir_for_file as _season_pack_dir_for_file,
    is_season_pack as is_season_pack, kill_child_processes as kill_child_processes,
)
from logic.processing_g3 import (
    _handle_nonready_validation as _handle_nonready_validation, _processing_db_type as _processing_db_type,
)

def _validation_worker_entry(
    result_queue: Any,
    *,
    path_text: str,
    category: str,
    force: bool,
    configured_folder_paths: list[str],
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[list[str]],
    skip_enabled: bool,
    skip_config: Optional[Dict[Any, Any]],
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    prefetched_base_folder_path: Optional[str] = None,
) -> None:
    """Run queue-item validation in an isolated subprocess."""
    try:
        result = _validate_queue_item(
            Path(path_text),
            category=category,
            force=force,
            configured_folders=[Path(folder) for folder in configured_folder_paths],
            target_indexer_id=target_indexer_id,
            target_indexer_ids=list(target_indexer_ids) if target_indexer_ids else None,
            skip_enabled=skip_enabled,
            skip_config=skip_config,
            prefetched_dest_status=prefetched_dest_status,
            prefetched_base_folder=Path(prefetched_base_folder_path) if prefetched_base_folder_path else None,
        )
        result_queue.put(("ok", result))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        result_queue.put(("error", f"Validation crashed for {Path(path_text).name}: {exc}"))

def _scan_item_support_assets(path: Path) -> SupportAssetScan:
    """Scan a release tree once to find the primary NFO and best mediainfo source."""
    if not path.is_dir():
        return SupportAssetScan(nfo_path=_find_nfo_path(path), mediainfo_source_path=path)

    nfo_path: Optional[Path] = None
    mediainfo_source_path: Optional[Path] = None
    largest_video_size = -1

    for root, dirs, files in os.walk(path):
        dirs[:] = [dirname for dirname in dirs if not dirname.startswith(".")]
        for filename in files:
            if filename.startswith("."):
                continue
            candidate = Path(root) / filename
            suffix = candidate.suffix.lower()

            if nfo_path is None and suffix == ".nfo" and "mediainfo" not in candidate.name.lower():
                nfo_path = candidate

            if suffix not in VIDEO_EXTENSIONS:
                continue

            try:
                size = candidate.stat().st_size
            except OSError:
                continue

            if size > largest_video_size:
                largest_video_size = size
                mediainfo_source_path = candidate

    return SupportAssetScan(nfo_path=nfo_path, mediainfo_source_path=mediainfo_source_path)

def _create_filtered_tv_pack_entries_for_selection(
    source_dir: Path,
    allowed_paths: list[Path],
    conf: Any,
    job: Optional[dict[str, Any]],
) -> tuple[list[tuple[Path, list[Path]]], set[str]]:
    """Stage filtered TV packs and return each pack with the episodes it contains."""
    allowed_files: list[Path] = []
    rejected_count = 0
    for path in allowed_paths:
        if not path.is_file():
            continue
        if _tv_pack_episode_rejection_reason(path, VIDEO_EXTENSIONS):
            rejected_count += 1
            continue
        allowed_files.append(path)
    if not source_dir.is_dir() or not allowed_files:
        if rejected_count:
            log_info(
                f"TV/anime pack selection {source_dir.name}: "
                f"{rejected_count} non-S##E##/invalid file(s) ignored"
            )
        return [], set()
    if rejected_count:
        log_info(
            f"TV/anime pack selection {source_dir.name}: "
            f"{rejected_count} non-S##E##/invalid file(s) ignored"
        )

    def pack_is_over_limit(pack_dir: Path) -> bool:
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if not folder_limit or not getattr(conf, "folder_size_limit_enabled", True):
            return False
        size_gb = _live_size_bytes(pack_dir) / _ONE_GIB
        if size_gb <= folder_limit:
            return False
        log_info(
            f"TV/anime pack {pack_dir.name} is {size_gb:.1f} GB and exceeds "
            f"{folder_limit} GB; pack upload skipped, episodes still queued"
        )
        return True

    child_pack_files: dict[Path, list[Path]] = defaultdict(list)
    loose_files: list[Path] = []
    for path in allowed_files:
        pack_dir = _season_pack_dir_for_file(source_dir, path)
        if pack_dir is not None:
            child_pack_files[pack_dir].append(path)
        else:
            loose_files.append(path)

    if not child_pack_files:
        if _has_direct_child_season_pack_dirs(source_dir):
            log_info(
                f"TV parent selection {source_dir.name}: child season folders exist; "
                "parent folder itself will not be uploaded as a pack"
            )
            return [], set()
        if not _has_tv_season_pack_name(source_dir):
            log_info(
                f"TV parent selection {source_dir.name}: "
                f"{len(loose_files) or len(allowed_files)} loose episode file(s) queued as singles"
            )
            return [], set()
        if pack_is_over_limit(source_dir):
            return [], set()
        staged_pack = _create_filtered_tv_pack_staging(source_dir, allowed_paths, conf, job)
        if staged_pack is None:
            return [], set()
        episode_keys = {_normalize_runtime_path(path) for path in allowed_files}
        return [(staged_pack, sorted(allowed_files, key=lambda path: path.name.lower()))], episode_keys

    entries: list[tuple[Path, list[Path]]] = []
    episode_keys: set[str] = set()
    for child_dir in sorted(child_pack_files, key=lambda p: p.name.lower()):
        child_files = sorted(child_pack_files[child_dir], key=lambda path: path.name.lower())
        if pack_is_over_limit(child_dir):
            continue
        staged_pack = _create_filtered_tv_pack_staging(child_dir, child_files, conf, job)
        if staged_pack is not None:
            entries.append((staged_pack, child_files))
            episode_keys.update(_normalize_runtime_path(path) for path in child_files)

    if loose_files:
        log_info(f"TV parent selection {source_dir.name}: {len(loose_files)} loose episode file(s) queued as singles")

    return entries, episode_keys

def generate_mediainfo(
    path: Path,
    *,
    conf: Optional[Any] = None,
    support_scan: Optional[SupportAssetScan] = None,
) -> Optional[Path]:
    """Generate Mediainfo for the item."""
    conf = conf or get_config()

    # Find the largest video file if it's a directory
    target = path
    if path.is_dir():
        scan = support_scan or _scan_item_support_assets(path)
        if not scan.mediainfo_source_path:
            return None
        target = scan.mediainfo_source_path

    info_path = _mediainfo_output_path(path, conf)
    info_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Reprocessing or retrying an unchanged item should not parse the same
        # media stream again. The canonical sidecar is valid while it is
        # non-empty and at least as new as the source selected for inspection.
        if (
            info_path.is_file()
            and info_path.stat().st_size > 0
            and info_path.stat().st_mtime >= target.stat().st_mtime
            and not _mediainfo_sidecar_has_escaped_names(info_path)
        ):
            log_verbose(f"Reusing current Mediainfo for {path.name}")
            return info_path

        # One CLI pass both validates the media and produces the exact text
        # artifact submitted alongside uploads.  PyMediaInfo previously caused
        # the same (potentially very large) source to be analyzed twice.
        cmd = ["mediainfo", "--Full", str(target)]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=120)

        if result.returncode == 0 and result.stdout.strip():
            info_path.write_text(_sanitize_mediainfo_output(result.stdout, target, conf), encoding="utf-8")
            return info_path

    except Exception as e:
        logger.debug(f"Mediainfo generation failed for {target}: {e}")

    return None

def prepare_item(
    path: Path,
    name: str,
    _itype: str,
    *,
    support_scan: Optional[SupportAssetScan] = None,
    total_bytes: Optional[int] = None,
) -> bool:
    """RAR and PAR2 preparation using modern subprocess management."""
    conf = get_config()
    job = get_thread_job()
    tmp = _new_prepare_tmp_path(conf, name, job)
    tmp.mkdir(parents=True, exist_ok=False)

    logger.info(f"Preparing {name}...")
    # Validation already measured every upload item.  Reuse that value instead
    # of walking large release directories a second time.
    total_bytes = int(total_bytes) if total_bytes is not None else compute_size_uncached(path)
    update_job_progress(
        msg=f"Preparing {name}...",
        item_name=name,
        item_percent=0,
        item_size=total_bytes,
        speed="Starting...",
        eta="Starting...",
    )

    if not _has_enough_temp_space(conf, name, total_bytes):
        return False

    # Generate Mediainfo
    generate_mediainfo(path, conf=conf, support_scan=support_scan)

    if conf.test_run or (job and job.get("test_mode")):
        log_info(f"[TEST MODE] Skipping RAR/PAR2 for {name}")
        (tmp / f"{name}.rar").touch()
        return True

    if job:
        job["current_stage"] = "PREPARING"

    from logic.stats_engine import ProgressTracker

    tracker = ProgressTracker(total_bytes)

    def rar_parser(line: str) -> Optional[str]:
        line = line.replace("\x08", "").strip()  # strip_ansi already ran in run_command
        if not line:
            return None

        pct = extract_percentage(line)
        if pct is not None:
            stats = tracker.update(pct)
            update_job_progress(item_percent=int(pct), speed=stats["speed"], eta=stats["eta"])
            return f"RARing: {int(pct)}%"

        if "Creating archive" in line:
            return f"Archive: {Path(line.split()[-1]).name}"

        return None

    def par2_parser(line: str) -> Optional[str]:
        line = line.strip()
        if not line:
            return None

        pct = extract_percentage(line)
        if pct is not None:
            speed = extract_speed(line)
            eta_match = re.search(r"ETA:\s*([\w:]+)", line, re.I)
            eta = eta_match.group(1) if eta_match else None

            if not speed or not eta:
                stats = tracker.update(pct)
                speed = speed or stats["speed"]
                eta = eta or stats["eta"]

            update_job_progress(item_percent=int(pct), speed=speed, eta=eta)
            return f"PAR2ing: {int(pct)}%"
        return None

    try:
        # Step 1: RAR
        rar_cmd = [
            getattr(conf, "rar_path", None) or "rar",
            "a",
            "-y",
            "-o+",
            "-r",
            "-ep1",
            f"-v{conf.rar_size}",
            "-ma5",
            "-m0",
            str(tmp / f"{name}.rar"),
            str(path),
        ]

        success, _ = run_command(rar_cmd, "RAR", job, parser=rar_parser, quiet=True)
        if not success:
            return False

        # Step 2: PAR2
        rar_files = sorted(list(tmp.glob("*.rar")))
        if not rar_files:
            logger.error(f"No RAR files found in {tmp}")
            return False

        tracker = ProgressTracker(total_bytes)

        par_cmd = [
            conf.parpar_path or "parpar",
            "-q",
            "--auto-slice-size",
            "-r10%",
            f"-s{conf.article_size}",
            "-o",
            str(tmp / name),
        ] + [str(f) for f in rar_files]

        success, _ = run_command(par_cmd, "PAR2", job, parser=par2_parser, quiet=True)
        if not success:
            return False

        logger.info(f"Preparation completed for {name}")
        return True

    except Exception as e:
        logger.error(f"Preparation failed for {name}: {e}")
        update_job_progress(msg=f"Prep Error: {e}")
        return False

def check_tools(conf: Optional[Any] = None) -> bool:
    """Verify that required external tools are available."""
    conf = conf or get_config()
    commands = _processing_tool_commands(conf)
    missing = []
    for label, command in commands.items():
        if not _tool_exists(command):
            missing.append(f"{label} ({command})")
    if missing:
        msg = f"Missing required tools: {', '.join(missing)}"
        log_info(msg, "ERROR")
        update_job_progress(msg=msg, status="failed")
        return False
    return True

def _live_size_bytes(path: Path) -> int:
    """Use an uncached size for upload-time guards; pending scans may cache stale growth."""
    return compute_size_uncached(path)

def _folder_size_cached(folder_path: Path) -> int:
    cache_key = _normalize_runtime_path(folder_path)
    job = get_thread_job()
    if job is not None:
        cache = job.setdefault("_folder_size_cache", {})
        if cache_key in cache:
            return int(cache[cache_key])
    # This cache is scoped to the active job.  The former process-wide LRU could
    # return stale sizes for mutable folders and retained path objects forever.
    size = compute_size_uncached(folder_path)
    if job is not None:
        cache = job.setdefault("_folder_size_cache", {})
        cache[cache_key] = int(size)
    return int(size)

def _record_folder_hierarchy_rows(
    path: Path,
    *,
    base_folder: Optional[Path],
    category: str,
    dest_id: Optional[str] = None,
    upload_result: Optional[dict[str, Any]] = None,
) -> None:
    folder_itype = _folder_log_itype(category)
    for folder_key, folder_path in _iter_folder_ancestor_entries(path, base_folder):
        folder_size = _folder_size_cached(folder_path)
        if folder_size <= 0:
            continue
        # Folder rows never jump to "now" on each child upload; they are pinned
        # just above their newest child instead.
        record_nntp_success(folder_key, folder_size, folder_itype, bump_timestamp=False)
        if dest_id and upload_result is not None:
            update_db_destination(
                dest_id,
                folder_path.name,
                folder_size,
                folder_key,
                itype=folder_itype,
                _bump_timestamp=False,
                **upload_result,
            )
        pin_folder_ts_to_children(folder_key)

def _plan_explicit_items(
    raw_items: list[tuple[Path, str]],
    *,
    skip_packs: bool,
    folder_limit: int,
    folder_enabled: bool,
    file_limit: int,
    file_enabled: bool,
) -> tuple[list[tuple[Path, str]], set[str], set[str]]:
    sorted_items: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    oversized_folders: set[str] = set()
    oversized_files: set[str] = set()

    def append_item(path: Path, category: str) -> None:
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            return
        seen_paths.add(normalized)
        sorted_items.append((path, category))
        if file_limit and file_enabled and path.is_file():
            if _live_size_bytes(path) / _ONE_GIB > file_limit:
                oversized_files.add(normalized)

    for path, category in raw_items:
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            continue

        if category in {"tv", "anime"}:
            season_pack = is_season_pack(path)
            if skip_packs and season_pack:
                continue
            if folder_limit and folder_enabled and season_pack:
                if _live_size_bytes(path) / _ONE_GIB > folder_limit:
                    oversized_folders.add(normalized)
                    for child in _descendant_files(path, video_only=True, sorted_names=True):
                        append_item(child, category)
                    continue
            append_item(path, category)
            continue

        if folder_limit and folder_enabled and path.is_dir():
            if _live_size_bytes(path) / _ONE_GIB > folder_limit:
                oversized_folders.add(normalized)
                for child in _descendant_files(path, video_only=False, sorted_names=True):
                    append_item(child, category)
                continue

        append_item(path, category)

    return sorted_items, oversized_folders, oversized_files

def _expand_oversized_category_items(
    cat_items: list[Path],
    *,
    folder_limit: int,
    oversized_folders: set[str],
    season_packs_only: bool,
) -> list[Path]:
    expanded: list[Path] = []
    seen = {_normalize_runtime_path(path) for path in cat_items}
    for path in cat_items:
        if season_packs_only:
            if not is_season_pack(path):
                continue
        elif not path.is_dir():
            continue
        if _live_size_bytes(path) / _ONE_GIB <= folder_limit:
            continue

        oversized_folders.add(_normalize_runtime_path(path))
        for child in _descendant_files(
            path,
            video_only=season_packs_only,
            sorted_names=season_packs_only,
        ):
            child_key = _normalize_runtime_path(child)
            if child_key in seen:
                continue
            expanded.append(child)
            seen.add(child_key)

    if not expanded:
        return cat_items
    retained = [
        path for path in cat_items if _normalize_runtime_path(path) not in oversized_folders
    ]
    return [*retained, *expanded]

def _plan_sorted_items(
    raw_items: list[tuple[Path, str]],
    cats: list[str],
    *,
    preserve_explicit_order: bool,
    skip_packs: bool,
    folder_limit: int,
    folder_enabled: bool,
    file_limit: int,
    file_enabled: bool,
) -> tuple[list[tuple[Path, str]], set[str], set[str]]:
    """Deduplicate, sort, and expand raw items into the concrete processing queue."""
    if preserve_explicit_order:
        return _plan_explicit_items(
            raw_items,
            skip_packs=skip_packs,
            folder_limit=folder_limit,
            folder_enabled=folder_enabled,
            file_limit=file_limit,
            file_enabled=file_enabled,
        )

    by_cat: dict[str, list[Path]] = defaultdict(list)
    seen_paths: set[str] = set()
    for path, cat in raw_items:
        normalized = _normalize_runtime_path(path)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        by_cat[cat].append(path)

    sorted_items: list[tuple[Path, str]] = []
    oversized_folders: set[str] = set()
    oversized_files: set[str] = set()

    for cat in cats:
        if cat not in by_cat:
            continue

        cat_items = list(by_cat[cat])
        if cat in {"tv", "anime"}:
            cat_items.sort(key=get_tv_sort_key)
            if skip_packs:
                cat_items = [path for path in cat_items if not is_season_pack(path)]
            elif folder_limit and folder_enabled:
                cat_items = _expand_oversized_category_items(
                    cat_items,
                    folder_limit=folder_limit,
                    oversized_folders=oversized_folders,
                    season_packs_only=True,
                )
                cat_items.sort(key=get_tv_sort_key)
            log_verbose(f"Sorted TV items: {[path.name for path in cat_items]}")
        else:
            if folder_limit and folder_enabled:
                cat_items = _expand_oversized_category_items(
                    cat_items,
                    folder_limit=folder_limit,
                    oversized_folders=oversized_folders,
                    season_packs_only=False,
                )
            cat_items.sort(key=_safe_mtime, reverse=True)

        if file_limit and file_enabled:
            for path in cat_items:
                if not path.is_file():
                    continue
                file_size = _live_size_bytes(path) / _ONE_GIB
                if file_size > file_limit:
                    oversized_files.add(_normalize_runtime_path(path))

        sorted_items.extend((path, cat) for path in cat_items)

    return sorted_items, oversized_folders, oversized_files

def _validate_queue_item(
    path: Path,
    *,
    category: str,
    force: bool,
    configured_folders: list[Path],
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
    skip_enabled: bool,
    skip_config: Optional[Dict[Any, Any]],
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    prefetched_base_folder: Optional[Path] = None,
) -> QueueItemValidation:
    """Validate one queue item without blocking the rest of the job."""
    from core.database import check_duplicate_dynamic

    conf = get_config()
    name = path.name
    db_type = _processing_db_type(path, category)

    try:
        item_size_bytes = _live_size_bytes(path)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        reason = f"Validation failed for {name}: unable to read size ({exc})"
        logger.exception(reason)
        return QueueItemValidation("failed", path, category, db_type, message=reason)

    item_size_gb = item_size_bytes / _ONE_GIB
    if path.is_dir() and getattr(conf, "folder_size_limit_enabled", True):
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if folder_limit and item_size_gb > folder_limit:
            if category in {"tv", "anime"}:
                reason = (
                    f"⏩ Folder '{name}' ({item_size_gb:.1f} GB) exceeds {folder_limit} GB limit "
                    "- pack skipped, episodes still queued"
                )
            else:
                reason = (
                    f"⏩ Folder '{name}' ({item_size_gb:.1f} GB) exceeds {folder_limit} GB limit "
                    "- folder upload skipped, contents queued"
                )
            return QueueItemValidation("skipped", path, category, db_type, message=reason, item_size_bytes=item_size_bytes)

    if path.is_file() and getattr(conf, "file_size_limit_enabled", True):
        file_limit = getattr(conf, "file_size_limit_gb", 0) or 0
        if file_limit and item_size_gb > file_limit:
            reason = f"⏩ File '{name}' ({item_size_gb:.1f} GB) exceeds {file_limit} GB limit - skipped"
            return QueueItemValidation("skipped", path, category, db_type, message=reason, item_size_bytes=item_size_bytes)

    if skip_enabled and skip_config and should_skip_file(name, category, cast(Dict[Any, Any], skip_config)):
        reason = f"⏩ '{name}' matches skip pattern - skipped"
        return QueueItemValidation("skipped", path, category, db_type, message=reason, item_size_bytes=item_size_bytes)

    source_root = prefetched_base_folder if prefetched_base_folder is not None else find_configured_root(path, configured_folders)
    all_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    if not all_indexers:
        if target_indexer_ids:
            reason = "Selected indexers were not found or are not enabled."
        elif target_indexer_id:
            reason = f"Indexer '{target_indexer_id}' not found or not enabled."
        else:
            reason = "No enabled indexers are available for upload."
        return QueueItemValidation("stopped", path, category, db_type, message=reason, base_folder=source_root)

    indexer_ids = [idx.id for idx in all_indexers]
    if prefetched_dest_status is not None:
        dest_status = {indexer_id: prefetched_dest_status.get(indexer_id) for indexer_id in indexer_ids}
        # Re-verify with the live size-aware check when the prefetch says all done, to avoid
        # basename-only false positives (same episode filename from a different release).
        if item_size_bytes and all(value is not None for value in dest_status.values()):
            live_key = _build_item_key(path, source_root)
            try:
                dest_status = check_duplicate_dynamic(live_key, db_type, indexer_ids, filesize=item_size_bytes)
            except Exception:  # pylint: disable=broad-exception-caught
                pass  # keep the prefetched result on error
    else:
        key = _build_item_key(path, source_root)
        try:
            dest_status = check_duplicate_dynamic(key, db_type, indexer_ids, filesize=item_size_bytes)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            reason = f"Validation failed for {name}: duplicate check error ({exc})"
            logger.exception(reason)
            return QueueItemValidation("failed", path, category, db_type, message=reason, base_folder=source_root)

    if _should_skip_completed_item(all_indexers, conf, dest_status, force=force, name=name):
        reason = _already_exists_skip_message(path, source_root, item_size_bytes)
        return QueueItemValidation(
            "skipped",
            path,
            category,
            db_type,
            message=reason,
            base_folder=source_root,
            item_size_bytes=item_size_bytes,
            prefetched_dest_status=dest_status,
        )

    try:
        submission_category = _resolve_submission_category(path, category, db_type)
    except ValueError as exc:
        reason = f"Validation failed for {name}: category detection failed ({exc})"
        logger.error(reason)
        return QueueItemValidation(
            "failed",
            path,
            category,
            db_type,
            message=reason,
            base_folder=source_root,
            item_size_bytes=item_size_bytes,
            prefetched_dest_status=dest_status,
        )

    return QueueItemValidation(
        "ready",
        path,
        category,
        db_type,
        base_folder=source_root,
        item_size_bytes=item_size_bytes,
        prefetched_dest_status=dest_status,
        submission_category=submission_category,
    )

def preview_processing_items(
    items: List[Dict[str, Any]],
    *,
    target_indexer_id: Optional[str] = None,
    target_indexer_ids: Optional[List[str]] = None,
    enable_duplicate_check: bool = True,
    force: Optional[bool] = None,
    test_mode: bool = False,
    skip_packs: bool = False,
    skip_episodes: bool = False,
    detail_limit: int = 500,
) -> Dict[str, Any]:
    """Plan and validate explicit items without creating a job or staging data."""
    from logic.services import UploadService

    conf = get_config()
    configured_folders = get_configured_folders(conf, must_exist=True)
    resolved_force = UploadService._resolve_force_flag(
        enable_duplicate_check=enable_duplicate_check,
        force=force,
        test_mode=test_mode,
    )
    selected_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    selected_indexer_ids = [str(indexer.id) for indexer in selected_indexers]

    details: List[Dict[str, Any]] = []
    raw_items: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    input_count = len(items)

    def append_detail(
        *,
        path: str,
        name: str,
        category: str,
        outcome: str,
        reason: str,
        size_bytes: int = 0,
        destinations: Optional[Dict[str, Any]] = None,
    ) -> None:
        details.append(
            {
                "path": path,
                "name": name,
                "category": category,
                "outcome": outcome,
                "reason": reason,
                "size_bytes": int(size_bytes or 0),
                "destinations": destinations or {},
            }
        )

    for item in items:
        path, display_path, name, category, reject_reason, outcome = _classify_preview_source_item(
            item, seen_paths
        )
        if reject_reason:
            append_detail(path=display_path, name=name, category=category, outcome=outcome, reason=reject_reason)
            continue
        normalized = _normalize_runtime_path(path)
        seen_paths.add(normalized)
        raw_items.append((path, category))

    process_tv_episodes = bool(getattr(conf, "process_tv_episodes", True)) and not skip_episodes
    if not process_tv_episodes:
        retained: list[tuple[Path, str]] = []
        for path, category in raw_items:
            if category in {"tv", "anime"} and path.is_file():
                append_detail(
                    path=str(path),
                    name=path.name,
                    category=category,
                    outcome="excluded",
                    reason="Individual episode processing is disabled",
                )
            else:
                retained.append((path, category))
        raw_items = retained

    categories = list(dict.fromkeys(category for _path, category in raw_items))
    sorted_items, _oversized_folders, oversized_files = _plan_sorted_items(
        raw_items,
        categories,
        preserve_explicit_order=True,
        skip_packs=skip_packs,
        folder_limit=int(getattr(conf, "folder_size_limit_gb", 99) or 0),
        folder_enabled=bool(getattr(conf, "folder_size_limit_enabled", True)),
        file_limit=int(getattr(conf, "file_size_limit_gb", 0) or 0),
        file_enabled=bool(getattr(conf, "file_size_limit_enabled", True)),
    )

    planned_keys = {_normalize_runtime_path(path) for path, _category in sorted_items}
    for path, category in raw_items:
        normalized = _normalize_runtime_path(path)
        if normalized not in planned_keys:
            reason = "Pack processing is disabled" if skip_packs and category in {"tv", "anime"} else "Excluded by processing rules"
            append_detail(
                path=str(path),
                name=path.name,
                category=category,
                outcome="excluded",
                reason=reason,
            )

    prefetched_item_db_keys: Dict[str, str] = {}
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]] = None
    if sorted_items and selected_indexer_ids:
        (
            prefetched_item_db_keys,
            prefetched_dupes,
            prefetched_source_root_for,
        ) = _build_duplicate_prefetch_state(
            conf,
            sorted_items,
            configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
        )

    skip_config = getattr(conf, "skip_files", None)
    skip_enabled = isinstance(skip_config, dict) and bool(skip_config.get("enabled", False))

    for path, category in sorted_items:
        normalized = _normalize_runtime_path(path)
        item_db_key = prefetched_item_db_keys.get(normalized)
        dest_status = prefetched_dupes.get(item_db_key) if item_db_key else None
        base_folder = prefetched_source_root_for(path) if prefetched_source_root_for is not None else None
        validation = _validate_queue_item(
            path,
            category=category,
            force=resolved_force,
            configured_folders=configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
            skip_enabled=skip_enabled,
            skip_config=cast(Optional[Dict[Any, Any]], skip_config),
            prefetched_dest_status=dest_status,
            prefetched_base_folder=base_folder,
        )
        outcome, reason, destination_status = _classify_preview_validation(
            validation,
            normalized=normalized,
            selected_indexer_ids=selected_indexer_ids,
            resolved_force=resolved_force,
            oversized_files=oversized_files,
        )
        append_detail(
            path=str(path),
            name=path.name,
            category=category,
            outcome=outcome,
            reason=reason,
            size_bytes=validation.item_size_bytes,
            destinations=destination_status,
        )

    destination_summary, outcome_counts, ready_bytes, total_bytes, partial_duplicates = (
        _summarize_preview_details(details, selected_indexer_ids)
    )

    return {
        "status": "preview",
        "summary": {
            "selected": input_count,
            "planned": len(sorted_items),
            **outcome_counts,
            "partial_duplicates": partial_duplicates,
            "total_bytes": total_bytes,
            "ready_bytes": ready_bytes,
        },
        "destinations": destination_summary,
        "items": details[: max(1, int(detail_limit))],
        "details_truncated": len(details) > max(1, int(detail_limit)),
        "duplicate_check_bypassed": resolved_force,
    }

def _run_validation_with_timeout(
    path: Path,
    *,
    category: str,
    force: bool,
    configured_folders: list[Path],
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
    skip_enabled: bool,
    skip_config: Optional[Dict[Any, Any]],
    timeout_s: float = _ITEM_VALIDATION_TIMEOUT_SECONDS,
    runtime_job: Optional[dict[str, Any]] = None,
    validation_worker: Optional[Callable[..., QueueItemValidation]] = None,
    isolate_with_process: Optional[bool] = None,
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    prefetched_base_folder: Optional[Path] = None,
) -> QueueItemValidation:
    """Run item validation with per-item isolation and a timeout guard."""

    if isolate_with_process is None:
        env_flag = os.getenv("NZBPOSTARR_VALIDATE_ISOLATE", "").strip().lower()
        if env_flag:
            isolate_with_process = env_flag not in {"0", "false", "no", "off"}
        else:
            # A timed-out Python thread cannot be killed and may continue
            # mutating job or filesystem state after failure is reported.
            # Production validation therefore runs in a process that can be
            # terminated. Tests and explicit injected workers retain the
            # lightweight thread path.
            isolate_with_process = validation_worker is None

    def runner() -> QueueItemValidation:
        if runtime_job is not None:
            set_thread_job(runtime_job)
        worker = validation_worker or _validate_queue_item
        return worker(
            path,
            category=category,
            force=force,
            configured_folders=configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
            skip_enabled=skip_enabled,
            skip_config=skip_config,
            prefetched_dest_status=prefetched_dest_status,
            prefetched_base_folder=prefetched_base_folder,
        )

    if isolate_with_process:
        result_queue = None
        process = None
        try:
            ctx = mp.get_context("spawn")
            result_queue = ctx.Queue(maxsize=1)
            process = ctx.Process(
                target=_validation_worker_entry,
                kwargs={
                    "result_queue": result_queue,
                    "path_text": str(path),
                    "category": category,
                    "force": force,
                    "configured_folder_paths": [str(folder) for folder in configured_folders],
                    "target_indexer_id": target_indexer_id,
                    "target_indexer_ids": list(target_indexer_ids) if target_indexer_ids else None,
                    "skip_enabled": skip_enabled,
                    "skip_config": skip_config,
                    "prefetched_dest_status": prefetched_dest_status,
                    "prefetched_base_folder_path": str(prefetched_base_folder) if prefetched_base_folder else None,
                },
                daemon=True,
            )
            process.start()
            process.join(timeout_s)
            if process.is_alive():
                reason = f"Validation timed out after {int(timeout_s)}s for {path.name}"
                logger.error(reason)
                process.terminate()
                process.join(timeout=5.0)
                if process.is_alive() and hasattr(process, "kill"):
                    process.kill()
                    process.join(timeout=1.0)
                return QueueItemValidation(
                    "failed",
                    path,
                    category,
                    _processing_db_type(path, category),
                    message=reason,
                )

            try:
                status, payload = result_queue.get_nowait()
            except stdlib_queue.Empty:
                reason = (
                    f"Validation worker exited without returning a result for {path.name}"
                    if process.exitcode == 0
                    else f"Validation worker exited with code {process.exitcode} for {path.name}"
                )
                logger.error(reason)
                return QueueItemValidation(
                    "failed",
                    path,
                    category,
                    _processing_db_type(path, category),
                    message=reason,
                )

            if status == "ok":
                return cast(QueueItemValidation, payload)

            reason = str(payload or f"Validation crashed for {path.name}")
            logger.error(reason)
            return QueueItemValidation(
                "failed",
                path,
                category,
                _processing_db_type(path, category),
                message=reason,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            reason = f"Validation crashed for {path.name}: {exc}"
            logger.exception(reason)
            return QueueItemValidation(
                "failed",
                path,
                category,
                _processing_db_type(path, category),
                message=reason,
            )
        finally:
            if result_queue is not None:
                try:
                    result_queue.close()
                    result_queue.join_thread()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
            if process is not None and process.is_alive():
                try:
                    process.terminate()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(runner)
    try:
        return future.result(timeout=timeout_s)
    except FutureTimeoutError:
        future.cancel()
        reason = f"Validation timed out after {int(timeout_s)}s for {path.name}"
        logger.error(reason)
        return QueueItemValidation(
            "failed",
            path,
            category,
            _processing_db_type(path, category),
            message=reason,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        reason = f"Validation crashed for {path.name}: {exc}"
        logger.exception(reason)
        return QueueItemValidation(
            "failed",
            path,
            category,
            _processing_db_type(path, category),
            message=reason,
        )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

def _run_validated_upload(
    validation: QueueItemValidation,
    *,
    force: bool,
    test_mode: bool,
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
    runtime_job: Optional[dict[str, Any]] = None,
) -> int:
    """Upload a validated item in the background worker."""
    if runtime_job is not None:
        set_thread_job(runtime_job)
    return process_single(
        validation.path,
        itype=validation.db_type,
        category=validation.category,
        force=force,
        base_folder=validation.base_folder,
        test_mode=test_mode,
        target_indexer_id=target_indexer_id,
        target_indexer_ids=target_indexer_ids,
        prefetched_dest_status=validation.prefetched_dest_status,
        validated_item_size_bytes=validation.item_size_bytes,
        validated_submission_category=validation.submission_category,
    )

def _resolve_support_assets(
    path: Path,
    conf: Any,
    name: str,
    *,
    support_scan: Optional[SupportAssetScan] = None,
) -> tuple[Optional[Path], Optional[Path]]:
    """Resolve optional sidecar assets submitted alongside the NZB."""
    nfo_path = support_scan.nfo_path if support_scan is not None else _find_nfo_path(path)
    if nfo_path:
        log_verbose(f"Found NFO for {name}: {nfo_path.name}")

    mediainfo_path = _find_mediainfo_path(path, conf)
    if mediainfo_path:
        log_verbose(f"Found Mediainfo for {name}: {mediainfo_path.name}")

    return nfo_path, mediainfo_path

def _submit_api_batch(
    upload_set: dict[str, Any],
    *,
    conf: Any,
    name: str,
    priority_label: str,
    unique_nzb: Path,
    submission_category: str,
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
) -> list[tuple[str, bool, str, str]]:
    """Submit an uploaded NZB to one or more indexers."""

    def submit_one(dest_id: str) -> tuple[str, bool, str, str]:
        result = submit_api(
            f"{priority_label}{name}",
            dest_id,
            conf,
            nzb_path=unique_nzb,
            cat=submission_category,
            nfo_path=nfo_path,
            mediainfo_path=mediainfo_path,
        )
        return dest_id, result.success, result.reason, result.status

    dests = upload_set["dests"]
    if len(dests) == 1:
        try:
            return [submit_one(dests[0])]
        except Exception as exc:
            reason = f"Unhandled submission exception for indexer '{dests[0]}': {exc}"
            logger.exception(reason)
            return [(dests[0], False, reason, "error")]

    results: dict[str, tuple[str, bool, str, str]] = {}
    with ThreadPoolExecutor(max_workers=len(dests)) as api_pool:
        futures = {api_pool.submit(submit_one, dest_id): dest_id for dest_id in dests}
        for future in as_completed(futures):
            dest_id = futures[future]
            try:
                results[dest_id] = future.result()
            except Exception as exc:
                reason = f"Unhandled submission exception for indexer '{dest_id}': {exc}"
                logger.exception(reason)
                results[dest_id] = (dest_id, False, reason, "error")
    return [results[dest_id] for dest_id in dests]

def _persist_submission_results(
    api_results: list[tuple[str, bool, str, str]],
    *,
    name: str,
    item_size: int,
    key: str,
    itype: str,
    item_path: Path,
    base_folder: Optional[Path],
    category: str,
    test_mode: bool,
    upload_result: dict[str, Any],
) -> bool:
    """Persist per-indexer submission results and return whether any succeeded."""
    any_success = False
    for dest_id, ok, reason, sub_status in api_results:
        if ok:
            logger.info(f"[INDEXER] {dest_id} accepted '{name}'")
            any_success = True
            if not test_mode and item_size > 0:
                update_db_destination(dest_id, name, item_size, key, itype=itype, **upload_result)
                _record_folder_hierarchy_rows(
                    item_path,
                    base_folder=base_folder,
                    category=category,
                    dest_id=dest_id,
                    upload_result=upload_result,
                )
            continue

        logger.error(f"[INDEXER] {dest_id} failed '{name}': {reason or 'unknown error'}")
        if sub_status == "duplicate":
            logger.info(f"[INDEXER] {dest_id} duplicate treated as already-posted for '{name}'")
            any_success = True
            if not test_mode and item_size > 0:
                update_db_destination(dest_id, name, item_size, key, itype=itype, **upload_result)
            continue
        if not test_mode and item_size > 0:
            update_db_destination(
                dest_id,
                name,
                item_size,
                key,
                itype=itype,
                status="failed",
                error=reason or "Indexer submission rejected or unreachable",
            )
    if any_success and not test_mode:
        # Pending folder expansion caches indexer ticks; drop them so the new
        # upload shows at once instead of after the cache TTL.
        from logic.pending_snapshot import invalidate_pending_indexer_context

        invalidate_pending_indexer_context()
    return any_success

def _run_single_upload_flow(
    upload_set: dict[str, Any],
    upload_server: Any,
    *,
    context: _SingleUploadContext,
    state: _SingleUploadState,
) -> bool:
    """Run and isolate one posting/indexer destination flow."""
    if context.job:
        set_thread_job(context.job)

    # A single upload_set may cover several indexer priority groups that share
    # this server (see _plan_upload_runs). Only ONE physical NNTP upload happens
    # below; submission_groups gives each priority group its own API submission
    # (and its own "Priority " name prefix) against that same posted NZB.
    submission_groups = upload_set.get("submission_groups") or [
        {"dests": upload_set["dests"], "priority": upload_set.get("priority", False)}
    ]
    for group in submission_groups:
        group_display = _upload_target_display({"id": "/".join(group["dests"]), "priority": group["priority"]})
        log_info(
            f"--- [UPLOAD] Targeting: {group_display} "
            f"({upload_server.name} @ {upload_server.max_connections} conn) ---"
        )

    set_id_safe = upload_set["id"].replace("/", "_").replace(" ", "_")
    unique_nzb = context.conf.get_nzb_path(
        f"{context.name}.{set_id_safe}.{upload_server.name}"
    )
    try:
        prepared_dir = (
            Path(str(context.job.get("_current_prepare_tmp")))
            if context.job and context.job.get("_current_prepare_tmp")
            else None
        )
        upload_result = upload_item(
            context.name,
            upload_server,
            nzb_path=unique_nzb,
            progress_key=upload_set["id"],
            prepared_dir=prepared_dir,
        )
        if not upload_result:
            return False

        if not context.test_mode and context.item_size > 0:
            record_nntp_success(context.key, context.item_size, context.itype)
            _record_folder_hierarchy_rows(
                context.path,
                base_folder=context.base_folder,
                category=context.category,
            )

        api_results: list[tuple[str, bool, str, str]] = []
        for group in submission_groups:
            group_set = {"id": "/".join(group["dests"]), "dests": group["dests"], "priority": group["priority"]}
            api_results.extend(
                _submit_api_batch(
                    group_set,
                    conf=context.conf,
                    name=context.name,
                    priority_label="Priority " if group["priority"] else "",
                    unique_nzb=unique_nzb,
                    submission_category=context.submission_category,
                    nfo_path=context.nfo_path,
                    mediainfo_path=context.mediainfo_path,
                )
            )
        run_success = _persist_submission_results(
            api_results,
            name=context.name,
            item_size=context.item_size,
            key=context.key,
            itype=context.itype,
            item_path=context.path,
            base_folder=context.base_folder,
            category=context.category,
            test_mode=context.test_mode,
            upload_result=upload_result,
        )

        with state.lock:
            state.any_success = run_success or state.any_success
            if context.job:
                context.job["total_bytes"] = context.job.get("total_bytes", 0) + context.item_size
        return run_success
    except Exception as exc:
        logger.exception(
            f"Upload target '{upload_set['id']}' failed for {context.name}: {exc}"
        )
        with state.lock:
            state.errors.append(f"{upload_set['id']}: {exc}")
        return False
    finally:
        if unique_nzb.exists():
            try:
                unique_nzb.unlink()
            except OSError:
                pass

def _prepare_and_upload_single(
    *,
    path: Path,
    name: str,
    itype: str,
    category: str,
    base_folder: Optional[Path],
    test_mode: bool,
    item_size: int,
    item_size_bytes: int,
    conf: Any,
    job: Optional[Dict[str, Any]],
    submission_category: str,
    key: str,
    sets_to_run: Any,
    upload_state: "_SingleUploadState",
    support_scan: Any,
) -> int:
    """Prepare the item on disk and run its upload flow across sets_to_run.
    Extracted from process_single to keep that function's own branching down;
    return codes match what process_single previously returned inline."""
    if not prepare_item(path, name, itype, support_scan=support_scan, total_bytes=item_size_bytes):
        if job and job.get("stop_requested"):
            return 2
        return 3

    nfo_path, mediainfo_path = _resolve_support_assets(path, conf, name, support_scan=support_scan)
    if job and not wait_for_job_resume(job):
        return 2
    if job and job.get("stop_requested"):
        return 2
    upload_context = _SingleUploadContext(
        conf=conf,
        name=name,
        path=path,
        category=category,
        itype=itype,
        base_folder=base_folder,
        test_mode=test_mode,
        item_size=item_size,
        job=job,
        submission_category=submission_category,
        nfo_path=nfo_path,
        mediainfo_path=mediainfo_path,
        key=key,
    )
    with ThreadPoolExecutor(max_workers=len(sets_to_run)) as executor:
        tasks = [
            executor.submit(
                _run_single_upload_flow,
                upload_set,
                upload_server,
                context=upload_context,
                state=upload_state,
            )
            for upload_set, upload_server in sets_to_run
        ]
        for task in tasks:
            task.result()
    if upload_state.errors and not upload_state.any_success:
        log_info(
            f"All upload targets failed for {name}: {'; '.join(upload_state.errors[:3])}"
            f"{' ...' if len(upload_state.errors) > 3 else ''}",
            "ERROR",
        )
    if job and not wait_for_job_resume(job):
        return 2
    if job and job.get("stop_requested"):
        return 2
    return 0 if upload_state.any_success else 4  # 4=upload ok, no indexer accepted

def process_single(
    path: Path,
    force: bool = False,
    itype: str = "Misc",
    category: str = "misc",
    base_folder: Optional[Path] = None,
    test_mode: bool = False,
    target_indexer_id: Optional[str] = None,
    target_indexer_ids: Optional[List[str]] = None,
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None,
    validated_item_size_bytes: Optional[int] = None,
    validated_submission_category: Optional[str] = None,
) -> int:
    """Process a single item (orchestrated for dual uploads)."""
    from core.database import check_duplicate_dynamic

    conf = get_config()
    name = path.name
    job = get_thread_job()

    if job and not wait_for_job_resume(job):
        return 2

    # ── Size-limit guard (applies to manually queued items too) ──
    item_size_bytes = validated_item_size_bytes if validated_item_size_bytes is not None else _live_size_bytes(path)
    item_size_gb = item_size_bytes / _ONE_GIB

    if _item_exceeds_size_limit(path, conf, item_size_gb, name):
        return 1  # treat as skip

    all_indexers = _resolve_target_indexers_for_single(conf, target_indexer_id, target_indexer_ids)
    if all_indexers is None:
        return 2

    indexer_ids = [idx.id for idx in all_indexers]
    indexer_map = {idx.id: idx for idx in all_indexers}

    key = _build_item_key(path, base_folder)

    if prefetched_dest_status is None:
        dest_status = check_duplicate_dynamic(key, itype, indexer_ids, filesize=item_size_bytes)
    else:
        dest_status = {idx: prefetched_dest_status.get(idx) for idx in indexer_ids}
    is_new = all(v is None for v in dest_status.values())
    global_backfill = conf.enable_backfill

    if _should_skip_completed_item(all_indexers, conf, dest_status, force=force, name=name):
        return 1

    if job:
        job["item_percents"] = {}
        job["item_speeds"] = {}
        job["item_percent"] = 0
        job["current_item"] = name

    all_servers = [s for s in (conf.nntp_servers or []) if getattr(s, "enabled", True)]
    if not all_servers:
        log_info("No configured servers found, skipping upload.", "ERROR")
        return 2

    item_size = item_size_bytes
    raw_sets = _plan_upload_runs(
        _build_upload_sets(all_indexers, conf),
        all_servers=all_servers,
        dest_status=dest_status,
        indexer_map=indexer_map,
        force=force,
        is_new=is_new,
        global_backfill=global_backfill,
    )

    if not raw_sets:
        reason = (
            "no enabled indexers with valid API keys"
            if not all_indexers
            else "all destinations already handled (and force=False)"
        )
        log_info(f"Skipping: {name} - {reason}.")
        return 1

    sets_to_run = _split_parallel_server_connections(raw_sets)

    upload_state = _SingleUploadState()
    try:
        submission_category = validated_submission_category or _resolve_submission_category(path, category, itype)
    except ValueError as exc:
        logger.error(f"Category detection failed for {name}: category={category} type={itype} error={exc}")
        log_info("Category detection failed - upload stopped", "ERROR")
        return 3
    log_info(f"Detected Category: {_submission_category_label(submission_category)}")
    support_scan = _scan_item_support_assets(path) if path.is_dir() else None

    try:
        return _prepare_and_upload_single(
            path=path,
            name=name,
            itype=itype,
            category=category,
            base_folder=base_folder,
            test_mode=test_mode,
            item_size=item_size,
            item_size_bytes=item_size_bytes,
            conf=conf,
            job=job,
            submission_category=submission_category,
            key=key,
            sets_to_run=sets_to_run,
            upload_state=upload_state,
            support_scan=support_scan,
        )
    finally:
        prepared_tmp = None
        if job is not None:
            prepared_tmp = job.pop("_current_prepare_tmp", None)
        standard_tmp = conf.tmp_sub / name
        if prepared_tmp and Path(str(prepared_tmp)) != standard_tmp:
            shutil.rmtree(Path(str(prepared_tmp)), ignore_errors=True)
        purge_item_data(name)

def _append_targeted_resolution_items(
    raw_items: list[tuple[Path, str]],
    *,
    selected_path: Path,
    resolution: Any,
    conf: Any,
    runtime_job: Optional[dict[str, Any]],
    process_tv_episodes: bool,
    staged_pack_source_dirs: set[str],
) -> None:
    resolved_paths = list(resolution.queue_paths)
    episode_paths_added_with_packs: set[str] = set()
    skip_parent_series_folder = False

    if (
        resolution.category in {"tv", "anime"}
        and selected_path.is_dir()
        and resolved_paths
        and any(resolved_path.is_file() for resolved_path in resolved_paths)
    ):
        pack_entries, episode_paths_added_with_packs = _create_filtered_tv_pack_entries_for_selection(
            selected_path,
            resolved_paths,
            conf,
            runtime_job,
        )
        skip_parent_series_folder = bool(pack_entries)
        if pack_entries:
            staged_pack_source_dirs.add(_normalize_runtime_path(selected_path))
        for staged_pack, pack_episode_paths in pack_entries:
            raw_items.append((staged_pack, resolution.category))
            if process_tv_episodes:
                raw_items.extend((episode_path, resolution.category) for episode_path in pack_episode_paths)

    selected_key = _normalize_runtime_path(selected_path)
    for resolved_path in resolved_paths:
        if (
            skip_parent_series_folder
            and resolved_path.is_dir()
            and _normalize_runtime_path(resolved_path) == selected_key
        ):
            continue
        if resolution.category in {"tv", "anime"} and not process_tv_episodes and resolved_path.is_file():
            continue
        if _normalize_runtime_path(resolved_path) in episode_paths_added_with_packs:
            continue
        raw_items.append((resolved_path, resolution.category))

def _collect_targeted_job_items(
    *,
    paths: List[str],
    item_hints: Optional[List[Dict[str, Any]]],
    category: str,
    conf: Any,
    runtime_job: Optional[dict[str, Any]],
    process_tv_episodes: bool,
) -> Optional[list[tuple[Path, str]]]:
    """Resolve the selected paths; None means the user stopped the job meanwhile."""
    log_info(f"Targeted upload: {len(paths)} item(s)")
    raw_items: list[tuple[Path, str]] = []
    staged_pack_source_dirs: set[str] = set()
    item_hint_map = _build_item_hint_map(item_hints)

    for raw_target in paths:
        if runtime_job and not wait_for_job_resume(runtime_job):
            log_info("Job stopped by user during path resolution.")
            update_job_progress(status="stopped")
            return None
        selected_path = _resolve_targeted_path(raw_target)
        if not selected_path:
            log_info(f"⏩ Non-absolute targeted path rejected: {raw_target}", "WARN")
            continue
        if not selected_path.exists():
            log_info(f"⏩ Missing path: {selected_path} (skipped)", "WARN")
            continue
        if selected_path.name.startswith("."):
            continue

        hint = item_hint_map.get(_normalize_runtime_path(selected_path), {})
        category_hint = str(hint.get("manual_category") or "")
        itype_hint = str(hint.get("itype") or "") if category_hint else ""
        resolution = resolve_explicit_path(
            selected_path,
            category_hint=category_hint,
            itype_hint=itype_hint,
            respect_explicit_hint=bool(category_hint),
            anime_lookup=_processing_cached_anime_lookup,
        )
        _log_explicit_resolution(resolution)
        if "disc" in resolution.content_flags:
            raw_items.append((selected_path, "disc"))
            continue
        _append_targeted_resolution_items(
            raw_items,
            selected_path=selected_path,
            resolution=resolution,
            conf=conf,
            runtime_job=runtime_job,
            process_tv_episodes=process_tv_episodes,
            staged_pack_source_dirs=staged_pack_source_dirs,
        )

    return _inject_inferred_tv_pack_entries(raw_items, conf, runtime_job, staged_pack_source_dirs)

def _collect_scanned_job_items(
    conf: Any,
    categories: list[str],
    *,
    process_tv_episodes: bool,
) -> list[tuple[Path, str]]:
    raw_items = []
    for scan_item in scan_configured_items(conf, must_exist=True):
        if scan_item.category not in categories:
            continue
        if scan_item.category in {"tv", "anime"} and not process_tv_episodes and scan_item.path.is_file():
            continue
        raw_items.append((scan_item.path, scan_item.category))
    return raw_items

def _validate_execution_item(
    item: Path,
    category: str,
    current_job: Optional[dict[str, Any]],
    context: _JobExecutionContext,
) -> QueueItemValidation:
    prefetched_dest_status, prefetched_base_folder = _prefetched_validation_state(context, item)

    # Batch duplicate state is already available in the parent process. Avoid
    # launching/importing an isolated Python worker-and avoid walking the item
    # tree-when every selected destination is known to be complete.
    if prefetched_dest_status is not None:
        conf = get_config()
        selected_indexers = _selected_indexers(
            conf,
            context.target_indexer_id,
            context.target_indexer_ids,
        )
        # The prefetch is size-aware (it passes live filesizes), so a replaced
        # file with the same name is not skipped here.
        if selected_indexers and _should_skip_completed_item(
            selected_indexers,
            conf,
            prefetched_dest_status,
            force=context.force,
            name=item.name,
        ):
            return QueueItemValidation(
                "skipped",
                item,
                category,
                _processing_db_type(item, category),
                message=_already_exists_skip_message(item, prefetched_base_folder, 0),
                base_folder=prefetched_base_folder,
                prefetched_dest_status=prefetched_dest_status,
            )

    return _run_validation_with_timeout(
        item,
        category=category,
        force=context.force,
        configured_folders=context.configured_folders,
        target_indexer_id=context.target_indexer_id,
        target_indexer_ids=context.target_indexer_ids,
        skip_enabled=context.skip_enabled,
        skip_config=context.skip_config,
        runtime_job=current_job,
        prefetched_dest_status=prefetched_dest_status,
        prefetched_base_folder=prefetched_base_folder,
    )

def _execute_job_queue(
    sorted_items: list[tuple[Path, str]],
    *,
    preserve_explicit_order: bool,
    runtime_job: Optional[dict[str, Any]],
    paths: Optional[List[str]],
    context: _JobExecutionContext,
    run_state: _JobRunState,
) -> bool:
    """Validate and upload one item ahead; return False when the run stops early."""
    with ThreadPoolExecutor(max_workers=1) as upload_executor:
        for _index, (item, category) in _iter_work_items(
            sorted_items,
            preserve_explicit_order=preserve_explicit_order,
            runtime_job=runtime_job,
            paths=paths,
        ):
            run_state.complete_active_upload(wait=False)
            if run_state.stop_processing or run_state.limit_reached:
                break

            current_job = get_thread_job()
            if current_job and not wait_for_job_resume(current_job):
                log_info("Job stopped by user.")
                update_job_progress(status="stopped")
                return False
            if current_job and current_job.get("stop_requested"):
                log_info("Job stopped by user.")
                update_job_progress(status="stopped")
                return False

            if current_job is not None:
                _begin_runtime_item_checkpoint(
                    current_job,
                    item,
                    make_current=run_state.active_upload_future is None,
                )
            if current_job is not None and run_state.active_upload_future is None:
                current_job["current_stage"] = "VALIDATING ITEM"
                update_job_progress(
                    msg=f"Validating {item.name} ({run_state.completed_count + 1}/{run_state.total})",
                    processed=run_state.completed_count,
                    total=run_state.total,
                    percent=int((run_state.completed_count / run_state.total) * 100) if run_state.total else 0,
                    skipped=run_state.duplicate_count,
                )

            validation = _validate_execution_item(item, category, current_job, context)
            handled = _handle_nonready_validation(validation, item, current_job, run_state)
            if handled is False:
                return False
            if handled is True:
                continue

            if run_state.active_upload_future is not None:
                run_state.complete_active_upload(wait=True)
                if run_state.stop_processing:
                    return False
                if run_state.limit_reached:
                    break

            if current_job is not None:
                current_job["_current_item_path"] = str(validation.path)
                _persist_runtime_job_checkpoint(current_job)

            run_state.active_upload_validation = validation
            run_state.active_upload_future = upload_executor.submit(
                _run_validated_upload,
                validation,
                force=context.force,
                test_mode=context.test_mode,
                target_indexer_id=context.target_indexer_id,
                target_indexer_ids=context.target_indexer_ids,
                runtime_job=current_job,
            )

        if (
            run_state.active_upload_future is not None
            and not run_state.stop_processing
            and not run_state.limit_reached
        ):
            run_state.complete_active_upload(wait=True)
            if run_state.stop_processing:
                return False

    return True

def run_job(
    category: str,
    limit: Optional[int] = None,
    skip_packs: bool = False,
    skip_episodes: bool = False,
    force: bool = False,
    test_mode: bool = False,
    target_indexer_id: Optional[str] = None,
    target_indexer_ids: Optional[List[str]] = None,
    paths: Optional[List[str]] = None,
    item_hints: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """Run a batch processing job."""
    conf = get_config()
    if not check_tools(conf):
        return

    runtime_job = get_thread_job()
    if runtime_job:
        runtime_job["test_mode"] = test_mode

    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_info(f"🚀 Starting job: {category.upper()}")
    log_info(
        f"⚙️  Settings: [Test Mode: {test_mode}] [Skip Dupes: {force}] "
        f"[Skip Packs: {skip_packs}] [Skip Episodes: {skip_episodes}] "
        f"[Target: {limit or 'All'}]"
    )
    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    cat_lower = category.lower()
    cats = _resolve_job_categories(category)
    configured_folders = get_configured_folders(conf, must_exist=True)
    process_tv_episodes = bool(getattr(conf, "process_tv_episodes", True))
    if skip_episodes:
        process_tv_episodes = False

    if not process_tv_episodes and "tv" in cats:
        log_info("⏩ Skipping TV - episodes disabled in settings")
        cats.remove("tv")

    # If explicit paths were provided (e.g. from Pending -> Force Upload),
    # only process those paths and skip full folder scans.
    if paths:
        if cat_lower not in cats and cat_lower not in {"all", "both", "mixed", "selected"}:
            # Respect the processing filters from settings even in targeted mode.
            log_info(f"⏩ Skipping {cat_lower.upper()} - disabled in settings")
            return

        raw_items = _collect_targeted_job_items(
            paths=paths,
            item_hints=item_hints,
            category=category,
            conf=conf,
            runtime_job=runtime_job,
            process_tv_episodes=process_tv_episodes,
        )
        if raw_items is None:
            return
    else:
        raw_items = _collect_scanned_job_items(
            conf,
            cats,
            process_tv_episodes=process_tv_episodes,
        )

    if not raw_items:
        if _guard_no_raw_items(category, paths):
            return

    # Size limits from config
    folder_limit = getattr(conf, "folder_size_limit_gb", 99)
    folder_enabled = getattr(conf, "folder_size_limit_enabled", True)
    file_limit = getattr(conf, "file_size_limit_gb", 0)
    file_enabled = getattr(conf, "file_size_limit_enabled", True)

    preserve_explicit_order = bool(paths)

    sorted_items, _oversized_folders, _oversized_files = _plan_sorted_items(
        raw_items,
        cats,
        preserve_explicit_order=preserve_explicit_order,
        skip_packs=skip_packs,
        folder_limit=folder_limit,
        folder_enabled=folder_enabled,
        file_limit=file_limit,
        file_enabled=file_enabled,
    )
    if preserve_explicit_order and runtime_job is not None:
        runtime_job["target_paths"] = [str(path) for path, _cat in sorted_items]

    effective_limit = limit
    if not effective_limit:
        configured_limit = getattr(conf, "item_limit_per_category", None)
        if configured_limit:
            effective_limit = int(configured_limit)

    total = len(sorted_items)
    if paths:
        logger.info(
            f"[QUEUE-RUN] selected={len(paths)} filtered={len(raw_items)} final_queued={total}"
        )
    if paths and total == 0:
        reason = (
            f"No valid items remained after queue validation: selected={len(paths)} "
            f"filtered={len(raw_items)} final_queued=0"
        )
        logger.error(f"[QUEUE-RUN] {reason}")
        update_job_progress(msg=reason, status="failed", total=len(paths), processed=0, percent=0)
        raise ValueError(reason)
    update_job_progress(total=total, processed=0, percent=0)
    if effective_limit and not limit:
        log_info(f"Target Limit: Job will stop after {effective_limit} success(es).")

    log_info(f"🔍 Queue Discovery: found {total} items in total.")
    if limit:
        log_info(f"🎯 Target Limit: Job will stop after {limit} success(es).")

    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_info(f"⚡ Processing queue: {total} items to check.")
    log_info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Skip files config
    skip_config = getattr(conf, "skip_files", None)
    skip_enabled = isinstance(skip_config, dict) and skip_config.get("enabled", False)
    prefetched_item_db_keys: Dict[str, str] = {}
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]] = None

    if preserve_explicit_order and sorted_items:
        (
            prefetched_item_db_keys,
            prefetched_dupes,
            prefetched_source_root_for,
        ) = _build_duplicate_prefetch_state(
            conf,
            sorted_items,
            configured_folders,
            target_indexer_id=target_indexer_id,
            target_indexer_ids=target_indexer_ids,
        )

    run_state = _JobRunState(
        total=total,
        effective_limit=effective_limit,
        test_mode=test_mode,
    )
    execution_context = _JobExecutionContext(
        force=force,
        test_mode=test_mode,
        target_indexer_id=target_indexer_id,
        target_indexer_ids=target_indexer_ids,
        configured_folders=configured_folders,
        skip_enabled=skip_enabled,
        skip_config=cast(Optional[Dict[Any, Any]], skip_config),
        prefetched_item_db_keys=prefetched_item_db_keys,
        prefetched_dupes=prefetched_dupes,
        prefetched_source_root_for=prefetched_source_root_for,
    )
    if not _execute_job_queue(
        sorted_items,
        preserve_explicit_order=preserve_explicit_order,
        runtime_job=runtime_job,
        paths=paths,
        context=execution_context,
        run_state=run_state,
    ):
        return

    update_job_progress(
        processed=run_state.completed_count,
        percent=100,
        skipped=run_state.duplicate_count,
    )

    _log_job_completion(category, test_mode, run_state)

def _log_job_completion(category: str, test_mode: bool, run_state: "_JobRunState") -> None:
    """Log the end-of-job summary and per-indexer totals for run_job.

    Extracted from run_job to keep its own branching down.
    """
    from core.database import get_all_upload_stats

    stats = get_all_upload_stats() or {}
    log_completed("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    mode_str = " (TEST RUN)" if test_mode else ""
    log_completed(f"🏁 Job Finished: {category.upper()}{mode_str}")
    log_completed(
        f"✅ Success: {run_state.success_count} | "
        f"⏩ Skipped: {run_state.duplicate_count} | "
        f"❌ Failed: {run_state.failure_count}"
    )

    if stats:
        # Build dynamic totals line from ALL enabled/active indexers
        active_stats = []
        from core.registry import get_enabled_indexers

        for idx in get_enabled_indexers(get_config()):
            # Fetch count from stats dict (keys are format {idx_id}_count)
            count = stats.get(f"{idx.id}_count", 0)
            active_stats.append(f"{idx.name}: {count}")

        if active_stats:
            log_completed(f"📈 Indexer Totals: {' | '.join(active_stats)}")

    log_completed("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

