# Auto-split mixin from queueing.py - verbatim method bodies.

from logic.queueing_base import (
    Any, JobState, Optional, ProcessingJobRequest, database, datetime, log_info, logger, re, reset_thread_job, set_thread_job, time, timezone, uuid,
)

class _QueueServiceMixinPart5:
    def _remove_started_queue_items(self, runnable_ids: set[int], job_id: str) -> None:
        try:
            removed_count = database.db_remove_queue_items(sorted(runnable_ids))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[QUEUE-START] Job {job_id} started but staged-item cleanup failed: {exc}")
            return
        if removed_count != len(runnable_ids):
            logger.warning(
                f"[QUEUE-START] Expected to remove {len(runnable_ids)} staged item(s) after job creation, "
                f"but removed {removed_count}"
            )
        with self._lock:

            def _queue_item_id(value: Any) -> Optional[int]:
                text = str(value or "").strip()
                return int(text) if re.fullmatch(r"\d+", text) else None

            self._queue_items = [
                queue_item
                for queue_item in self._queue_items
                if _queue_item_id(queue_item.get("id")) not in runnable_ids
            ]

    def start_queue_with_details(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            if not self._queue_items:
                return {
                    "job_ids": [],
                    "jobs_created": 0,
                    "started_items": 0,
                    "skipped_items": 0,
                    "remaining_staged": 0,
                }
            items = list(self._queue_items)

        prepared = self._prepare_queue_start_items(items)
        logger.info(
            f"[QUEUE-START] staged_selected={len(items)} filtered_valid={len(prepared.runnable_items)} "
            f"excluded={len(prepared.skipped_items)}"
        )
        for index, (skipped_item, reason) in enumerate(prepared.skipped_items, start=1):
            log_info(
                f"[QUEUE-START] Skipping staged item '{self._queue_item_label(skipped_item, index)}': {reason}",
                "WARNING",
            )

        if not prepared.runnable_items:
            self._raise_no_runnable_queue_items(prepared)

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
            self._remove_started_queue_items(runnable_ids, job_id)
        remaining_staged = len(self.get_queue_items())
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
            self._execute_processing_job(
                job,
                ProcessingJobRequest(
                    category=category,
                    limit=limit,
                    skip_packs=skip_packs,
                    skip_episodes=skip_episodes,
                    test_mode=test_mode,
                    target_indexer_id=indexer_id,
                    paths=tuple(self._normalize_paths(paths)),
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

