# Auto-split mixin from queueing.py - verbatim method bodies.
# W11-B10: request shaping lives in logic/jobs/requests.py and persistence in logic/jobs/store.py;
# the one-line delegations below are removed with the mixins in W12-B12.

from logic.jobs import requests as job_requests
from logic.jobs import store as job_store
from logic.jobs.models import normalize_job_name, normalize_run_after, parse_iso_datetime_utc
from core.db import job_history as db_job_history
from logic.queueing_base import (
    Any, Optional, ProcessingJobRequest, datetime, log_success, logger, timezone, usenet_stream,
)

class _QueueServiceMixinPart2:
    @classmethod
    def _collapse_overlapping_tv_request_paths(cls, request: ProcessingJobRequest) -> ProcessingJobRequest:
        return job_requests.collapse_overlapping_tv_request_paths(request)

    def start_processing_job_requests(self, requests: list[ProcessingJobRequest], **job_kwargs: Any) -> list[str]:
        """Start multiple normalized processing job requests."""
        return [self.start_processing_job_request(request, **job_kwargs) for request in requests]

    def _is_resumable_stopped_job_locked(self, job: dict[str, Any]) -> bool:
        return job_store.preserve_stopped_processing_job(job)

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
    def _normalize_job_name(name: Any) -> Optional[str]:
        return normalize_job_name(name)

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

    @staticmethod
    def _parse_iso_datetime_utc(raw: Any) -> Optional[datetime]:
        return parse_iso_datetime_utc(raw)

    @classmethod
    def _normalize_run_after(cls, raw: Any) -> Optional[str]:
        return normalize_run_after(raw)

    def _serialize_job_for_persistence(self, job: dict[str, Any]) -> Optional[dict[str, Any]]:
        return job_store.serialize_job(job)

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
        elif status == "stopped" and self._is_resumable_stopped_job_locked(job):
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

        snapshot = self._normalize_paths(job.get("_snapshot_paths"))
        if snapshot:
            job["_completed_paths"] = snapshot

        db_job_history.save_job_history(
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

