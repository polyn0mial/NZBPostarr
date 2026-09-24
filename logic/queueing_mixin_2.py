# Auto-split mixin from queueing.py - verbatim method bodies.

from logic.queueing_base import (
    Any, JobState, Optional, Path, ProcessingJobRequest, _FINISHED_JOB_RETENTION_COUNT, _FINISHED_JOB_RETENTION_DAYS, _TERMINAL_JOB_STATUSES,
    atomic_write_text, database, datetime, json, log_info, log_success, logger, os, timedelta, timezone, usenet_stream, uuid,
)

class _QueueServiceMixinPart2:
    @classmethod
    def _collapse_overlapping_tv_request_paths(cls, request: ProcessingJobRequest) -> ProcessingJobRequest:
        """Drop ancestor TV directories when explicit child season folders are also selected."""
        paths = list(request.paths)
        if len(paths) < 2:
            return request

        hints_by_path: dict[str, dict[str, Any]] = {}
        for hint in request.item_hints:
            path_text = str(hint.get("path") or "").strip()
            if path_text:
                hints_by_path[path_text] = dict(hint)

        deduped_paths: list[str] = []
        deduped_hints: list[dict[str, Any]] = []
        seen_identities: set[str] = set()
        for path_text in paths:
            identity = cls._normalize_job_path_identity(path_text)
            if not identity or identity in seen_identities:
                continue
            seen_identities.add(identity)
            deduped_paths.append(path_text)
            hint = hints_by_path.get(path_text)
            if hint is not None:
                deduped_hints.append(hint)

        if len(deduped_paths) < 2:
            return cls._dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

        tv_dir_entries: list[tuple[int, Path]] = []
        for idx, path_text in enumerate(deduped_paths):
            try:
                path = Path(path_text)
            except TypeError:
                continue
            if not path.is_dir():
                continue

            hint = hints_by_path.get(path_text, {})
            if cls._request_path_category(path, hint) != "tv":
                continue
            tv_dir_entries.append((idx, path))

        if len(tv_dir_entries) < 2:
            return cls._dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

        try:
            resolved_entries = [(idx, path.resolve()) for idx, path in tv_dir_entries]
        except OSError:
            resolved_entries = [(idx, path) for idx, path in tv_dir_entries]

        drop_indices: set[int] = set()
        for idx, path in resolved_entries:
            for other_idx, other_path in resolved_entries:
                if other_idx == idx:
                    continue
                try:
                    is_descendant = other_path.is_relative_to(path)
                except AttributeError:
                    is_descendant = str(other_path).startswith(str(path).rstrip("\\/") + os.sep)
                if is_descendant and other_path != path:
                    drop_indices.add(idx)
                    break

        if not drop_indices:
            return cls._dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

        new_paths: list[str] = []
        new_hints: list[dict[str, Any]] = []
        for idx, path_text in enumerate(deduped_paths):
            if idx in drop_indices:
                continue
            new_paths.append(path_text)
            hint = hints_by_path.get(path_text)
            if hint is not None:
                new_hints.append(hint)

        logger.info(
            f"[QUEUE-CREATE] collapsed {len(paths) - len(new_paths)} overlapping ancestor/duplicate TV path(s) before starting job"
        )
        return cls._clone_request_with_paths(request, new_paths, new_hints)

    def start_processing_job_requests(self, requests: list[ProcessingJobRequest], **job_kwargs: Any) -> list[str]:
        """Start multiple normalized processing job requests."""
        return [self.start_processing_job_request(request, **job_kwargs) for request in requests]

    def _preserve_stopped_processing_job_locked(self, job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "stopped":
            return False
        if str(job.get("job_type") or "processing") != "processing":
            return False

        has_explicit_paths = bool(job.get("has_explicit_paths"))
        resumable_paths = self._get_job_target_paths(job)
        current_path = str(job.pop("_current_item_path", "") or "").strip()
        if current_path:
            current_identity = self._normalize_job_path_identity(current_path)
            existing = {self._normalize_job_path_identity(path) for path in resumable_paths}
            if current_identity and current_identity not in existing:
                resumable_paths = [current_path, *resumable_paths]

        if has_explicit_paths:
            if not resumable_paths:
                return False
            job["target_paths"] = list(resumable_paths)
        else:
            job.pop("target_paths", None)

        progress_text = str(job.get("progress") or "").strip().lower()
        if not progress_text or progress_text == "stopping...":
            job["progress"] = "Stopped by user"

        return True

    def _is_resumable_stopped_job_locked(self, job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "stopped":
            return False
        return self._preserve_stopped_processing_job_locked(job)

    def _restore_stopped_job_to_queue_locked(self, job: dict[str, Any], *, progress: Optional[str] = None) -> bool:
        if not self._preserve_stopped_processing_job_locked(job):
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
        if name is None:
            return None
        cleaned = " ".join(str(name).split()).strip()
        if not cleaned:
            return None
        return cleaned[:80]

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
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def _normalize_run_after(cls, raw: Any) -> Optional[str]:
        dt = cls._parse_iso_datetime_utc(raw)
        return dt.isoformat() if dt else None

    def _dedupe_recovery_paths(self, job: dict[str, Any]) -> list[str]:
        """Merge the active item, in-flight items, and remaining targets into one deduped path order."""
        recovery_paths = [
            str(job.get("_current_item_path") or "").strip(),
            *self._normalize_paths(job.get("_inflight_item_paths")),
            *self._get_job_target_paths(job),
        ]
        deduped: list[str] = []
        seen_path_identities: set[str] = set()
        for recovery_path in recovery_paths:
            identity = self._normalize_job_path_identity(recovery_path)
            if not identity or identity in seen_path_identities:
                continue
            seen_path_identities.add(identity)
            deduped.append(recovery_path)
        return deduped

    def _serialize_job_for_persistence(self, job: dict[str, Any]) -> Optional[dict[str, Any]]:
        status = str(job.get("status", "queued"))
        if status == "stopped":
            self._preserve_stopped_processing_job_locked(job)
        elif status not in {"queued", "running", "paused", "stopping", *_TERMINAL_JOB_STATUSES}:
            return None

        kwargs = job.get("_kwargs")
        if not isinstance(kwargs, dict):
            kwargs = {}

        job_id = str(job.get("job_id", "")).strip()
        if not job_id:
            return None

        paths = self._dedupe_recovery_paths(job)

        payload = self._build_job_persistence_payload(job, job_id, status, kwargs, paths)
        if status in _TERMINAL_JOB_STATUSES or (
            status == "stopped" and not self._is_resumable_stopped_job_locked(job)
        ):
            payload["kwargs"] = {}
            payload["paths"] = []
        return payload

    def _build_job_persistence_payload(
        self,
        job: dict[str, Any],
        job_id: str,
        status: str,
        kwargs: dict[str, Any],
        paths: list[str],
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "category": str(job.get("category", "misc")),
            "source": self._normalize_job_source(job.get("source")),
            "status": status,
            "started_at": str(job.get("started_at") or datetime.now(timezone.utc).isoformat()),
            "created_at": str(job.get("created_at") or job.get("started_at") or ""),
            "finished_at": str(job.get("finished_at") or ""),
            "progress": str(job.get("progress") or ""),
            "progress_percent": int(job.get("progress_percent") or 0),
            "test_mode": bool(job.get("test_mode", False)),
            "display_name": self._normalize_job_name(job.get("display_name")),
            "run_after": self._normalize_run_after(job.get("run_after")),
            "priority": int(job.get("priority") or 0),
            "job_type": str(job.get("job_type") or "processing"),
            "items_processed": int(job.get("items_processed") or 0),
            "items_total": int(job.get("items_total") or 0),
            "items_skipped": int(job.get("items_skipped") or 0),
            "total_bytes": int(job.get("total_bytes") or 0),
            "summary": dict(job.get("summary") or {}),
            "attempt_count": int(job.get("attempt_count") or 0),
            "retry_of": str(job.get("retry_of") or "") or None,
            "retried_as": str(job.get("retried_as") or "") or None,
            "retry_eligible": bool(job.get("retry_eligible", False)),
            "last_error": str(job.get("last_error") or "") or None,
            "events": list(job.get("events") or [])[-30:],
            "retry_request": (
                dict(job.get("_retry_request") or {})
                if status == "failed" and bool(job.get("retry_eligible"))
                else {}
            ),
            "kwargs": kwargs,
            "paths": paths,
        }

    def _prune_finished_jobs_locked(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FINISHED_JOB_RETENTION_DAYS)
        finished: list[tuple[datetime, str]] = []
        for job_id, job in self._jobs.items():
            status = str(job.get("status") or "")
            if status not in _TERMINAL_JOB_STATUSES and not (
                status == "stopped" and not self._is_resumable_stopped_job_locked(job)
            ):
                continue
            timestamp = self._parse_iso_datetime_utc(job.get("finished_at") or job.get("started_at"))
            finished.append((timestamp or datetime.min.replace(tzinfo=timezone.utc), job_id))

        finished.sort(reverse=True)
        retained = {job_id for _timestamp, job_id in finished[:_FINISHED_JOB_RETENTION_COUNT]}
        for timestamp, job_id in finished:
            if job_id not in retained or timestamp < cutoff:
                self._jobs.pop(job_id, None)

    def _persist_jobs_locked(self) -> None:
        self._prune_finished_jobs_locked()
        payload = {
            "queue_processing_paused": bool(self._queue_processing_paused),
            "jobs": [
                snap
                for snap in (self._serialize_job_for_persistence(job) for job in self._jobs.values())
                if snap is not None
            ],
        }

        try:
            self._write_jobs_state_file_atomic(self._jobs_state_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Failed to persist queued jobs: {exc}")
            return

        try:
            self._write_jobs_state_file_atomic(self._jobs_state_backup_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Failed to update queued jobs backup state: {exc}")

    def _persist_runtime_checkpoint(self) -> None:
        """Persist an active job at a safe item boundary for crash recovery."""
        with self._lock:
            self._persist_jobs_locked()

    def _write_jobs_state_file_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _load_jobs_state_payload_locked(self) -> Optional[dict[str, Any]]:
        candidates = [
            ("primary", self._jobs_state_path),
            ("backup", self._jobs_state_backup_path),
        ]

        for label, path in candidates:
            if not path.exists():
                continue
            try:
                raw = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning(f"Could not read {label} queued jobs state from {path}: {exc}")
                continue

            if not raw:
                logger.warning(f"Ignoring empty {label} queued jobs state file at {path}")
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning(f"Ignoring corrupt {label} queued jobs state file at {path}: {exc}")
                continue

            if not isinstance(data, dict):
                logger.warning(f"Ignoring malformed {label} queued jobs state file at {path}")
                continue

            if label == "backup":
                try:
                    self._write_jobs_state_file_atomic(self._jobs_state_path, data)
                except (OSError, TypeError, ValueError) as exc:
                    logger.warning(f"Failed to restore primary queued jobs state from backup: {exc}")
            return data

        return None

    def _build_restored_terminal_job(
        self,
        row: dict[str, Any],
        *,
        job_id: str,
        category: str,
        started_at: str,
        created_at: str,
        display_name: str,
        persisted_status: str,
        priority: int,
    ) -> dict[str, Any]:
        """Rebuild a finished job's state dict from its persisted row."""
        terminal_job: dict[str, Any] = JobState(
            job_id=job_id,
            category=category,
            status=persisted_status,
            started_at=started_at,
            created_at=created_at,
            progress=str(row.get("progress") or ""),
            progress_percent=int(row.get("progress_percent") or 0),
            test_mode=bool(row.get("test_mode", False)),
            display_name=display_name,
            priority=priority,
            attempt_count=int(row.get("attempt_count") or 0),
            retry_of=str(row.get("retry_of") or "") or None,
            retried_as=str(row.get("retried_as") or "") or None,
            retry_eligible=bool(row.get("retry_eligible", False)),
            last_error=str(row.get("last_error") or "") or None,
            events=list(row.get("events") or [])[-30:],
        ).model_dump()
        terminal_job.update(
            {
                "source": self._normalize_job_source(row.get("source")),
                "job_type": str(row.get("job_type") or "processing"),
                "finished_at": str(row.get("finished_at") or started_at),
                "items_processed": int(row.get("items_processed") or 0),
                "items_total": int(row.get("items_total") or 0),
                "items_skipped": int(row.get("items_skipped") or 0),
                "total_bytes": int(row.get("total_bytes") or 0),
                "summary": dict(row.get("summary") or {}),
            }
        )
        retry_request = row.get("retry_request")
        if isinstance(retry_request, dict) and retry_request:
            terminal_job["_retry_request"] = retry_request
        return terminal_job

    def _build_restored_queued_job(
        self,
        row: dict[str, Any],
        *,
        job_id: str,
        category: str,
        started_at: str,
        created_at: str,
        display_name: str,
        run_after: Any,
        priority: int,
        persisted_status: str,
        kwargs: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Rebuild a still-pending job's state dict from its persisted row.

        Returns the job dict and whether it was manually stopped (the caller uses that
        to re-pause the queue).
        """
        restored_manual_stop = False
        restored_paused = persisted_status == "paused"
        progress = str(row.get("progress") or "")
        if persisted_status == "stopped":
            progress = "Recovered after restart - waiting for queue resume."
            restored_manual_stop = True
        elif restored_paused:
            # A job the user paused stays paused; only Resume restarts it.
            progress = "Recovered after restart - paused."
        elif persisted_status in {"running", "stopping"}:
            progress = "Recovered after restart - re-queued."
        elif not progress:
            progress = "Recovered after restart - queued."

        job: dict[str, Any] = JobState(
            job_id=job_id,
            category=category,
            status="paused" if restored_paused else "queued",
            started_at=started_at,
            created_at=created_at,
            progress=progress,
            speed=None,
            eta=None,
            current_stage="PAUSED" if restored_paused else "QUEUED",
            test_mode=bool(row.get("test_mode", False)),
            display_name=display_name,
            run_after=run_after,
            priority=priority,
            attempt_count=int(row.get("attempt_count") or 0),
            retry_of=str(row.get("retry_of") or "") or None,
            retried_as=str(row.get("retried_as") or "") or None,
            events=list(row.get("events") or [])[-30:],
        ).model_dump()
        if not display_name:
            job.pop("display_name", None)
        if not run_after:
            job.pop("run_after", None)
        job["source"] = self._normalize_job_source(row.get("source"))
        job["_kwargs"] = kwargs
        if restored_paused:
            # No worker thread exists for it; resume re-queues it for a fresh start.
            job["pause_requested"] = True
            job["_restored_paused"] = True
        retry_request = row.get("retry_request")
        if isinstance(retry_request, dict) and retry_request:
            job["_retry_request"] = retry_request
        self._set_job_target_paths(job, row.get("paths"))

        prior_processed = int(row.get("items_processed") or 0)
        prior_total = int(row.get("items_total") or 0)
        if prior_processed > 0 or prior_total > 0:
            job["_resume_items_processed"] = prior_processed
            job["_resume_items_total"] = prior_total
            job["_resume_items_skipped"] = int(row.get("items_skipped") or 0)

        return job, restored_manual_stop

    def _restore_jobs_from_disk_locked(self) -> None:
        data = self._load_jobs_state_payload_locked()
        if data is None:
            return

        if isinstance(data, dict):
            self._queue_processing_paused = bool(data.get("queue_processing_paused", False))
            rows = data.get("jobs", [])
        else:
            rows = []
        if not isinstance(rows, list):
            return

        recovered = 0
        recovered_finished = 0
        restored_manual_stop = False
        for row in rows:
            if not isinstance(row, dict):
                continue

            job_id = str(row.get("job_id") or "").strip() or str(uuid.uuid4())[:8]
            category = str(row.get("category") or "misc")
            started_at = str(row.get("started_at") or datetime.now(timezone.utc).isoformat())
            created_at = str(row.get("created_at") or started_at)
            persisted_status = str(row.get("status") or "queued")

            kwargs = row.get("kwargs")
            if not isinstance(kwargs, dict):
                kwargs = {}

            display_name = self._normalize_job_name(row.get("display_name"))
            run_after = self._normalize_run_after(row.get("run_after"))
            priority = int(row.get("priority") or 0)

            persisted_paths = self._normalize_paths(row.get("paths"))
            persisted_finished = persisted_status in _TERMINAL_JOB_STATUSES or (
                persisted_status == "stopped" and not persisted_paths
            )
            if persisted_finished:
                self._jobs[job_id] = self._build_restored_terminal_job(
                    row,
                    job_id=job_id,
                    category=category,
                    started_at=started_at,
                    created_at=created_at,
                    display_name=display_name,
                    persisted_status=persisted_status,
                    priority=priority,
                )
                recovered_finished += 1
                continue

            job, is_manual_stop = self._build_restored_queued_job(
                row,
                job_id=job_id,
                category=category,
                started_at=started_at,
                created_at=created_at,
                display_name=display_name,
                run_after=run_after,
                priority=priority,
                persisted_status=persisted_status,
                kwargs=kwargs,
            )
            if is_manual_stop:
                restored_manual_stop = True
            self._jobs[job_id] = job
            recovered += 1

        if restored_manual_stop:
            self._queue_processing_paused = True

        recovered_finished += self._import_legacy_finished_jobs_locked()

        if recovered:
            log_info(f"Recovered {recovered} queued job(s) after restart.")
        if recovered_finished:
            log_info(f"Recovered {recovered_finished} recently finished job(s) after restart.")
        self._persist_jobs_locked()

    def _import_legacy_finished_jobs_locked(self) -> int:
        """One-time import of the server's separate finished-job file.

        The client's server kept finished jobs in job_finished_state.json (+ .bak).
        Rows not already restored are rebuilt as terminal jobs, the caller's
        persist writes them into the main state file, and the legacy file is
        renamed to *.migrated so the import runs once.
        """
        legacy_path = self._jobs_state_path.with_name("job_finished_state.json")
        legacy_backup = legacy_path.with_name("job_finished_state.json.bak")
        data: Optional[dict[str, Any]] = None
        for candidate in (legacy_path, legacy_backup):
            if not candidate.exists():
                continue
            try:
                loaded = json.loads(candidate.read_text(encoding="utf-8").strip() or "null")
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning(f"Ignoring unreadable legacy finished jobs file {candidate}: {exc}")
                continue
            if isinstance(loaded, dict):
                data = loaded
                break
        if data is None:
            return 0

        imported = 0
        rows = data.get("jobs", [])
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            job_id = str(row.get("job_id") or "").strip()
            status = str(row.get("status") or "")
            if not job_id or job_id in self._jobs or status not in {"completed", "failed", "cancelled", "stopped"}:
                continue
            started_at = str(row.get("started_at") or datetime.now(timezone.utc).isoformat())
            self._jobs[job_id] = self._build_restored_terminal_job(
                row,
                job_id=job_id,
                category=str(row.get("category") or "misc"),
                started_at=started_at,
                created_at=str(row.get("created_at") or started_at),
                display_name=self._normalize_job_name(row.get("display_name")),
                persisted_status=status,
                priority=int(row.get("priority") or 0),
            )
            imported += 1

        for candidate in (legacy_path, legacy_backup):
            if not candidate.exists():
                continue
            try:
                candidate.replace(candidate.with_name(candidate.name + ".migrated"))
            except OSError as exc:
                logger.warning(f"Could not rename legacy finished jobs file {candidate}: {exc}")
        return imported

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
        self._preserve_stopped_processing_job_locked(job)

        if job.get("source_monitor_id"):
            usenet_stream.record_stream_monitor_job(str(job.get("source_monitor_id")), job)

        if remove_from_active or clear_after_stop:
            self._jobs.pop(job_id, None)

