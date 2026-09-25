# Auto-split mixin from queueing.py - verbatim method bodies.
# W11-B10: revalidation rules live in logic/jobs/revalidate.py.

from logic.jobs.revalidate import revalidated_plan, revalidation_candidates, revalidation_targets
from logic.queueing_base import (
    Any, Optional, Path, database, datetime, log_info, logger, threading, time, timedelta, timezone, usenet_stream,
)

class _QueueServiceMixinPart4:
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
        source = self._normalize_job_source(job.get("source"))
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
        base_name = self._normalize_job_name(release_name) or Path(stream_source_name).stem
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
                    due_at = self._parse_iso_datetime_utc(job.get("run_after"))
                    if due_at and due_at > now_utc:
                        continue
                    candidates.append(job)
                if not candidates:
                    return
                next_job = min(candidates, key=lane_order)

        log_info(
            f"Dequeuing job {next_job['job_id']} ({next_job['category']}, source={self._normalize_job_source(next_job.get('source'))})..."
        )
        self._launch_job(next_job)

    def _queue_scheduler_loop(self) -> None:
        while not self._queue_scheduler_stop.wait(15):
            try:
                self._try_start_queued()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Queue scheduler loop error: {exc}")

    def get_active_jobs(self, *, compact: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            exclude = {
                "_kwargs",
                "_retry_request",
                "_paths",
                "_current_item_path",
                "_inflight_item_paths",
                "_current_prepare_tmp",
                "_clear_after_stop",
                "_hide_while_stopping",
                "_pause_ack_callback",
                "_persist_callback",
                "_removed_item_paths",
                "_restored_paused",
            }
            visible_jobs = [
                job
                for job in self._jobs.values()
                if not (job.get("_hide_while_stopping") and str(job.get("status") or "") == "stopping")
            ]
            snapshots: list[dict[str, Any]] = []
            for job in visible_jobs:
                snapshot = {k: v for k, v in job.items() if k not in exclude}
                if compact:
                    target_paths = self._get_job_target_paths(job)
                    snapshot.pop("target_paths", None)
                    snapshot["has_explicit_paths"] = bool(target_paths)
                    snapshot["target_path_count"] = len(target_paths)
                    events = snapshot.get("events")
                    if isinstance(events, list):
                        snapshot["events"] = events[-3:]
                snapshots.append(snapshot)
            return snapshots

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
            paths = self._normalize_paths(retry_request.get("paths"))
            kwargs = dict(retry_kwargs)
            display_name = self._normalize_job_name(job.get("display_name")) or self._job_category_label(category)
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

    def _snapshot_revalidation_targets(self, include_paused: bool) -> list[dict[str, Any]]:
        return revalidation_targets(self._jobs.values(), include_paused)

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
                self._set_job_target_paths(job, new_paths)
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
            snapshots = self._snapshot_revalidation_targets(include_paused)

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
                prepared = self._prepare_queue_start_items(candidates)
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

                from core.scheduler import get_scheduler

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
                or (job.get("status") == "stopped" and not self._is_resumable_stopped_job_locked(job))
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
                if status == "queued" or self._is_resumable_stopped_job_locked(job):
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
                if not self._is_resumable_stopped_job_locked(job):
                    return False
            elif status != "queued":
                return False
            queued = [
                queued_job
                for queued_job in self._jobs.values()
                if queued_job["status"] == "queued" or self._is_resumable_stopped_job_locked(queued_job)
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

    def get_queued_job_items(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            status = str(job.get("status") or "")
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return None
            elif status != "queued":
                return None

            items: list[dict[str, Any]] = []
            for idx, path in enumerate(self._get_job_target_paths(job), start=1):
                text = str(path)
                items.append({"index": idx, "path": text, "name": Path(text).name or text})
            return items

    def get_active_job_items(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        """Return remaining explicit paths for an active job on demand."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or str(job.get("status") or "") not in {"running", "paused", "stopping"}:
                return None
            paths = self._get_job_target_paths(job)
            if not paths:
                paths = self._normalize_paths(job.get("_snapshot_paths"))
            items: list[dict[str, Any]] = []
            for idx, path in enumerate(paths, start=1):
                text = str(path)
                items.append({"index": idx, "path": text, "name": Path(text).name or text})
            return items

    def get_finished_job_items(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        """Return item paths for a recently finished job (current session only)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            status = str(job.get("status") or "")
            if status not in {"completed", "stopped", "failed", "cancelled"}:
                return None
            paths = self._normalize_paths(job.get("_completed_paths"))
            return [
                {"index": index, "path": str(path), "name": Path(str(path)).name or str(path)}
                for index, path in enumerate(paths, 1)
            ]

    def reorder_queued_job_items(self, job_id: str, ordered_paths: list[str]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = str(job.get("status") or "")
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return False
            elif status != "queued":
                return False

            current = self._get_job_target_paths(job)
            new_order = self._match_job_paths_by_identity(current, ordered_paths)
            if new_order is None:
                return False

            self._set_job_target_paths(job, new_order)
            self._persist_jobs_locked()
            return True

    def reorder_active_job_items(self, job_id: str, ordered_paths: list[str]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") not in ("running", "paused"):
                return False

            current = self._get_job_target_paths(job)
            new_order = self._match_job_paths_by_identity(current, ordered_paths)
            if new_order is None:
                return False

            self._set_job_target_paths(job, new_order)
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
            target_identity = self._normalize_job_path_identity(path)
            if not target_identity:
                return False
            current = self._get_job_target_paths(job)
            removed = [p for p in current if self._normalize_job_path_identity(p) == target_identity]
            if not removed:
                return False
            updated = [p for p in current if self._normalize_job_path_identity(p) != target_identity]
            job["_removed_item_paths"] = [*self._normalize_paths(job.get("_removed_item_paths")), *removed]
            self._set_job_target_paths(job, updated)
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
                if not self._is_resumable_stopped_job_locked(job):
                    return False
            elif status != "queued":
                return False

            current = self._get_job_target_paths(job)
            target_identity = self._normalize_job_path_identity(path)
            if not target_identity:
                return False

            updated = [
                current_path
                for current_path in current
                if self._normalize_job_path_identity(current_path) != target_identity
            ]
            if len(updated) == len(current):
                return False
            if updated:
                self._set_job_target_paths(job, updated)
            else:
                self._jobs.pop(job_id, None)

            self._persist_jobs_locked()
            return True

    def get_queue_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._queue_items)

    def add_queue_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            added = database.db_add_queue_items(items)
            if not added:
                return []

            original_by_identity: dict[str, dict[str, Any]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                identity = self._normalize_job_path_identity(item.get("path"))
                if identity:
                    original_by_identity[identity] = dict(item)

            merged_added: list[dict[str, Any]] = []
            for row in added:
                merged = dict(row)
                identity = self._normalize_job_path_identity(row.get("path"))
                extras = dict(original_by_identity.get(identity, {}))
                extras.pop("id", None)
                extras.pop("added_at", None)
                merged.update(extras)
                merged_added.append(merged)

            self._queue_items.extend(merged_added)
        return merged_added

    def remove_queue_item(self, item_id: int) -> bool:
        with self._lock:
            removed = database.db_remove_queue_item(item_id)
            if removed:
                self._queue_items = [queue_item for queue_item in self._queue_items if queue_item["id"] != item_id]
        return removed

    def clear_queue_items(self) -> int:
        with self._lock:
            count = database.db_clear_queue()
            self._queue_items.clear()
        return count

    def reorder_queue_items(self, item_ids: list[int]) -> bool:
        with self._lock:
            id_map = {queue_item["id"]: queue_item for queue_item in self._queue_items}
            if set(item_ids) != set(id_map.keys()):
                return False
            ok = database.db_reorder_queue(item_ids)
            if ok:
                self._queue_items = [id_map[item_id] for item_id in item_ids]
        return ok

    def start_queue(self, **kwargs: Any) -> list[str]:
        return self.start_queue_with_details(**kwargs)["job_ids"]

    def _raise_no_runnable_queue_items(self, prepared: Any) -> None:
        skipped_reasons = [
            f"{self._queue_item_label(item, idx)} ({reason})"
            for idx, (item, reason) in enumerate(prepared.skipped_items, start=1)
        ]
        detail = "Queue start failed: no runnable staged items"
        if skipped_reasons:
            detail = f"{detail}. " + "; ".join(skipped_reasons[:5])
            if len(skipped_reasons) > 5:
                detail = f"{detail}; and {len(skipped_reasons) - 5} more"
        log_info(f"[QUEUE-START] {detail}", "ERROR")
        raise ValueError(detail)

