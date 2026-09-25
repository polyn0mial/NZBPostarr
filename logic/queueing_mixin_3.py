# Auto-split mixin from queueing.py - verbatim method bodies.
# W11-B10: the process registry lives in logic/jobs/processes.py; the one-line delegations
# below are removed with the mixins in W12-B12.

from logic.jobs.processes import ProcessRegistry
from logic.jobs.requests import build_retry_request
from logic.queueing_base import (
    Any, JobState, Optional, Path, datetime, log_info, logger, shutil, time, timedelta, timezone, stream_monitors, uuid,
)

class _QueueServiceMixinPart3:
    def _cleanup_job_artifacts_locked(self, job: dict[str, Any]) -> None:
        if job.get("_artifacts_cleaned"):
            return

        cleanup_paths = self._normalize_paths(job.get("cleanup_paths"))
        kwargs = job.get("_kwargs")
        if isinstance(kwargs, dict):
            cleanup_paths.extend(self._normalize_paths(kwargs.get("cleanup_paths")))

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

    def _process_registry(self) -> ProcessRegistry:
        return ProcessRegistry(self._processes, self._lock)

    def register_process(self, job_id: str, process: Any) -> None:
        self._process_registry().register(job_id, process)

    def _terminate_job_processes_locked(self, job_id: str, *, kill_delay_s: float = 0.5) -> int:
        return self._process_registry().terminate_locked(job_id, kill_delay_s=kill_delay_s)

    def unregister_process(self, job_id: str, process: Optional[Any] = None) -> None:
        self._process_registry().unregister(job_id, process)

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
            active = next(
                (
                    {
                        "job_id": j.get("job_id"),
                        "status": j.get("status"),
                        "category": j.get("category"),
                        "display_name": j.get("display_name"),
                    }
                    for j in self._jobs.values()
                    if j.get("status") in ("running", "paused", "stopping")
                ),
                None,
            )
            return {
                "paused": bool(self._queue_processing_paused),
                "active": active,
            }

    def get_work_activity_state(self) -> dict[str, Any]:
        with self._lock:
            active_statuses = ("running", "paused", "stopping")
            active_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "category": str(job.get("category") or "misc"),
                    "source": self._normalize_job_source(job.get("source")),
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
                    "source": self._normalize_job_source(job.get("source")),
                    "status": str(job.get("status") or "unknown"),
                    "display_name": job.get("display_name"),
                }
                for job in self._jobs.values()
                if str(job.get("status") or "") == "queued" or self._is_resumable_stopped_job_locked(job)
            ]

            return {
                "queue_paused": bool(self._queue_processing_paused),
                "active_jobs": active_jobs,
                "queued_jobs": queued_jobs,
                "active_count": len(active_jobs),
                "queued_count": len(queued_jobs),
                "staged_count": len(self._queue_items),
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
                if self._is_resumable_stopped_job_locked(job):
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
        cleared_items = self.clear_queue_items() if clear_staged_items else 0

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
        normalized = self._normalize_job_name(name)
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
        normalized = self._normalize_run_after(run_after)
        if run_after not in (None, "") and normalized is None:
            return False, None, "invalid-datetime"

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None, "not-found"
            status = str(job.get("status"))
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return False, None, "not-queued"
            elif status != "queued":
                return False, None, "not-queued"

            if normalized:
                job["run_after"] = normalized
                due_at = self._parse_iso_datetime_utc(normalized)
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
                    stream_monitors.record_stream_monitor_job(str(job.get("source_monitor_id")), job)
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

                terminated = self._terminate_job_processes_locked(job_id, kill_delay_s=0.5 if clear_after_stop else 1.0)
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
        self._set_job_target_paths(job, paths)
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
        paths = self._normalize_paths(raw_paths)
        display_name = self._normalize_job_name(kwargs.pop("job_name", None))
        run_after = self._normalize_run_after(kwargs.pop("run_after", None))
        retry_of = str(kwargs.pop("retry_of", "") or "") or None
        attempt_count_base = max(0, int(kwargs.pop("attempt_count_base", 0) or 0))
        job_type = str(kwargs.get("job_type") or "processing")
        source = self._normalize_job_source(kwargs.pop("source", None))
        run_after_dt = self._parse_iso_datetime_utc(run_after)
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
