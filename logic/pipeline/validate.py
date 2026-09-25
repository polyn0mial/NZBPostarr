"""Queue item validation: duplicate/size/preview checks run before an item is uploaded."""

from __future__ import annotations

import multiprocessing as mp
import os
import queue as stdlib_queue
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, cast

from loguru import logger

from core.config import get_config
from core.utils import log_verbose, set_thread_job, should_skip_file
from logic.pending.roots import find_configured_root
from logic.pipeline.checkpoints import _normalize_runtime_path
from logic.pipeline.plan import _selected_indexers
from logic.pipeline.prepare import _ONE_GIB
from logic.pipeline.record import _live_size_bytes
from logic.pipeline.submission_category import _processing_db_type, _resolve_submission_category


_ITEM_VALIDATION_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class QueueItemValidation:
    """Validation outcome for a single queued item before upload starts."""

    outcome: str
    path: Path
    category: str
    db_type: str
    message: str = ""
    base_folder: Optional[Path] = None
    item_size_bytes: int = 0
    prefetched_dest_status: Optional[Dict[str, Optional[str]]] = None
    submission_category: Optional[str] = None


def _build_item_key(path: Path, base_folder: Optional[Path]) -> str:
    """Build the canonical database key for a queued item path."""
    try:
        if base_folder:
            return str(path.relative_to(base_folder)).replace("\\", "/")
    except ValueError:
        pass
    # Files outside a configured root (staged pack episodes) are keyed by
    # their pack folder too, so same-named episodes of two packs stay apart.
    if not path.is_dir():
        parent_name = path.parent.name
        if parent_name and parent_name not in {"", "."}:
            return f"{parent_name}/{path.name}"
    return path.name


def _already_exists_skip_message(path: Path, source_root: Optional[Path], size_bytes: int) -> str:
    """Name the pack folder and size so the log shows which copy was skipped."""
    skip_folder = path.parent.name if (source_root is None or path.parent != source_root) else ""
    skip_mb = round(size_bytes / (1024 * 1024)) if size_bytes else 0
    size_part = f" ({skip_mb} MB)" if skip_mb else ""
    folder_part = f" [{skip_folder}]" if skip_folder else ""
    return f"⏩ '{path.name}'{folder_part}{size_part} already exists on all selected destinations - skipped"


def _should_skip_completed_item(
    indexers: list[Any], conf: Any, dest_status: Dict[str, Optional[str]], *, force: bool, name: str
) -> bool:
    """Return True when every enabled destination already has the item."""
    from core.indexers.models import resolve_indexer_enabled

    active_dests_needed = 0
    already_done_count = 0
    for indexer in indexers:
        if not resolve_indexer_enabled(indexer, conf):
            continue
        active_dests_needed += 1
        if dest_status.get(indexer.id) is not None:
            already_done_count += 1

    if force or active_dests_needed == 0 or already_done_count != active_dests_needed:
        logger.debug(
            "[DUPE-CHECK] %s: force=%r  needed=%d  done=%d  => NOT skipping",
            name,
            force,
            active_dests_needed,
            already_done_count,
        )
        return False

    log_verbose(f"Skipping: {name} is already uploaded to all {active_dests_needed} enabled destinations.")
    return True


def _classify_preview_validation(
    validation: "QueueItemValidation",
    *,
    normalized: str,
    selected_indexer_ids: List[str],
    resolved_force: bool,
    oversized_files: Any,
) -> "tuple[str, str, Dict[str, Dict[str, Any]]]":
    """Turn one item's validation result into a preview outcome/reason/destinations.
    Extracted from preview_processing_items to keep its own branching down."""
    destination_status = {
        indexer_id: {
            "status": "uploaded" if (validation.prefetched_dest_status or {}).get(indexer_id) else "pending",
            "uploaded_at": (validation.prefetched_dest_status or {}).get(indexer_id),
        }
        for indexer_id in selected_indexer_ids
    }
    all_uploaded = bool(destination_status) and all(
        destination["status"] == "uploaded" for destination in destination_status.values()
    )
    if validation.outcome == "ready":
        outcome = "ready"
    elif validation.outcome == "skipped" and all_uploaded and not resolved_force:
        outcome = "duplicate"
    elif validation.outcome == "skipped":
        outcome = "excluded"
    elif validation.outcome == "stopped":
        outcome = "blocked"
    else:
        outcome = "invalid"

    reason = validation.message
    if not reason and normalized in oversized_files:
        reason = "File exceeds the configured size limit"
    return outcome, reason, destination_status


def _summarize_preview_details(
    details: List[Dict[str, Any]],
    selected_indexer_ids: List[str],
) -> "tuple[Dict[str, Dict[str, int]], Dict[str, int], int, int, int]":
    """Aggregate per-item preview details into summary counters.
    Extracted from preview_processing_items to keep its own branching down."""
    destination_summary: Dict[str, Dict[str, int]] = {
        indexer_id: {"pending": 0, "uploaded": 0}
        for indexer_id in selected_indexer_ids
    }
    outcome_counts = {
        "ready": 0,
        "duplicate": 0,
        "excluded": 0,
        "invalid": 0,
        "blocked": 0,
    }
    ready_bytes = 0
    total_bytes = 0
    partial_duplicates = 0
    for detail in details:
        outcome = str(detail["outcome"])
        if outcome in outcome_counts:
            outcome_counts[outcome] += 1
        size_bytes = int(detail.get("size_bytes") or 0)
        total_bytes += size_bytes
        if outcome == "ready":
            ready_bytes += size_bytes
        destination_values = list((detail.get("destinations") or {}).values())
        uploaded_destinations = sum(1 for destination in destination_values if destination.get("status") == "uploaded")
        if outcome == "ready" and 0 < uploaded_destinations < len(destination_values):
            partial_duplicates += 1
        for indexer_id, destination in (detail.get("destinations") or {}).items():
            status = str(destination.get("status") or "pending")
            destination_summary.setdefault(indexer_id, {"pending": 0, "uploaded": 0})
            destination_summary[indexer_id][status] = destination_summary[indexer_id].get(status, 0) + 1

    return destination_summary, outcome_counts, ready_bytes, total_bytes, partial_duplicates


def _build_duplicate_prefetch_state(
    conf: Any,
    sorted_items: list[tuple[Path, str]],
    configured_folders: list[Path],
    *,
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
) -> tuple[Dict[str, str], Dict[str, Dict[str, Optional[str]]], Callable[[Path], Optional[Path]]]:
    """Resolve item DB keys and batch-prefetch duplicate state for the job queue."""
    from core.database import get_duplicate_status_batch
    from core.indexers.registry import get_enabled_indexers

    source_root_cache: Dict[str, Optional[Path]] = {}

    def source_root_for(item_path: Path) -> Optional[Path]:
        item_key = _normalize_runtime_path(item_path)
        if item_key not in source_root_cache:
            source_root_cache[item_key] = find_configured_root(item_path, configured_folders)
        return source_root_cache[item_key]

    eligible_indexers = get_enabled_indexers(conf)
    if target_indexer_ids:
        allowed_ids = set(target_indexer_ids)
        eligible_indexers = [idx for idx in eligible_indexers if idx.id in allowed_ids]
    elif target_indexer_id:
        eligible_indexers = [idx for idx in eligible_indexers if idx.id == target_indexer_id]
    eligible_indexer_ids = [idx.id for idx in eligible_indexers]

    item_db_keys: Dict[str, str] = {}
    for item, _cat in sorted_items:
        item_db_keys[_normalize_runtime_path(item)] = _build_item_key(item, source_root_for(item))

    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    if eligible_indexer_ids:

        item_keys = [item_db_keys[_normalize_runtime_path(item)] for item, _cat in sorted_items]
        # Current on-disk size per item key -- lets get_duplicate_status_batch tell
        # a genuine re-upload of the same name apart from a locally-replaced file
        # (same name, different size) instead of treating every name match as done.
        item_filesizes: Dict[str, int] = {
            item_db_keys[_normalize_runtime_path(item)]: _live_size_bytes(item)
            for item, _cat in sorted_items
        }
        prefetched_dupes = get_duplicate_status_batch(item_keys, eligible_indexer_ids, filesizes=item_filesizes)

    return item_db_keys, prefetched_dupes, source_root_for


def _classify_preview_source_item(
    item: Dict[str, Any],
    seen_paths: set[str],
) -> tuple[Optional[Path], str, str, str, Optional[str], str]:
    """Validate one raw preview item.

    Returns (path, display_path, name, category, reject_reason, outcome). reject_reason
    is None when the item is valid and should be queued. Extracted from
    preview_processing_items to keep its own branching down.
    """
    path_text = str(item.get("path") or "").strip()
    category = str(item.get("category") or item.get("detected_category") or "").strip().lower()
    name = str(item.get("name") or (Path(path_text).name if path_text else "") or "Unknown item")
    if not path_text:
        return None, "", name, category, "Missing source path", "invalid"
    path = Path(path_text)
    if not path.is_absolute():
        return path, path_text, name, category, "Source path must be absolute", "invalid"
    if not path.exists():
        return path, path_text, name, category, "Source path was not found", "invalid"
    if not category or category in {"all", "both", "mixed", "selected", "external"}:
        return path, str(path), name, category, "A concrete upload category is required", "invalid"
    normalized = _normalize_runtime_path(path)
    if normalized in seen_paths:
        return path, str(path), name, category, "Duplicate source path in selection", "excluded"
    return path, str(path), name, category, None, "valid"


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
