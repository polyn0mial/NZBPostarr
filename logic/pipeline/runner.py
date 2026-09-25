"""Job runner: run a queue job or a single item through validate, prepare, post and submit."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, cast

from loguru import logger

from core.config import get_config
from core.logging import log_completed, log_info, log_success
from core.paths import path_key
from logic.jobs.context import get_thread_job, set_thread_job, update_job_progress, wait_for_job_resume
from logic.pending.roots import get_configured_folders
from logic.pipeline.checkpoints import (
    _begin_runtime_item_checkpoint,
    _complete_runtime_item_checkpoint,
    _persist_runtime_job_checkpoint,
)
from logic.pipeline.cleanup import purge_item_data
from logic.pipeline.plan import (
    _collect_scanned_job_items,
    _collect_targeted_job_items,
    _guard_no_raw_items,
    _item_exceeds_size_limit,
    _iter_work_items,
    _no_indexers_reason,
    _plan_sorted_items,
    _resolve_job_categories,
    _selected_indexers,
)
from logic.pipeline.posting import (
    _SingleUploadContext,
    _SingleUploadState,
    _build_upload_sets,
    _plan_upload_runs,
    _run_single_upload_flow,
    _split_parallel_server_connections,
)
from logic.pipeline.prepare import (
    _ONE_GIB,
    _resolve_support_assets,
    _scan_item_support_assets,
    check_tools,
    prepare_item,
)
from logic.pipeline.record import _live_size_bytes
from logic.pipeline.submission_category import (
    _processing_db_type,
    _resolve_submission_category,
    _submission_category_label,
)
from logic.pipeline.validate import (
    QueueItemValidation,
    _already_exists_skip_message,
    _build_duplicate_prefetch_state,
    _build_item_key,
    _classify_preview_source_item,
    _classify_preview_validation,
    _run_validation_with_timeout,
    _should_skip_completed_item,
    _summarize_preview_details,
    _validate_queue_item,
)


@dataclass
class _JobRunState:
    """Mutable counters and the single in-flight upload for one processing run."""

    total: int
    effective_limit: Optional[int]
    test_mode: bool
    success_count: int = 0
    duplicate_count: int = 0
    failure_count: int = 0
    completed_count: int = 0
    active_upload_future: Any = None
    active_upload_validation: Optional[QueueItemValidation] = None
    stop_processing: bool = False
    limit_reached: bool = False
    consecutive_failures: int = 0
    max_consecutive_failures: int = 5

    def note_failure(self) -> None:
        """Count a failed item and warn on long failure runs; the job keeps going."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            log_info(f"{self.consecutive_failures} consecutive item failures - continuing job.", "WARN")

    def publish_counts(self) -> None:
        update_job_progress(
            processed=self.completed_count,
            total=self.total,
            percent=int((self.completed_count / self.total) * 100) if self.total else 0,
            skipped=self.duplicate_count,
        )

    def complete_active_upload(self, *, wait: bool) -> None:
        if self.active_upload_future is None or self.active_upload_validation is None:
            return
        if not wait and not self.active_upload_future.done():
            return

        result = self.active_upload_future.result()
        current_job = get_thread_job()
        if current_job is not None and result != 2:
            _complete_runtime_item_checkpoint(current_job, self.active_upload_validation.path)

        if result == 0:
            self.consecutive_failures = 0
            self.success_count += 1
            self.completed_count += 1
            prefix = "[TEST] " if self.test_mode else ""
            log_completed(
                f"{prefix}COMPLETED: {self.active_upload_validation.path.name} "
                f"({self.success_count}/{self.effective_limit or 'All'})"
            )
            self.publish_counts()
            if self.effective_limit and self.success_count >= self.effective_limit:
                log_success(f"Target limit reached: {self.success_count} uploads.")
                self.limit_reached = True
        elif result == 1:
            self.duplicate_count += 1
            self.completed_count += 1
            self.publish_counts()
        elif result == 2:
            self.stop_processing = True
        elif result in (3, 4):
            self.failure_count += 1
            self.completed_count += 1
            self.publish_counts()
            self.note_failure()

        self.active_upload_future = None
        self.active_upload_validation = None


@dataclass(frozen=True)
class _JobExecutionContext:
    force: bool
    test_mode: bool
    target_indexer_id: Optional[str]
    target_indexer_ids: Optional[List[str]]
    configured_folders: List[Any]
    skip_enabled: bool
    skip_config: Optional[Dict[Any, Any]]
    prefetched_item_db_keys: Dict[str, str]
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]]
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]]


def _prefetched_validation_state(
    context: _JobExecutionContext,
    item: Path,
) -> tuple[Optional[Dict[str, Optional[str]]], Optional[Path]]:
    destination_status = None
    normalized_item = path_key(item)
    if context.prefetched_item_db_keys:
        item_db_key = context.prefetched_item_db_keys.get(normalized_item)
        if item_db_key:
            destination_status = context.prefetched_dupes.get(item_db_key)

    base_folder = context.prefetched_source_root_for(item) if context.prefetched_source_root_for else None
    return destination_status, base_folder


def _handle_nonready_validation(
    validation: QueueItemValidation,
    checkpoint_path: Path,
    current_job: Optional[dict[str, Any]],
    run_state: _JobRunState,
) -> Optional[bool]:
    """Handle non-ready validation outcomes; None means the item is ready to upload."""
    if validation.outcome == "stopped":
        message = validation.message or "Job stopped during validation."
        requested_stop = bool(current_job and current_job.get("stop_requested"))
        log_info(message, "WARNING" if requested_stop else "ERROR")
        update_job_progress(
            msg=message,
            status="stopped" if requested_stop else "failed",
        )
        return False

    if validation.outcome == "skipped":
        log_info(validation.message)
        run_state.duplicate_count += 1
    elif validation.outcome == "failed":
        log_info(validation.message or f"Validation failed for {validation.path.name}", "ERROR")
        run_state.failure_count += 1
    else:
        return None

    run_state.completed_count += 1
    _complete_runtime_item_checkpoint(current_job, checkpoint_path)
    run_state.publish_counts()
    if validation.outcome == "failed":
        run_state.note_failure()
    return True


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
    from logic.jobs.requests import resolve_force_flag

    conf = get_config()
    configured_folders = get_configured_folders(conf, must_exist=True)
    resolved_force = resolve_force_flag(
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
        normalized = path_key(path)
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

    planned_keys = {path_key(path) for path, _category in sorted_items}
    for path, category in raw_items:
        normalized = path_key(path)
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
        normalized = path_key(path)
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
    from core.db.ledger import destinations_for

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

    all_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    if not all_indexers:
        reason = _no_indexers_reason(target_indexer_id, target_indexer_ids)
        if reason:
            log_info(reason)
        return 2

    indexer_ids = [idx.id for idx in all_indexers]
    indexer_map = {idx.id: idx for idx in all_indexers}

    key = _build_item_key(path, base_folder)

    if prefetched_dest_status is None:
        dest_status = destinations_for(key, itype, indexer_ids, filesize=item_size_bytes)
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
    from core.db.stats import get_all_upload_stats

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
        from core.indexers.registry import get_enabled_indexers

        for idx in get_enabled_indexers(get_config()):
            # Fetch count from stats dict (keys are format {idx_id}_count)
            count = stats.get(f"{idx.id}_count", 0)
            active_stats.append(f"{idx.name}: {count}")

        if active_stats:
            log_completed(f"📈 Indexer Totals: {' | '.join(active_stats)}")

    log_completed("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
