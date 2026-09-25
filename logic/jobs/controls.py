"""Job controls: the pause/resume/stop lifecycle, the queue-wide controls, and the user's edits to
a queued or active job (rename, schedule, priority, retry, delete, item order and removal).

Each function acts on the engine's jobs under its lock; JobEngine keeps the public methods and
delegates here. Pause never freezes a tool (no SIGSTOP): the current item finishes, the worker
holds before the next one, and a paused job keeps the queue lane.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

from core.logging import log_info
from logic.jobs import store as job_store
from logic.jobs.models import (
    job_target_paths,
    normalize_job_name,
    normalize_job_path_identity,
    normalize_paths,
    normalize_run_after,
    parse_iso_datetime_utc,
    set_job_target_paths,
)
from logic.stream import monitors as stream_monitors

if TYPE_CHECKING:
    from logic.jobs.engine import JobEngine


def pause_job(engine: JobEngine, job_id: str) -> bool:
    """Mark a running job Paused at once without freezing its tools.

    The item being uploaded finishes normally; the worker then holds in
    wait_for_job_resume before the next item. A paused job keeps the queue
    lane, so no other job starts until it is resumed or stopped.
    """
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False

        status = str(job.get("status"))
        if status == "paused":
            return True
        if status != "running":
            return False

        job["pause_requested"] = True
        job.pop("_pause_ack_callback", None)
        job["status"] = "paused"
        job["progress"] = "Paused by user"
        job["speed"] = "Paused"
        job["current_stage"] = "PAUSED"
        engine._record_job_event(job, "paused", "Paused by user")
        logger.debug(f"Pause requested for job {job_id}; current item finishes first")
        engine._persist_jobs_locked()
    return True


def restore_stopped_job_to_queue_locked(
    engine: JobEngine, job: dict[str, Any], *, progress: Optional[str] = None
) -> bool:
    if not job_store.preserve_stopped_processing_job(job):
        return False

    job["status"] = "queued"
    job["stop_requested"] = False
    job["pause_requested"] = False
    job["current_stage"] = "QUEUED"
    job["speed"] = None
    job["eta"] = None
    job["item_percent"] = 0
    if progress:
        job["progress"] = progress
    elif not str(job.get("progress") or "").strip():
        job["progress"] = "Queued - waiting for queue resume."
    engine._record_job_event(job, "requeued", str(job["progress"]))
    return True


def pause_queue(engine: JobEngine, pause_active: bool = True) -> bool:
    to_pause: list[str] = []
    with engine._lock:
        engine._queue_processing_paused = True
        if pause_active:
            to_pause = [jid for jid, job in engine._jobs.items() if job.get("status") == "running"]
        engine._persist_jobs_locked()

    for job_id in to_pause:
        engine.pause_job(job_id)
    return True


def resume_queue(engine: JobEngine) -> bool:
    with engine._lock:
        engine._queue_processing_paused = False
        for job in engine._jobs.values():
            if job.get("status") == "running" and job.get("pause_requested"):
                job["pause_requested"] = False
                job.pop("_pause_ack_callback", None)
                job["progress"] = "Pause cancelled"
            elif job.get("status") == "paused":
                job["resume_requested"] = True
                job["progress"] = "Resume queued - waiting for scheduler lane..."
        for job in engine._jobs.values():
            if job_store.preserve_stopped_processing_job(job):
                restore_stopped_job_to_queue_locked(
                    engine,
                    job,
                    progress="Queued - waiting for current job to finish...",
                )
        engine._persist_jobs_locked()

    engine._try_start_queued()
    return True


def stop_queue(engine: JobEngine, *, clear_after_stop: bool = False) -> bool:
    to_stop: list[str] = []
    with engine._lock:
        engine._queue_processing_paused = not clear_after_stop
        to_stop = [jid for jid, job in engine._jobs.items() if job.get("status") in ("running", "paused", "stopping")]
        engine._persist_jobs_locked()

    for job_id in to_stop:
        engine.stop_job(job_id, clear_after_stop=clear_after_stop)
    return True


def stop_queue_and_clear(engine: JobEngine) -> dict[str, Any]:
    engine.stop_queue(clear_after_stop=True)
    cleared_jobs = engine.clear_queued_jobs()
    with engine._lock:
        engine._queue_processing_paused = False
        engine._persist_jobs_locked()
    return {
        "cleared_jobs": int(cleared_jobs),
        "control": engine.get_queue_control_state(),
    }


def stop_all_jobs_and_wait(
    engine: JobEngine,
    *,
    clear_staged_items: bool = True,
    wait_timeout_s: float = 15.0,
    poll_interval_s: float = 0.25,
) -> dict[str, Any]:
    engine.stop_queue()
    cleared_jobs = engine.clear_queued_jobs()
    cleared_items = engine.staging.clear() if clear_staged_items else 0

    timed_out = False
    deadline = time.monotonic() + max(0.0, float(wait_timeout_s))
    final_state = engine.get_work_activity_state()

    while final_state["active_count"] > 0:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        time.sleep(max(0.05, float(poll_interval_s)))
        final_state = engine.get_work_activity_state()

    return {
        "queue_paused": bool(final_state["queue_paused"]),
        "cleared_jobs": int(cleared_jobs),
        "cleared_staged_items": int(cleared_items),
        "timed_out": bool(timed_out),
        "active_jobs_remaining": final_state["active_jobs"],
        "queued_jobs_remaining": final_state["queued_jobs"],
        "active_count": int(final_state["active_count"]),
        "queued_count": int(final_state["queued_count"]),
        "staged_count": int(final_state["staged_count"]),
    }


def rename_job(engine: JobEngine, job_id: str, name: Optional[str]) -> tuple[bool, Optional[str]]:
    normalized = normalize_job_name(name)
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False, None
        if normalized:
            job["display_name"] = normalized
        else:
            job.pop("display_name", None)
        engine._record_job_event(job, "renamed", f"Renamed to {normalized or 'default name'}")
        engine._persist_jobs_locked()
        return True, job.get("display_name")


def set_queued_job_schedule(
    engine: JobEngine, job_id: str, run_after: Optional[str]
) -> tuple[bool, Optional[str], str]:
    normalized = normalize_run_after(run_after)
    if run_after not in (None, "") and normalized is None:
        return False, None, "invalid-datetime"

    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False, None, "not-found"
        status = str(job.get("status"))
        if status == "stopped":
            if not job_store.preserve_stopped_processing_job(job):
                return False, None, "not-queued"
        elif status != "queued":
            return False, None, "not-queued"

        if normalized:
            job["run_after"] = normalized
            due_at = parse_iso_datetime_utc(normalized)
            if due_at and due_at > datetime.now(timezone.utc):
                job["progress"] = f"Scheduled for {due_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
            else:
                job["progress"] = "Queued - waiting for current job to finish..."
        else:
            job.pop("run_after", None)
            job["progress"] = "Queued - waiting for current job to finish..."

        current = job.get("run_after")
        engine._record_job_event(
            job,
            "scheduled" if current else "schedule-cleared",
            str(job.get("progress") or "Schedule updated"),
        )
        engine._persist_jobs_locked()

    engine._try_start_queued()
    return True, current, "ok"


def resume_job(engine: JobEngine, job_id: str) -> bool:
    should_try_start = False
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False

        status = str(job.get("status"))
        if status == "running":
            if job.get("pause_requested"):
                job["pause_requested"] = False
                job.pop("_pause_ack_callback", None)
                job["progress"] = "Pause cancelled"
                engine._record_job_event(job, "pause-cancelled", str(job["progress"]))
                engine._persist_jobs_locked()
            return True
        if status == "stopped":
            if not restore_stopped_job_to_queue_locked(
                engine,
                job,
                progress="Queued - waiting for current job to finish...",
            ):
                return False

            engine._queue_processing_paused = False
            queued = [queued_job for queued_job in engine._jobs.values() if queued_job.get("status") == "queued"]
            if queued:
                earliest = min(str(queued_job.get("started_at") or "") for queued_job in queued)
                try:
                    dt = datetime.fromisoformat(earliest)
                    job["started_at"] = (dt - timedelta(seconds=1)).isoformat()
                except (TypeError, ValueError):
                    job["started_at"] = datetime.now(timezone.utc).isoformat()
            else:
                job["started_at"] = datetime.now(timezone.utc).isoformat()

            engine._persist_jobs_locked()
            should_try_start = True
        elif status == "paused":
            lane_busy = any(
                other.get("status") in {"running", "stopping"}
                for other_id, other in engine._jobs.items()
                if other_id != job_id
            )
            if lane_busy or engine._queue_processing_paused:
                job["resume_requested"] = True
                job["progress"] = "Resume queued - waiting for scheduler lane..."
                engine._record_job_event(job, "resume-requested", str(job["progress"]))
            elif resume_paused_job_locked(engine, job_id, job):
                should_try_start = True
            engine._persist_jobs_locked()
        else:
            return False

    if should_try_start:
        engine._try_start_queued()
    return True


def resume_paused_job_locked(engine: JobEngine, job_id: str, job: dict[str, Any]) -> bool:
    """Resume a paused job; True means it was re-queued and needs a launch.

    A job restored as paused after a restart has no worker thread, so it is
    re-queued for a fresh start. A live paused worker is blocked in
    wait_for_job_resume and simply continues.
    """
    job["resume_requested"] = False
    job["pause_requested"] = False
    job.pop("_pause_ack_callback", None)
    if job.pop("_restored_paused", False):
        job["status"] = "queued"
        job["progress"] = "Re-queued after resume"
        job["current_stage"] = "QUEUED"
        job["speed"] = None
        engine._record_job_event(job, "resumed", "Re-queued after resume")
        return True
    job["status"] = "running"
    job["progress"] = "Resumed"
    engine._record_job_event(job, "resumed", "Job resumed")
    if str(job.get("speed") or "").strip().lower() == "paused":
        job["speed"] = "Starting..."
    if str(job.get("current_stage") or "") == "PAUSED":
        job["current_stage"] = "UPLOADING"

    logger.debug(f"Resumed job {job_id}")
    return False


def stop_job(engine: JobEngine, job_id: str, *, clear_after_stop: bool = False) -> bool:
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False

        if job.get("status") == "queued":
            log_info(f"Cancelled queued job {job_id} ({job['category']})")
            job["status"] = "cancelled"
            job["progress"] = "Cancelled (was queued)"
            engine._record_job_event(job, "cancelled", str(job["progress"]))
            engine._cleanup_job_artifacts_locked(job)
            if job.get("source_monitor_id"):
                stream_monitors.record_stream_monitor_job(str(job.get("source_monitor_id")), job)
            job.pop("_kwargs", None)
            job.pop("_paths", None)
            if clear_after_stop:
                engine._jobs.pop(job_id, None)
            engine._persist_jobs_locked()
            return True

        if job.get("status") in {"running", "paused", "stopping"}:
            prev_status = str(job.get("status"))
            if prev_status == "stopping":
                if clear_after_stop:
                    job["_clear_after_stop"] = True
                    job["_hide_while_stopping"] = True
                    job["progress"] = "Stopping... clearing when safe"
                    engine._queue_processing_paused = False
                    engine._persist_jobs_locked()
                return True
            log_info(f"STOP REQUESTED for job {job_id} ({job['category']})", "WARN")
            job["stop_requested"] = True
            job["pause_requested"] = False
            job["status"] = "stopping"
            job["progress"] = "Stopping... clearing when safe" if clear_after_stop else "Stopping..."
            if clear_after_stop:
                job["_clear_after_stop"] = True
                job["_hide_while_stopping"] = True
            engine._record_job_event(job, "stop-requested", str(job["progress"]))
            engine._persist_jobs_locked()

            kill_delay_s = 0.5 if clear_after_stop else 1.0
            terminated = engine.process_registry().terminate_locked(job_id, kill_delay_s=kill_delay_s)
            logger.debug(f"Termination signal sent to {terminated} process(es) for job {job_id}")
            return True
    return False


def retry_job(engine: JobEngine, job_id: str) -> tuple[bool, Optional[str], str]:
    """Create one new processing job from a failed job's immutable request."""
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False, None, "not-found"
        if job.get("status") != "failed" or not job.get("retry_eligible"):
            return False, None, "not-retryable"

        retry_request = job.get("_retry_request")
        if not isinstance(retry_request, dict):
            return False, None, "missing-request"
        retry_kwargs = retry_request.get("kwargs")
        if not isinstance(retry_kwargs, dict):
            return False, None, "missing-request"

        category = str(job.get("category") or "misc")
        paths = normalize_paths(retry_request.get("paths"))
        kwargs = dict(retry_kwargs)
        display_name = normalize_job_name(job.get("display_name")) or engine._job_category_label(category)
        attempt_count = int(job.get("attempt_count") or 0)
        job["retry_eligible"] = False
        engine._record_job_event(job, "retry-requested", "Retry requested")
        engine._persist_jobs_locked()

    kwargs.update(
        reuse_running=False,
        source="retry",
        retry_of=job_id,
        attempt_count_base=attempt_count,
        job_name=f"{display_name} retry",
    )
    if paths:
        kwargs["paths"] = paths
    else:
        kwargs.pop("paths", None)

    try:
        new_job_id = engine.start_upload_job(category, **kwargs)
    except Exception:
        # Any failure to create the retry makes the original retryable again; the error propagates.
        with engine._lock:
            original = engine._jobs.get(job_id)
            if original:
                original["retry_eligible"] = True
                engine._record_job_event(original, "retry-failed", "Unable to create retry job")
                engine._persist_jobs_locked()
        raise

    with engine._lock:
        original = engine._jobs.get(job_id)
        if original:
            original["retried_as"] = new_job_id
            engine._record_job_event(original, "retried", f"Retry queued as {new_job_id}")
            engine._persist_jobs_locked()
    return True, new_job_id, "queued"


def delete_job(engine: JobEngine, job_id: str) -> bool:
    engine.stop_job(job_id)

    with engine._lock:
        if job_id in engine._jobs:
            engine._cleanup_job_artifacts_locked(engine._jobs[job_id])
            del engine._jobs[job_id]
            if job_id in engine._processes:
                del engine._processes[job_id]
            engine._persist_jobs_locked()

            from core.scheduler import get_scheduler  # deferred: the scheduler starts with the app

            get_scheduler().add_job(
                engine._try_start_queued,
                "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=0.5),
            )
            return True
    return False


def clear_completed_jobs(engine: JobEngine) -> int:
    with engine._lock:
        to_delete = [
            job_id
            for job_id, job in engine._jobs.items()
            if job["status"] in ["completed", "failed", "cancelled"]
            or (job.get("status") == "stopped" and not job_store.preserve_stopped_processing_job(job))
        ]
        for job_id in to_delete:
            del engine._jobs[job_id]
        engine._persist_jobs_locked()
        return len(to_delete)


def clear_queued_jobs(engine: JobEngine) -> int:
    with engine._lock:
        cleared = 0
        to_delete: list[str] = []

        for job_id, job in engine._jobs.items():
            status = str(job.get("status") or "")
            if status == "queued" or job_store.preserve_stopped_processing_job(job):
                job["status"] = "cancelled"
                job["progress"] = "Cancelled (queue cleared)"
                engine._cleanup_job_artifacts_locked(job)
                job.pop("_kwargs", None)
                job.pop("_paths", None)
                to_delete.append(job_id)
                cleared += 1
                continue

            if status == "stopping" and bool(job.get("stop_requested")):
                job["_clear_after_stop"] = True
                job["progress"] = "Stopping... queued for removal"
                cleared += 1

        for job_id in to_delete:
            del engine._jobs[job_id]
        engine._persist_jobs_locked()
        return cleared


def promote_job(engine: JobEngine, job_id: str) -> bool:
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False
        status = str(job.get("status") or "")
        if status == "stopped":
            if not job_store.preserve_stopped_processing_job(job):
                return False
        elif status != "queued":
            return False
        queued = [
            queued_job
            for queued_job in engine._jobs.values()
            if queued_job["status"] == "queued" or job_store.preserve_stopped_processing_job(queued_job)
        ]
        highest_priority = max((int(queued_job.get("priority") or 0) for queued_job in queued), default=0)
        job["priority"] = highest_priority + 1
        engine._record_job_event(job, "promoted", f"Promoted to priority {job['priority']}")
        engine._persist_jobs_locked()
        return True


def set_job_priority(engine: JobEngine, job_id: str, priority: int) -> tuple[bool, Optional[int]]:
    normalized = max(-100, min(100, int(priority)))
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False, None
        if str(job.get("status") or "") not in {"queued", "paused"}:
            return False, None
        job["priority"] = normalized
        engine._record_job_event(job, "priority-changed", f"Priority set to {normalized}")
        engine._persist_jobs_locked()
    engine._try_start_queued()
    return True, normalized


def _normalize_job_path_mapping(paths: Any) -> tuple[list[str], dict[str, str]]:
    normalized_paths = normalize_paths(paths)
    mapping: dict[str, str] = {}
    for path in normalized_paths:
        identity = normalize_job_path_identity(path)
        if identity:
            mapping.setdefault(identity, path)
    return normalized_paths, mapping


def _match_job_paths_by_identity(current_paths: Any, requested_paths: Any) -> Optional[list[str]]:
    current, current_map = _normalize_job_path_mapping(current_paths)
    requested = normalize_paths(requested_paths)
    if len(requested) != len(current):
        return None

    matched: list[str] = []
    seen_identities: set[str] = set()
    for raw_path in requested:
        identity = normalize_job_path_identity(raw_path)
        if not identity or identity in seen_identities or identity not in current_map:
            return None
        matched.append(current_map[identity])
        seen_identities.add(identity)

    if len(seen_identities) != len(current_map):
        return None
    return matched


def reorder_queued_job_items(engine: JobEngine, job_id: str, ordered_paths: list[str]) -> bool:
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False
        status = str(job.get("status") or "")
        if status == "stopped":
            if not job_store.preserve_stopped_processing_job(job):
                return False
        elif status != "queued":
            return False

        current = job_target_paths(job)
        new_order = _match_job_paths_by_identity(current, ordered_paths)
        if new_order is None:
            return False

        set_job_target_paths(job, new_order)
        engine._persist_jobs_locked()
        return True


def reorder_active_job_items(engine: JobEngine, job_id: str, ordered_paths: list[str]) -> bool:
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job or job.get("status") not in ("running", "paused"):
            return False

        current = job_target_paths(job)
        new_order = _match_job_paths_by_identity(current, ordered_paths)
        if new_order is None:
            return False

        set_job_target_paths(job, new_order)
        engine._persist_jobs_locked()
        return True


def remove_active_job_item(engine: JobEngine, job_id: str, path: str) -> bool:
    """Drop a not-yet-started item from a running or paused job.

    The processing loop re-reads target_paths before each item and skips
    anything listed in _removed_item_paths, so the item is not uploaded.
    """
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job or job.get("status") not in ("running", "paused"):
            return False
        target_identity = normalize_job_path_identity(path)
        if not target_identity:
            return False
        current = job_target_paths(job)
        removed = [p for p in current if normalize_job_path_identity(p) == target_identity]
        if not removed:
            return False
        updated = [p for p in current if normalize_job_path_identity(p) != target_identity]
        job["_removed_item_paths"] = [*normalize_paths(job.get("_removed_item_paths")), *removed]
        set_job_target_paths(job, updated)
        engine._record_job_event(job, "item-removed", str(path))
        engine._persist_jobs_locked()
        return True


def remove_queued_job_item(engine: JobEngine, job_id: str, path: str) -> bool:
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return False
        status = str(job.get("status") or "")
        if status == "stopped":
            if not job_store.preserve_stopped_processing_job(job):
                return False
        elif status != "queued":
            return False

        current = job_target_paths(job)
        target_identity = normalize_job_path_identity(path)
        if not target_identity:
            return False

        updated = [
            current_path
            for current_path in current
            if normalize_job_path_identity(current_path) != target_identity
        ]
        if len(updated) == len(current):
            return False
        if updated:
            set_job_target_paths(job, updated)
        else:
            engine._jobs.pop(job_id, None)

        engine._persist_jobs_locked()
        return True
