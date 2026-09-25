"""The job engine: job state and its lock, the scheduler lane, launch, and lifecycle controls.

Constructing a JobEngine restores persisted jobs and staged items but starts no thread;
logic.runtime.ensure_engine_started() calls start() once. Staged items live in
logic/jobs/staging.py, job execution in logic/jobs/executors.py, and read-only listings in
logic/jobs/views.py.
"""

from __future__ import annotations

import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from core import database
from core.config import get_config
from core.utils import log_info, log_success, reset_thread_job, set_thread_job
from logic import usenet_stream
from logic.jobs import executors
from logic.jobs import requests as job_requests
from logic.jobs import store as job_store
from logic.jobs.models import (
    JobState,
    ProcessingJobRequest,
    job_target_paths,
    normalize_job_name,
    normalize_job_path_identity,
    normalize_job_source,
    normalize_paths,
    normalize_run_after,
    parse_iso_datetime_utc,
    set_job_target_paths,
)
from logic.jobs.processes import ProcessRegistry
from logic.jobs.requests import build_retry_request
from logic.jobs.revalidate import revalidated_plan, revalidation_candidates, revalidation_targets
from logic.jobs.staging import StagingQueue, prepare_start_items, queue_item_label, raise_no_runnable
from logic.jobs.views import job_snapshots, queue_control_state

_SCHEDULER_INTERVAL_S = 15


class JobEngine:
    """Owns every upload job: one scheduler lane, persisted state, and the staged items."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, list[Any]] = {}
        self._queue_processing_paused = False
        self._queue_scheduler_stop = threading.Event()
        self._queue_scheduler_thread: Optional[threading.Thread] = None
        self.staging = StagingQueue(self._lock, database.db_load_queue())

        self._jobs_state_path, self._jobs_state_backup_path = job_store.state_paths(get_config().script_dir)
        self._jobs_state_path.parent.mkdir(parents=True, exist_ok=True)

        with self._lock:
            self._restore_jobs_from_disk_locked()

    @property
    def started(self) -> bool:
        thread = self._queue_scheduler_thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start the scheduler loop and the first queued job; a second call does nothing."""
        with self._lock:
            if self.started:
                return
            self._queue_scheduler_stop.clear()
            self._queue_scheduler_thread = threading.Thread(target=self._queue_scheduler_loop, daemon=True)
            self._queue_scheduler_thread.start()
        self._try_start_queued()

    def stop(self) -> None:
        """Stop the scheduler loop; running jobs keep their own threads."""
        self._queue_scheduler_stop.set()

    def _job_store(self) -> job_store.JobStore:
        return job_store.JobStore(self._jobs_state_path, self._jobs_state_backup_path)

    def _persist_jobs_locked(self) -> None:
        self._job_store().persist(self._jobs, queue_paused=self._queue_processing_paused)

    def _persist_runtime_checkpoint(self) -> None:
        """Persist an active job at a safe item boundary for crash recovery."""
        with self._lock:
            self._persist_jobs_locked()

    def _restore_jobs_from_disk_locked(self) -> None:
        self._queue_processing_paused = self._job_store().restore(self._jobs, queue_paused=self._queue_processing_paused)

    def process_registry(self) -> ProcessRegistry:
        """The job_id -> external tool processes map, guarded by the engine lock."""
        return ProcessRegistry(self._processes, self._lock)

    def _dispatch_job_execution(self, job: dict[str, Any], request: Any) -> None:
        executors.execute_job(self, job, request)

    def _consume_job_launch_state_locked(self, job: dict[str, Any]) -> tuple[str, Any, list[str], Optional[str]]:
        stored_kwargs = job.get("_kwargs", {})
        if not isinstance(stored_kwargs, dict):
            stored_kwargs = {}
        kwargs = dict(stored_kwargs)

        paths = normalize_paths(job.get("_paths"))
        if not paths:
            paths = normalize_paths(job.get("target_paths"))
        job["_snapshot_paths"] = list(paths)
        job.pop("_paths", None)
        job_type = str(kwargs.get("job_type") or job.get("job_type") or "processing")
        cleanup_paths = normalize_paths(kwargs.pop("cleanup_paths", None))
        source_monitor_id = str(kwargs.get("source_monitor_id") or job.get("source_monitor_id") or "") or None

        job["job_type"] = job_type
        if cleanup_paths:
            job["cleanup_paths"] = cleanup_paths
        if source_monitor_id:
            job["source_monitor_id"] = source_monitor_id

        if job_type == "usenet_stream":
            request = job_requests.build_stream_request(str(job.get("category") or "misc"), kwargs)
        else:
            request = job_requests.build_processing_request(str(job.get("category") or "misc"), kwargs, paths)

        return job_type, request, cleanup_paths, source_monitor_id

    @staticmethod
    def _record_job_event(job: dict[str, Any], event_type: str, message: str) -> None:
        events = job.get("events")
        if not isinstance(events, list):
            events = []
        events.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "type": str(event_type),
                "message": str(message)[:240],
            }
        )
        job["events"] = events[-30:]

    def _set_job_running_state_locked(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        job["progress"] = "Starting..."
        job["speed"] = "Starting..."
        job["eta"] = "Starting..."
        job["current_stage"] = "INITIALIZING"
        job["stop_requested"] = False
        job["pause_requested"] = False
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["attempt_count"] = int(job.get("attempt_count") or 0) + 1
        self._record_job_event(
            job,
            "started",
            f"Attempt {job['attempt_count']} started",
        )

    def start_path_jobs(self, grouped_paths: dict[str, list[str]], **kwargs: Any) -> list[dict[str, Any]]:
        """Start one queued job per category for a grouped set of explicit paths."""
        started: list[dict[str, Any]] = []
        for category, raw_paths in grouped_paths.items():
            paths = normalize_paths(raw_paths)
            if not paths:
                continue
            request = ProcessingJobRequest(
                category=str(category or "misc"),
                paths=tuple(paths),
                test_mode=bool(kwargs.get("test_mode", False)),
                target_indexer_id=kwargs.get("indexer_id"),
                target_indexer_ids=tuple(
                    str(value) for value in (kwargs.get("indexer_ids") or []) if str(value).strip()
                ),
                enable_duplicate_check=kwargs.get("enable_duplicate_check", True),
            )
            job_id = self.start_processing_job_request(
                request,
                reuse_running=kwargs.get("reuse_running", True),
                source=kwargs.get("source"),
                job_name=kwargs.get("job_name"),
                run_after=kwargs.get("run_after"),
            )
            started.append(
                {
                    "job_id": job_id,
                    "category": str(category or "misc"),
                    "paths": paths,
                }
            )
        return started

    @classmethod
    def _normalize_job_path_mapping(cls, paths: Any) -> tuple[list[str], dict[str, str]]:
        normalized_paths = normalize_paths(paths)
        mapping: dict[str, str] = {}
        for path in normalized_paths:
            identity = normalize_job_path_identity(path)
            if identity:
                mapping.setdefault(identity, path)
        return normalized_paths, mapping

    @classmethod
    def _match_job_paths_by_identity(cls, current_paths: Any, requested_paths: Any) -> Optional[list[str]]:
        current, current_map = cls._normalize_job_path_mapping(current_paths)
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

    def start_processing_job_request(self, request: ProcessingJobRequest, **job_kwargs: Any) -> str:
        """Start a processing job from an explicit normalized request."""
        # Force upload passes skip_pack_expansion: it starts at once with exactly
        # the rows the user picked, without a pack walk.
        if not request.skip_pack_expansion:
            request = job_requests.expand_explicit_pack_request_paths(request)
            request = job_requests.with_inferred_tv_pack_request_paths(request)
            request = job_requests.collapse_overlapping_tv_request_paths(request)
        return self.start_upload_job(
            category=request.category,
            limit=request.limit,
            skip_packs=request.skip_packs,
            skip_episodes=request.skip_episodes,
            test_mode=request.test_mode,
            indexer_id=request.target_indexer_id,
            indexer_ids=list(request.target_indexer_ids) or None,
            paths=list(request.paths) or None,
            item_hints=list(request.item_hints) or None,
            enable_duplicate_check=request.enable_duplicate_check,
            force=request.force,
            **job_kwargs,
        )

    def start_processing_job_requests(self, requests: list[ProcessingJobRequest], **job_kwargs: Any) -> list[str]:
        """Start multiple normalized processing job requests."""
        return [self.start_processing_job_request(request, **job_kwargs) for request in requests]

    def _restore_stopped_job_to_queue_locked(self, job: dict[str, Any], *, progress: Optional[str] = None) -> bool:
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
        self._record_job_event(job, "requeued", str(job["progress"]))
        return True

    @staticmethod
    def _job_category_label(category: str) -> str:
        key = str(category or "").strip().lower()
        labels = {
            "tv": "TV",
            "movies": "Movies",
            "anime": "Anime",
            "misc": "Misc",
            "both": "Both",
            "mixed": "Mixed",
            "selected": "Selected",
            "all": "All",
        }
        if key in labels:
            return labels[key]
        if not key:
            return "Job"
        return key.replace("_", " ").title()

    @classmethod
    def _default_job_name(cls, category: str, item_count: int, paths: Any = None) -> str:
        """Name a job '<Category> - N items' as the queue page shows it (paths is unused)."""
        del paths
        count = max(int(item_count or 0), 0)
        if count <= 0:
            return cls._job_category_label(category)
        noun = "item" if count == 1 else "items"
        return f"{cls._job_category_label(category)} - {count} {noun}"

    def _finalize_job_locked(
        self,
        job: dict[str, Any],
        *,
        duration_sec: float,
        clear_active_fields: bool,
        remove_from_active: bool,
    ) -> None:
        from logic.stats_engine import format_seconds

        job_id = str(job.get("job_id", ""))
        duration_str = format_seconds(duration_sec)
        status = str(job.get("status") or "")
        clear_after_stop = bool(job.pop("_clear_after_stop", False))

        if status not in {"stopped", "failed"} and (bool(job.get("stop_requested")) or status == "stopping"):
            job["status"] = "stopped"
            job["progress"] = "Stopped by user"
            status = "stopped"
            logger.info(f"Job {job_id} stopped by user.")

        if status == "stopped" and clear_after_stop:
            job["status"] = "cancelled"
            job["progress"] = "Cancelled (queue cleared)"
            status = "cancelled"

        if status not in {"stopped", "failed", "cancelled"}:
            job["status"] = "completed"
            job["progress"] = f"Finished in {duration_str}"
            job["progress_percent"] = 100
            log_success(f"Job {job_id} completed.")
        elif status == "stopped" and job_store.preserve_stopped_processing_job(job):
            self._queue_processing_paused = True

        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        if job.get("status") == "failed":
            job["last_error"] = str(job.get("progress") or "Job failed")[:500]
            job["retry_eligible"] = bool(
                str(job.get("job_type") or "processing") == "processing"
                and isinstance(job.get("_retry_request"), dict)
            )
        else:
            job["last_error"] = None
            job["retry_eligible"] = False
        self._record_job_event(
            job,
            str(job.get("status") or "finished"),
            str(job.get("progress") or "Job finished"),
        )

        snapshot = normalize_paths(job.get("_snapshot_paths"))
        if snapshot:
            job["_completed_paths"] = snapshot

        database.save_job_history(
            job_id,
            category=job.get("category"),
            status=job.get("status"),
            items_processed=job.get("items_processed", 0),
            items_total=job.get("items_total", 0),
            items_skipped=job.get("items_skipped", 0),
            total_bytes=job.get("total_bytes", 0) or 0,
            duration_seconds=duration_sec,
            test_mode=bool(job.get("test_mode", False)),
            started_at=job.get("started_at"),
            completed_at=job["finished_at"],
            error_message=str(job.get("progress")) if job.get("status") == "failed" else None,
        )

        job["summary"] = {
            "duration": duration_str,
            "processed": f"{job.get('items_processed', 0)}/{job.get('items_total', 0)}",
            "skipped": job.get("items_skipped", 0),
            "total_bytes": job.get("total_bytes", 0),
        }

        if clear_active_fields:
            for key in ["current_item", "item_percent", "speed", "eta", "current_stage"]:
                job[key] = None

        self._cleanup_job_artifacts_locked(job)
        job_store.preserve_stopped_processing_job(job)

        if job.get("source_monitor_id"):
            usenet_stream.record_stream_monitor_job(str(job.get("source_monitor_id")), job)

        if remove_from_active or clear_after_stop:
            self._jobs.pop(job_id, None)

    def _cleanup_job_artifacts_locked(self, job: dict[str, Any]) -> None:
        if job.get("_artifacts_cleaned"):
            return

        cleanup_paths = normalize_paths(job.get("cleanup_paths"))
        kwargs = job.get("_kwargs")
        if isinstance(kwargs, dict):
            cleanup_paths.extend(normalize_paths(kwargs.get("cleanup_paths")))

        seen: set[str] = set()
        for raw in cleanup_paths:
            path_str = str(raw).strip()
            if not path_str or path_str in seen:
                continue
            seen.add(path_str)
            try:
                path = Path(path_str)
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

        job["_artifacts_cleaned"] = True

    def pause_job(self, job_id: str) -> bool:
        """Mark a running job Paused at once without freezing its tools.

        The item being uploaded finishes normally; the worker then holds in
        wait_for_job_resume before the next item. A paused job keeps the queue
        lane, so no other job starts until it is resumed or stopped.
        """
        with self._lock:
            job = self._jobs.get(job_id)
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
            self._record_job_event(job, "paused", "Paused by user")
            logger.debug(f"Pause requested for job {job_id}; current item finishes first")
            self._persist_jobs_locked()
        return True

    def get_queue_control_state(self) -> dict[str, Any]:
        with self._lock:
            return queue_control_state(self._jobs.values(), paused=self._queue_processing_paused)

    def get_work_activity_state(self) -> dict[str, Any]:
        with self._lock:
            active_statuses = ("running", "paused", "stopping")
            active_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "category": str(job.get("category") or "misc"),
                    "source": normalize_job_source(job.get("source")),
                    "status": str(job.get("status") or "unknown"),
                    "display_name": job.get("display_name"),
                    "progress": str(job.get("progress") or ""),
                }
                for job in self._jobs.values()
                if str(job.get("status") or "") in active_statuses
            ]
            queued_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "category": str(job.get("category") or "misc"),
                    "source": normalize_job_source(job.get("source")),
                    "status": str(job.get("status") or "unknown"),
                    "display_name": job.get("display_name"),
                }
                for job in self._jobs.values()
                if str(job.get("status") or "") == "queued" or job_store.preserve_stopped_processing_job(job)
            ]

            return {
                "queue_paused": bool(self._queue_processing_paused),
                "active_jobs": active_jobs,
                "queued_jobs": queued_jobs,
                "active_count": len(active_jobs),
                "queued_count": len(queued_jobs),
                "staged_count": len(self.staging.items),
            }

    def pause_queue(self, pause_active: bool = True) -> bool:
        to_pause: list[str] = []
        with self._lock:
            self._queue_processing_paused = True
            if pause_active:
                to_pause = [jid for jid, job in self._jobs.items() if job.get("status") == "running"]
            self._persist_jobs_locked()

        for job_id in to_pause:
            self.pause_job(job_id)
        return True

    def resume_queue(self) -> bool:
        with self._lock:
            self._queue_processing_paused = False
            for job in self._jobs.values():
                if job.get("status") == "running" and job.get("pause_requested"):
                    job["pause_requested"] = False
                    job.pop("_pause_ack_callback", None)
                    job["progress"] = "Pause cancelled"
                elif job.get("status") == "paused":
                    job["resume_requested"] = True
                    job["progress"] = "Resume queued - waiting for scheduler lane..."
            for job in self._jobs.values():
                if job_store.preserve_stopped_processing_job(job):
                    self._restore_stopped_job_to_queue_locked(
                        job,
                        progress="Queued - waiting for current job to finish...",
                    )
            self._persist_jobs_locked()

        self._try_start_queued()
        return True

    def stop_queue(self, *, clear_after_stop: bool = False) -> bool:
        to_stop: list[str] = []
        with self._lock:
            self._queue_processing_paused = not clear_after_stop
            to_stop = [jid for jid, job in self._jobs.items() if job.get("status") in ("running", "paused", "stopping")]
            self._persist_jobs_locked()

        for job_id in to_stop:
            self.stop_job(job_id, clear_after_stop=clear_after_stop)
        return True

    def stop_queue_and_clear(self) -> dict[str, Any]:
        self.stop_queue(clear_after_stop=True)
        cleared_jobs = self.clear_queued_jobs()
        with self._lock:
            self._queue_processing_paused = False
            self._persist_jobs_locked()
        return {
            "cleared_jobs": int(cleared_jobs),
            "control": self.get_queue_control_state(),
        }

    def stop_all_jobs_and_wait(
        self,
        *,
        clear_staged_items: bool = True,
        wait_timeout_s: float = 15.0,
        poll_interval_s: float = 0.25,
    ) -> dict[str, Any]:
        self.stop_queue()
        cleared_jobs = self.clear_queued_jobs()
        cleared_items = self.staging.clear() if clear_staged_items else 0

        timed_out = False
        deadline = time.monotonic() + max(0.0, float(wait_timeout_s))
        final_state = self.get_work_activity_state()

        while final_state["active_count"] > 0:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(max(0.05, float(poll_interval_s)))
            final_state = self.get_work_activity_state()

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

    def rename_job(self, job_id: str, name: Optional[str]) -> tuple[bool, Optional[str]]:
        normalized = normalize_job_name(name)
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None
            if normalized:
                job["display_name"] = normalized
            else:
                job.pop("display_name", None)
            self._record_job_event(job, "renamed", f"Renamed to {normalized or 'default name'}")
            self._persist_jobs_locked()
            return True, job.get("display_name")

    def set_queued_job_schedule(self, job_id: str, run_after: Optional[str]) -> tuple[bool, Optional[str], str]:
        normalized = normalize_run_after(run_after)
        if run_after not in (None, "") and normalized is None:
            return False, None, "invalid-datetime"

        with self._lock:
            job = self._jobs.get(job_id)
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
            self._record_job_event(
                job,
                "scheduled" if current else "schedule-cleared",
                str(job.get("progress") or "Schedule updated"),
            )
            self._persist_jobs_locked()

        self._try_start_queued()
        return True, current, "ok"

    def resume_job(self, job_id: str) -> bool:
        should_try_start = False
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            status = str(job.get("status"))
            if status == "running":
                if job.get("pause_requested"):
                    job["pause_requested"] = False
                    job.pop("_pause_ack_callback", None)
                    job["progress"] = "Pause cancelled"
                    self._record_job_event(job, "pause-cancelled", str(job["progress"]))
                    self._persist_jobs_locked()
                return True
            if status == "stopped":
                if not self._restore_stopped_job_to_queue_locked(
                    job,
                    progress="Queued - waiting for current job to finish...",
                ):
                    return False

                self._queue_processing_paused = False
                queued = [queued_job for queued_job in self._jobs.values() if queued_job.get("status") == "queued"]
                if queued:
                    earliest = min(str(queued_job.get("started_at") or "") for queued_job in queued)
                    try:
                        dt = datetime.fromisoformat(earliest)
                        job["started_at"] = (dt - timedelta(seconds=1)).isoformat()
                    except (TypeError, ValueError):
                        job["started_at"] = datetime.now(timezone.utc).isoformat()
                else:
                    job["started_at"] = datetime.now(timezone.utc).isoformat()

                self._persist_jobs_locked()
                should_try_start = True
            elif status == "paused":
                lane_busy = any(
                    other.get("status") in {"running", "stopping"}
                    for other_id, other in self._jobs.items()
                    if other_id != job_id
                )
                if lane_busy or self._queue_processing_paused:
                    job["resume_requested"] = True
                    job["progress"] = "Resume queued - waiting for scheduler lane..."
                    self._record_job_event(job, "resume-requested", str(job["progress"]))
                elif self._resume_paused_job_locked(job_id, job):
                    should_try_start = True
                self._persist_jobs_locked()
            else:
                return False

        if should_try_start:
            self._try_start_queued()
        return True

    def _resume_paused_job_locked(self, job_id: str, job: dict[str, Any]) -> bool:
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
            self._record_job_event(job, "resumed", "Re-queued after resume")
            return True
        job["status"] = "running"
        job["progress"] = "Resumed"
        self._record_job_event(job, "resumed", "Job resumed")
        if str(job.get("speed") or "").strip().lower() == "paused":
            job["speed"] = "Starting..."
        if str(job.get("current_stage") or "") == "PAUSED":
            job["current_stage"] = "UPLOADING"

        logger.debug(f"Resumed job {job_id}")
        return False

    def stop_job(self, job_id: str, *, clear_after_stop: bool = False) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.get("status") == "queued":
                log_info(f"Cancelled queued job {job_id} ({job['category']})")
                job["status"] = "cancelled"
                job["progress"] = "Cancelled (was queued)"
                self._record_job_event(job, "cancelled", str(job["progress"]))
                self._cleanup_job_artifacts_locked(job)
                if job.get("source_monitor_id"):
                    usenet_stream.record_stream_monitor_job(str(job.get("source_monitor_id")), job)
                job.pop("_kwargs", None)
                job.pop("_paths", None)
                if clear_after_stop:
                    self._jobs.pop(job_id, None)
                self._persist_jobs_locked()
                return True

            if job.get("status") in {"running", "paused", "stopping"}:
                prev_status = str(job.get("status"))
                if prev_status == "stopping":
                    if clear_after_stop:
                        job["_clear_after_stop"] = True
                        job["_hide_while_stopping"] = True
                        job["progress"] = "Stopping... clearing when safe"
                        self._queue_processing_paused = False
                        self._persist_jobs_locked()
                    return True
                log_info(f"STOP REQUESTED for job {job_id} ({job['category']})", "WARN")
                job["stop_requested"] = True
                job["pause_requested"] = False
                job["status"] = "stopping"
                job["progress"] = "Stopping... clearing when safe" if clear_after_stop else "Stopping..."
                if clear_after_stop:
                    job["_clear_after_stop"] = True
                    job["_hide_while_stopping"] = True
                self._record_job_event(job, "stop-requested", str(job["progress"]))
                self._persist_jobs_locked()

                terminated = self.process_registry().terminate_locked(job_id, kill_delay_s=0.5 if clear_after_stop else 1.0)
                logger.debug(f"Termination signal sent to {terminated} process(es) for job {job_id}")
                return True
        return False

    def stop_and_clear_job(self, job_id: str) -> bool:
        return self.stop_job(job_id, clear_after_stop=True)

    def _find_reusable_running_job(self, category: str) -> Optional[str]:
        """Return the job_id of an existing running/paused job in this category, if any.

        Extracted from start_upload_job to keep its own branching down.
        """
        with self._lock:
            for job in self._jobs.values():
                if job["category"] == category and job["status"] in ("running", "paused"):
                    return str(job["job_id"])
        return None

    def _determine_job_wait_state(self, run_after_dt: Optional[datetime]) -> tuple[bool, bool, str, str]:
        """Return (has_running, deferred, wait_msg, schedule_label) for a job about to be created.

        schedule_label is "" unless the job is deferred. Extracted from
        start_upload_job to keep its own branching down.
        """
        with self._lock:
            deferred = bool(run_after_dt and run_after_dt > datetime.now(timezone.utc))
            has_running = (
                bool(self._queue_processing_paused)
                or any(job["status"] in ("running", "stopping") for job in self._jobs.values())
                or deferred
            )

        schedule_label = ""
        if deferred and run_after_dt:
            schedule_label = run_after_dt.astimezone().strftime("%Y-%m-%d %H:%M")
            wait_msg = f"Scheduled for {schedule_label}"
        elif has_running:
            wait_msg = "Queued - waiting for current job to finish..."
        else:
            wait_msg = "Starting..."
        return has_running, deferred, wait_msg, schedule_label

    def _build_new_job_state(
        self,
        category: str,
        has_running: bool,
        wait_msg: str,
        display_name: str,
        run_after: str,
        attempt_count_base: int,
        retry_of: Optional[str],
        job_type: str,
        source: str,
        paths: list,
        kwargs: dict,
    ) -> tuple[str, dict]:
        """Build, register, and persist the in-memory job dict for a new upload job.

        Returns (job_id, job). Extracted from start_upload_job to keep its own
        branching down.
        """
        job_id = str(uuid.uuid4())[:8]
        created_at = datetime.now(timezone.utc).isoformat()
        job: dict[str, Any] = JobState(
            job_id=job_id,
            category=category,
            status="queued" if has_running else "running",
            started_at=created_at,
            created_at=created_at,
            progress=wait_msg,
            speed=None if has_running else "Starting...",
            eta=None if has_running else "Starting...",
            current_stage="QUEUED" if has_running else "INITIALIZING",
            test_mode=bool(kwargs.get("test_mode", False)),
            display_name=display_name,
            run_after=run_after,
            priority=int(kwargs.pop("priority", 0) or 0),
            attempt_count=attempt_count_base,
            retry_of=retry_of,
        ).model_dump()
        if not display_name:
            job.pop("display_name", None)
        if not run_after:
            job.pop("run_after", None)
        job["job_type"] = job_type
        job["source"] = source
        if kwargs.get("source_monitor_id"):
            job["source_monitor_id"] = str(kwargs.get("source_monitor_id"))
        job["_kwargs"] = kwargs
        set_job_target_paths(job, paths)
        if job_type == "processing":
            job["_retry_request"] = build_retry_request(kwargs, paths)
        self._record_job_event(
            job,
            "created",
            f"Created from {source}" + (f" as retry of {retry_of}" if retry_of else ""),
        )
        with self._lock:
            self._jobs[job_id] = job
            self._persist_jobs_locked()
        return job_id, job

    def start_upload_job(self, category: str, **kwargs: Any) -> str:
        reuse_running = bool(kwargs.pop("reuse_running", True))
        raw_paths = kwargs.pop("paths", None)
        targeted_request = raw_paths is not None or kwargs.get("item_hints") is not None
        paths = normalize_paths(raw_paths)
        display_name = normalize_job_name(kwargs.pop("job_name", None))
        run_after = normalize_run_after(kwargs.pop("run_after", None))
        retry_of = str(kwargs.pop("retry_of", "") or "") or None
        attempt_count_base = max(0, int(kwargs.pop("attempt_count_base", 0) or 0))
        job_type = str(kwargs.get("job_type") or "processing")
        source = normalize_job_source(kwargs.pop("source", None))
        run_after_dt = parse_iso_datetime_utc(run_after)
        if job_type == "processing" and targeted_request and not paths:
            raise ValueError("No valid items remained after queue filtering")
        if not display_name and paths:
            display_name = self._default_job_name(category, len(paths), paths)

        if reuse_running:
            existing_job_id = self._find_reusable_running_job(category)
            if existing_job_id:
                return existing_job_id

        has_running, deferred, wait_msg, schedule_label = self._determine_job_wait_state(run_after_dt)

        job_id, job = self._build_new_job_state(
            category, has_running, wait_msg, display_name, run_after,
            attempt_count_base, retry_of, job_type, source, paths, kwargs,
        )

        if has_running:
            if deferred and run_after_dt:
                log_info(f"Job {job_id} ({category}, source={source}) scheduled for {schedule_label}.")
            else:
                log_info(f"Job {job_id} ({category}, source={source}) queued - waiting for active job to finish.")
            return job_id

        self._launch_job(job)
        return job_id

    def _launch_job(self, job: dict[str, Any]) -> None:
        start_time_float = time.time()
        with self._lock:
            job["_persist_callback"] = self._persist_runtime_checkpoint
            _job_type, request, _cleanup_paths, source_monitor_id = self._consume_job_launch_state_locked(job)
            self._set_job_running_state_locked(job)
            self._persist_jobs_locked()

        if source_monitor_id:
            usenet_stream.record_stream_monitor_job(source_monitor_id, job)

        category = str(job.get("category") or "misc")
        source = normalize_job_source(job.get("source"))
        logger.info(f"Launching job {job.get('job_id')} ({category}, source={source})")

        def run() -> None:
            try:
                self._dispatch_job_execution(job, request)
            finally:
                duration_sec = time.time() - start_time_float
                with self._lock:
                    self._finalize_job_locked(
                        job,
                        duration_sec=duration_sec,
                        clear_active_fields=True,
                        remove_from_active=False,
                    )
                    self._persist_jobs_locked()
                self._try_start_queued()

        threading.Thread(target=run, daemon=True).start()

    def start_usenet_stream_job(
        self,
        *,
        category: str,
        stream_source_path: str,
        stream_source_name: str,
        release_name: Optional[str] = None,
        test_mode: bool = False,
        enable_duplicate_check: bool = True,
        indexer_id: Optional[str] = None,
        posting_server_name: Optional[str] = None,
        submit_mode: str = "post_and_submit",
        cleanup_paths: Optional[list[str]] = None,
        source_monitor_id: Optional[str] = None,
    ) -> str:
        base_name = normalize_job_name(release_name) or Path(stream_source_name).stem
        job_name = f"NZB Stream - {base_name}"
        return self.start_upload_job(
            category=category,
            reuse_running=False,
            source="usenet-stream",
            job_type="usenet_stream",
            stream_source_path=stream_source_path,
            stream_source_name=stream_source_name,
            release_name=base_name,
            test_mode=test_mode,
            enable_duplicate_check=enable_duplicate_check,
            indexer_id=indexer_id,
            posting_server_name=posting_server_name,
            submit_mode=submit_mode,
            cleanup_paths=cleanup_paths,
            source_monitor_id=source_monitor_id,
            job_name=job_name,
        )

    def _try_start_queued(self) -> None:
        with self._lock:
            if self._queue_processing_paused:
                return
            has_running = any(job["status"] in ("running", "stopping") for job in self._jobs.values())
            if has_running:
                return

            def lane_order(job: dict[str, Any]) -> tuple[int, str]:
                return -int(job.get("priority") or 0), str(job.get("started_at") or "")

            # A paused job keeps the lane: nothing else starts until it is resumed
            # (resume_requested) or stopped.
            paused_jobs = [job for job in self._jobs.values() if job.get("status") == "paused"]
            if paused_jobs:
                resuming = [job for job in paused_jobs if job.get("resume_requested")]
                if not resuming:
                    return
                next_job = min(resuming, key=lane_order)
                requeued = self._resume_paused_job_locked(str(next_job.get("job_id") or ""), next_job)
                self._persist_jobs_locked()
                if not requeued:
                    return
            else:
                now_utc = datetime.now(timezone.utc)
                candidates: list[dict[str, Any]] = []
                for job in self._jobs.values():
                    if job.get("status") != "queued":
                        continue
                    due_at = parse_iso_datetime_utc(job.get("run_after"))
                    if due_at and due_at > now_utc:
                        continue
                    candidates.append(job)
                if not candidates:
                    return
                next_job = min(candidates, key=lane_order)

        log_info(
            f"Dequeuing job {next_job['job_id']} ({next_job['category']}, source={normalize_job_source(next_job.get('source'))})..."
        )
        self._launch_job(next_job)

    def _queue_scheduler_loop(self) -> None:
        while not self._queue_scheduler_stop.wait(_SCHEDULER_INTERVAL_S):
            try:
                self._try_start_queued()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Queue scheduler loop error: {exc}")

    def get_active_jobs(self, *, compact: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            return job_snapshots(self._jobs.values(), compact=compact)

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._jobs.get(job_id)

    def retry_job(self, job_id: str) -> tuple[bool, Optional[str], str]:
        """Create one new processing job from a failed job's immutable request."""
        with self._lock:
            job = self._jobs.get(job_id)
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
            display_name = normalize_job_name(job.get("display_name")) or self._job_category_label(category)
            attempt_count = int(job.get("attempt_count") or 0)
            job["retry_eligible"] = False
            self._record_job_event(job, "retry-requested", "Retry requested")
            self._persist_jobs_locked()

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
            new_job_id = self.start_upload_job(category, **kwargs)
        except Exception:
            with self._lock:
                original = self._jobs.get(job_id)
                if original:
                    original["retry_eligible"] = True
                    self._record_job_event(original, "retry-failed", "Unable to create retry job")
                    self._persist_jobs_locked()
            raise

        with self._lock:
            original = self._jobs.get(job_id)
            if original:
                original["retried_as"] = new_job_id
                self._record_job_event(original, "retried", f"Retry queued as {new_job_id}")
                self._persist_jobs_locked()
        return True, new_job_id, "queued"

    def _apply_revalidation_result(
        self, snapshot: dict[str, Any], prepared: Any
    ) -> Optional[tuple[str, dict[str, Any]]]:
        """Apply one job's revalidation outcome. Returns ("updated"|"cancelled", job_update) or None."""
        new_paths, new_hints, new_category = revalidated_plan(prepared)

        with self._lock:
            job = self._jobs.get(snapshot["job_id"])
            if not job:
                return None
            current_status = str(job.get("status") or "")
            if current_status not in ("queued", "paused"):
                return None

            if new_paths:
                set_job_target_paths(job, new_paths)
                kwargs = dict(job.get("_kwargs") or {})
                kwargs["item_hints"] = tuple(new_hints)
                job["_kwargs"] = kwargs
                if new_category:
                    job["category"] = new_category
                if not str(job.get("display_name") or "").strip():
                    job["display_name"] = self._default_job_name(
                        str(job.get("category") or "misc"),
                        len(new_paths),
                        new_paths,
                    )
                job["progress"] = "Revalidated against current rules"
                outcome = (
                    "updated",
                    {
                        "job_id": snapshot["job_id"],
                        "status": current_status,
                        "changed": True,
                        "kept": len(new_paths),
                        "skipped": len(prepared.skipped_items),
                    },
                )
            else:
                job["status"] = "cancelled"
                job["progress"] = "Cancelled after revalidation"
                job["stop_requested"] = False
                job["pause_requested"] = False
                job.pop("_kwargs", None)
                job.pop("_paths", None)
                self._cleanup_job_artifacts_locked(job)
                outcome = (
                    "cancelled",
                    {
                        "job_id": snapshot["job_id"],
                        "status": current_status,
                        "changed": True,
                        "kept": 0,
                        "skipped": len(prepared.skipped_items),
                    },
                )
            self._persist_jobs_locked()
            return outcome

    def revalidate_queued_jobs(self, *, include_paused: bool = True) -> dict[str, Any]:
        """Re-scan queued/paused processing jobs against the current rules."""
        with self._lock:
            snapshots = revalidation_targets(self._jobs.values(), include_paused)

        if not snapshots:
            return {"inspected": 0, "updated": 0, "cancelled": 0, "jobs": []}

        from logic.classify.walk import begin_scan_cache, end_scan_cache

        inspected = 0
        updated = 0
        cancelled = 0
        job_updates: list[dict[str, Any]] = []
        cache_token = begin_scan_cache()
        try:
            for snapshot in snapshots:
                inspected += 1
                candidates = revalidation_candidates(snapshot)
                prepared = prepare_start_items(candidates)
                outcome = self._apply_revalidation_result(snapshot, prepared)
                if outcome is None:
                    continue
                kind, job_update = outcome
                if kind == "updated":
                    updated += 1
                else:
                    cancelled += 1
                job_updates.append(job_update)
        finally:
            end_scan_cache(cache_token)

        return {
            "inspected": inspected,
            "updated": updated,
            "cancelled": cancelled,
            "jobs": job_updates,
        }

    def delete_job(self, job_id: str) -> bool:
        self.stop_job(job_id)

        with self._lock:
            if job_id in self._jobs:
                self._cleanup_job_artifacts_locked(self._jobs[job_id])
                del self._jobs[job_id]
                if job_id in self._processes:
                    del self._processes[job_id]
                self._persist_jobs_locked()

                from logic.process_reaper import get_scheduler

                get_scheduler().add_job(
                    self._try_start_queued,
                    "date",
                    run_date=datetime.now(timezone.utc) + timedelta(seconds=0.5),
                )
                return True
        return False

    def clear_completed_jobs(self) -> int:
        with self._lock:
            to_delete = [
                job_id
                for job_id, job in self._jobs.items()
                if job["status"] in ["completed", "failed", "cancelled"]
                or (job.get("status") == "stopped" and not job_store.preserve_stopped_processing_job(job))
            ]
            for job_id in to_delete:
                del self._jobs[job_id]
            self._persist_jobs_locked()
            return len(to_delete)

    def clear_queued_jobs(self) -> int:
        with self._lock:
            cleared = 0
            to_delete: list[str] = []

            for job_id, job in self._jobs.items():
                status = str(job.get("status") or "")
                if status == "queued" or job_store.preserve_stopped_processing_job(job):
                    job["status"] = "cancelled"
                    job["progress"] = "Cancelled (queue cleared)"
                    self._cleanup_job_artifacts_locked(job)
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
                del self._jobs[job_id]
            self._persist_jobs_locked()
            return cleared

    def promote_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
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
                for queued_job in self._jobs.values()
                if queued_job["status"] == "queued" or job_store.preserve_stopped_processing_job(queued_job)
            ]
            highest_priority = max((int(queued_job.get("priority") or 0) for queued_job in queued), default=0)
            job["priority"] = highest_priority + 1
            self._record_job_event(job, "promoted", f"Promoted to priority {job['priority']}")
            self._persist_jobs_locked()
            return True

    def set_job_priority(self, job_id: str, priority: int) -> tuple[bool, Optional[int]]:
        normalized = max(-100, min(100, int(priority)))
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None
            if str(job.get("status") or "") not in {"queued", "paused"}:
                return False, None
            job["priority"] = normalized
            self._record_job_event(job, "priority-changed", f"Priority set to {normalized}")
            self._persist_jobs_locked()
        self._try_start_queued()
        return True, normalized

    def reorder_queued_job_items(self, job_id: str, ordered_paths: list[str]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = str(job.get("status") or "")
            if status == "stopped":
                if not job_store.preserve_stopped_processing_job(job):
                    return False
            elif status != "queued":
                return False

            current = job_target_paths(job)
            new_order = self._match_job_paths_by_identity(current, ordered_paths)
            if new_order is None:
                return False

            set_job_target_paths(job, new_order)
            self._persist_jobs_locked()
            return True

    def reorder_active_job_items(self, job_id: str, ordered_paths: list[str]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") not in ("running", "paused"):
                return False

            current = job_target_paths(job)
            new_order = self._match_job_paths_by_identity(current, ordered_paths)
            if new_order is None:
                return False

            set_job_target_paths(job, new_order)
            self._persist_jobs_locked()
            return True

    def remove_active_job_item(self, job_id: str, path: str) -> bool:
        """Drop a not-yet-started item from a running or paused job.

        The processing loop re-reads target_paths before each item and skips
        anything listed in _removed_item_paths, so the item is not uploaded.
        """
        with self._lock:
            job = self._jobs.get(job_id)
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
            self._record_job_event(job, "item-removed", str(path))
            self._persist_jobs_locked()
            return True

    def remove_queued_job_item(self, job_id: str, path: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
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
                self._jobs.pop(job_id, None)

            self._persist_jobs_locked()
            return True

    def start_queue_with_details(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            if not self.staging.items:
                return {
                    "job_ids": [],
                    "jobs_created": 0,
                    "started_items": 0,
                    "skipped_items": 0,
                    "remaining_staged": 0,
                }
            items = list(self.staging.items)

        prepared = prepare_start_items(items)
        logger.info(
            f"[QUEUE-START] staged_selected={len(items)} filtered_valid={len(prepared.runnable_items)} "
            f"excluded={len(prepared.skipped_items)}"
        )
        for index, (skipped_item, reason) in enumerate(prepared.skipped_items, start=1):
            log_info(
                f"[QUEUE-START] Skipping staged item '{queue_item_label(skipped_item, index)}': {reason}",
                "WARNING",
            )

        if not prepared.runnable_items:
            raise_no_runnable(prepared)

        runnable_ids = {
            int(item["id"])
            for item in prepared.runnable_items
            if str(item.get("id") or "").strip() and re.fullmatch(r"\d+", str(item.get("id")))
        }
        categories = {
            str(item.get("category") or "").strip().lower()
            for item in prepared.runnable_items
            if str(item.get("category") or "").strip()
        }
        request_category = next(iter(categories)) if len(categories) == 1 else "mixed"
        job_id = self.start_processing_job_request(
            ProcessingJobRequest(
                category=request_category,
                test_mode=bool(kwargs.get("test_mode", False)),
                target_indexer_id=kwargs.get("indexer_id"),
                paths=tuple(
                    str(item.get("path") or "")
                    for item in prepared.runnable_items
                    if str(item.get("path") or "").strip()
                ),
                item_hints=tuple(dict(item) for item in prepared.runnable_items if isinstance(item, dict)),
                enable_duplicate_check=kwargs.get("enable_duplicate_check", True),
            ),
            reuse_running=False,
            source=str(kwargs.get("source") or "queue-start"),
        )
        if runnable_ids:
            self.staging.remove_started(runnable_ids, job_id)
        remaining_staged = len(self.staging.list_items())
        started_items = len(prepared.runnable_items)
        skipped_items = len(prepared.skipped_items)
        if skipped_items:
            log_info(
                f"[QUEUE-START] Created job {job_id} with final_queued={started_items}; "
                f"{skipped_items} staged item(s) remain in staging",
            )
        else:
            log_info(f"[QUEUE-START] Created job {job_id} with final_queued={started_items}")
        return {
            "job_ids": [job_id],
            "jobs_created": 1,
            "started_items": started_items,
            "skipped_items": skipped_items,
            "remaining_staged": remaining_staged,
        }

    def run_job_sync(
        self,
        category: str,
        limit: Optional[int] = None,
        skip_packs: bool = False,
        skip_episodes: bool = False,
        force: bool = False,
        test_mode: bool = False,
        indexer_id: Optional[str] = None,
        paths: Optional[list] = None,
    ) -> dict[str, Any]:
        job_id = f"cli-{uuid.uuid4().hex[:8]}"
        job: dict[str, Any] = JobState(
            job_id=job_id,
            category=category,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            progress="Starting...",
            current_stage="INITIALIZING",
            test_mode=test_mode,
        ).model_dump()

        with self._lock:
            self._jobs[job_id] = job

        token = set_thread_job(job)
        start_time = time.time()
        try:
            executors.execute_processing_job(
                self,
                job,
                ProcessingJobRequest(
                    category=category,
                    limit=limit,
                    skip_packs=skip_packs,
                    skip_episodes=skip_episodes,
                    test_mode=test_mode,
                    target_indexer_id=indexer_id,
                    paths=tuple(normalize_paths(paths)),
                    force=force,
                ),
            )
        finally:
            reset_thread_job(token)

        duration_sec = time.time() - start_time

        with self._lock:
            self._finalize_job_locked(
                job,
                duration_sec=duration_sec,
                clear_active_fields=False,
                remove_from_active=True,
            )

        return job
