"""Job executors: run a launched job's work on its thread and record runtime state on the job."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from core.utils import reset_thread_job, set_thread_job
from logic import processing, usenet_stream
from logic.jobs.models import ProcessingJobRequest, StreamJobRequest, normalize_paths
from logic.jobs.requests import resolve_force_flag

if TYPE_CHECKING:
    from logic.jobs.engine import JobEngine


def update_job_runtime_state(engine: JobEngine, job: dict[str, Any], **updates: Any) -> None:
    with engine._lock:
        job.update(updates)
        engine._persist_jobs_locked()


def mark_job_failed(engine: JobEngine, job: dict[str, Any], exc: Exception, *, log_prefix: str) -> None:
    logger.exception(f"{log_prefix}: {exc}")
    update_job_runtime_state(engine, job, status="failed", progress=f"Error: {exc}")


def mark_processing_job_started(engine: JobEngine, job: dict[str, Any], request: ProcessingJobRequest) -> None:
    explicit_count = len(request.paths)
    updates: dict[str, Any] = {"test_mode": bool(request.test_mode)}
    if explicit_count > 0:
        prior_processed = int(job.pop("_resume_items_processed", None) or 0)
        prior_total = int(job.pop("_resume_items_total", None) or 0)
        prior_skipped = int(job.pop("_resume_items_skipped", None) or 0)
        is_resume = prior_processed > 0 or prior_total > 0
        total = max(prior_total, prior_processed + explicit_count)
        progress_percent = int(prior_processed / total * 100) if total > 0 else 0
        progress_label = (
            f"Resuming - validating {explicit_count} remaining item(s)..."
            if is_resume
            else f"Validating {explicit_count} selected item(s)..."
        )
        updates.update(
            {
                "items_total": total,
                "items_processed": prior_processed,
                "items_skipped": prior_skipped,
                "current_stage": "VALIDATING SELECTION",
                "progress": progress_label,
                "progress_percent": progress_percent,
            }
        )
    update_job_runtime_state(engine, job, **updates)


def mark_stream_job_started(engine: JobEngine, job: dict[str, Any], request: StreamJobRequest) -> None:
    update_job_runtime_state(
        engine,
        job,
        test_mode=bool(request.test_mode),
        items_total=1,
        items_processed=0,
        items_skipped=0,
        current_stage="PROBING SOURCE NZB",
    )


def execute_job(engine: JobEngine, job: dict[str, Any], request: Any) -> None:
    if isinstance(request, StreamJobRequest):
        execute_usenet_stream_job(engine, job, request)
        return
    execute_processing_job(engine, job, request)


def execute_processing_job(engine: JobEngine, job: dict[str, Any], request: ProcessingJobRequest) -> None:
    """Execute processing.run_job with shared force/error semantics.

    The thread-job binding is restored on exit so callers that run this
    synchronously (CLI, tests with ``ImmediateThread``) don't leak the
    binding into the next operation.
    """
    token = set_thread_job(job)

    try:
        mark_processing_job_started(engine, job, request)
        if bool(job.get("has_explicit_paths")) and not request.paths:
            reason = "No valid items remained after queue filtering"
            logger.error(f"[QUEUE-START] {reason}")
            update_job_runtime_state(
                engine,
                job,
                status="failed",
                progress=reason,
                current_stage="FAILED",
                progress_percent=0,
            )
            return
        test_v = bool(request.test_mode)
        force_v = resolve_force_flag(
            enable_duplicate_check=request.enable_duplicate_check,
            force=request.force,
            test_mode=test_v,
        )

        processing.run_job(
            request.category,
            limit=request.limit,
            skip_packs=request.skip_packs,
            skip_episodes=request.skip_episodes,
            force=force_v,
            test_mode=test_v,
            target_indexer_id=request.target_indexer_id,
            target_indexer_ids=list(request.target_indexer_ids) or None,
            paths=list(request.paths) or None,
            item_hints=list(request.item_hints) or None,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        mark_job_failed(engine, job, e, log_prefix="JOB CRASHED")
    finally:
        reset_thread_job(token)


def execute_usenet_stream_job(engine: JobEngine, job: dict[str, Any], request: StreamJobRequest) -> None:
    """Execute the NZB stream/repost job.

    See ``execute_processing_job`` for the rationale behind the
    ``set_thread_job`` / ``reset_thread_job`` pairing.
    """
    token = set_thread_job(job)

    try:
        mark_stream_job_started(engine, job, request)
        test_v = bool(request.test_mode)

        force_v = resolve_force_flag(
            enable_duplicate_check=request.enable_duplicate_check,
            test_mode=test_v,
        )

        if not request.source_path:
            raise RuntimeError("Stream source path is missing")

        manifest_path = usenet_stream.build_stream_manifest_path(
            request.release_name or Path(str(request.source_path)).stem
        )
        with engine._lock:
            cleanup_paths = normalize_paths(job.get("cleanup_paths"))
            cleanup_paths.append(str(manifest_path))
            job["cleanup_paths"] = list(dict.fromkeys(cleanup_paths))
            engine._persist_jobs_locked()

        usenet_stream.stream_nzb_upload(
            source_path=Path(str(request.source_path)),
            category=request.category,
            release_name=request.release_name,
            target_indexer_id=request.target_indexer_id,
            posting_server_name=request.posting_server_name,
            submit_mode=request.submit_mode,
            force=force_v,
            test_mode=test_v,
            manifest_path=manifest_path,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        mark_job_failed(engine, job, e, log_prefix="STREAM JOB CRASHED")
        if job.get("source_monitor_id"):
            usenet_stream.record_stream_monitor_job(str(job.get("source_monitor_id")), job)
    finally:
        reset_thread_job(token)
