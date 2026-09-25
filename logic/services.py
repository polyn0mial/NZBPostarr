"""
📦 NZBPostarr - Services Module
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Unified service layer for orchestration and job state.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from core import config as config_mod
from core import proc
from core.db import history as db_history
from core.db import job_history as db_job_history
from core.db import stats as db_stats
from core.config import get_config
from logic.jobs.context import reset_thread_job, set_thread_job
from logic.pipeline import runner
from logic.stream import manifest as stream_manifest, monitors as stream_monitors, repost as stream_repost
from logic.pending.index import get_pending_index_manager
from logic.pending.view import build_dashboard_summary
from logic.jobs.models import ProcessingJobRequest, StreamJobRequest
from logic.jobs.requests import resolve_force_flag
from logic.jobs.store import is_resumable_stopped
from logic.queueing import QueueServiceMixin

_WEB_CONSOLE_SUPPRESSED_PREFIXES = ("[Queue Update] Found ",)


class ConsoleBuffer:
    """In-memory log buffer for the web terminal, wired as a loguru sink.

    Instead of manual ``.log()`` calls this class exposes a ``sink``
    method that can be passed directly to ``logger.add()``.  The
    special PROGRESS-collapse behaviour (consecutive PROGRESS lines
    replace the previous one) is preserved.
    """

    def __init__(self, max_lines: int = 2000):
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._sequence = 0
        self._handler_id: Optional[int] = None

    # ── loguru sink ──────────────────────────────────────────────────

    def sink(self, message: Any) -> None:
        """Loguru sink callable - auto-filters based on verbose config."""
        level_name = message.record["level"].name
        level_no = message.record["level"].no
        log_message = message.record["message"]

        # Avoid get_config() during early startup/config load. Calling get_config
        # from the sink can deadlock if config loading itself emits logs.
        conf = getattr(config_mod, "_GLOBAL_CONFIG", None)
        verbose_enabled = bool(getattr(conf, "verbose", False)) if conf is not None else False

        # Filter console noise if verbose is off
        if not verbose_enabled and level_no < 20:
            return

        if log_message.startswith(_WEB_CONSOLE_SUPPRESSED_PREFIXES):
            return

        self._write(log_message, level_name)

    def _write(self, message: str, level: str = "INFO") -> None:
        """Append a log entry (with PROGRESS collapse)."""
        with self._lock:
            if level == "PROGRESS" and self._buffer and self._buffer[-1]["level"] == "PROGRESS":
                self._buffer[-1].update(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "msg": message,
                    }
                )
                self._sequence += 1
                self._buffer[-1]["seq"] = self._sequence
            else:
                self._sequence += 1
                self._buffer.append(
                    {
                        "seq": self._sequence,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "level": level,
                        "msg": message,
                    }
                )

    # Keep .log() as a thin alias for any remaining callers
    log = _write

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def get_logs(self, after_seq: int = 0, limit: Optional[int] = None) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            logs = [entry for entry in self._buffer if entry["seq"] > after_seq]
            if not logs:
                return [], self._sequence

            # If we are limiting, only return the oldest ones after the requested seq
            # to ensure the client can eventually catch up without missing data.
            if limit and len(logs) > limit:
                logs = logs[:limit]

            # The new after_seq should be the sequence of the LAST log we are returning
            new_after = logs[-1]["seq"]
            return logs, new_after

    def get_tail(self, count: int = 100) -> tuple[list[dict[str, Any]], int]:
        """Return the most recent *count* log entries (newest-last).

        Used on initial dashboard load so the client jumps straight to
        the end of the buffer instead of replaying from the beginning.
        """
        with self._lock:
            buf = list(self._buffer)
            tail = buf[-count:] if count and len(buf) > count else buf
            return tail, self._sequence

    # ── Registration helpers ─────────────────────────────────────────

    def install(self) -> None:
        """Register this buffer as a loguru sink (idempotent)."""
        if self._handler_id is None:
            # Level 1 captures everything including custom VERBOSE (5)
            self._handler_id = logger.add(self.sink, level=1)

console = ConsoleBuffer()
console.install()


def init_app() -> None:
    """Shared initialization for both WebUI and headless modes.

    Initializes database, indexer registry, console buffer, and
    runs a background cleanup of stale tmp data.
    """
    from core.db.schema import init_database
    from core.indexers.registry import get_registry
    from logic.pipeline.cleanup import run_global_purge
    from core.scheduler import get_scheduler

    logger.info("Initializing database...")
    init_database()

    logger.info("Loading indexer registry...")
    get_registry()

    # Clean up stale tmp data via scheduler (one-shot, runs once at startup)
    sched = get_scheduler()
    sched.add_job(run_global_purge, id="startup_purge", replace_existing=True)


class UploadService(QueueServiceMixin):
    _instance: Optional["UploadService"] = None

    def __new__(cls) -> "UploadService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "_initialized"):
            self._lock = threading.Lock()
            self._initialized = True
            self._stats_cache: Dict[str, Any] = {}
            self._stats_cache_ts: float = 0.0
            self._initialize_queue_state()

    @staticmethod
    def _resolve_force_flag(
        *,
        enable_duplicate_check: Optional[bool] = None,
        force: Optional[bool] = None,
        test_mode: bool = False,
    ) -> bool:
        return resolve_force_flag(enable_duplicate_check=enable_duplicate_check, force=force, test_mode=test_mode)

    def _get_statistics_cached(self, now: float, ttl_s: float) -> dict[str, Any]:
        if self._stats_cache and (now - self._stats_cache_ts) < ttl_s:
            return self._stats_cache
        stats = self.get_statistics()
        self._stats_cache = stats
        self._stats_cache_ts = now
        return stats

    def invalidate_statistics_cache(self) -> None:
        """Drop cached upload stats so the next dashboard request reads fresh values."""
        with self._lock:
            self._stats_cache = {}
            self._stats_cache_ts = 0.0

    def _update_job_runtime_state(self, job: dict[str, Any], **updates: Any) -> None:
        with self._lock:
            job.update(updates)
            self._persist_jobs_locked()

    def _mark_job_failed(self, job: dict[str, Any], exc: Exception, *, log_prefix: str) -> None:
        logger.exception(f"{log_prefix}: {exc}")
        self._update_job_runtime_state(job, status="failed", progress=f"Error: {exc}")

    def _mark_processing_job_started(self, job: dict[str, Any], request: ProcessingJobRequest) -> None:
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
        self._update_job_runtime_state(job, **updates)

    def _mark_stream_job_started(self, job: dict[str, Any], request: StreamJobRequest) -> None:
        self._update_job_runtime_state(
            job,
            test_mode=bool(request.test_mode),
            items_total=1,
            items_processed=0,
            items_skipped=0,
            current_stage="PROBING SOURCE NZB",
        )

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Return dashboard summary derived from the pending-index snapshot and live stats."""
        conf = get_config()
        stats_ttl = float(max(1.0, min(5.0, getattr(conf, "ui_refresh_seconds", 2) or 2)))
        stats = self._get_statistics_cached(time.time(), stats_ttl)
        pending_state = get_pending_index_manager().get_state()
        if not isinstance(pending_state, dict) or pending_state.get("snapshot") is None:
            get_pending_index_manager().request_refresh(reason="dashboard-summary")
        return build_dashboard_summary(conf, stats, pending_state)

    def _execute_processing_job(self, job: dict[str, Any], request: ProcessingJobRequest) -> None:
        """Execute runner.run_job with shared force/error semantics.

        The thread-job binding is restored on exit so callers that run this
        synchronously (CLI, tests with ``ImmediateThread``) don't leak the
        binding into the next operation.
        """
        token = set_thread_job(job)

        try:
            self._mark_processing_job_started(job, request)
            if bool(job.get("has_explicit_paths")) and not request.paths:
                reason = "No valid items remained after queue filtering"
                logger.error(f"[QUEUE-START] {reason}")
                self._update_job_runtime_state(
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

            runner.run_job(
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
            self._mark_job_failed(job, e, log_prefix="JOB CRASHED")
        finally:
            reset_thread_job(token)

    def _execute_usenet_stream_job(self, job: dict[str, Any], request: StreamJobRequest) -> None:
        """Execute the NZB stream/repost job.

        See ``_execute_processing_job`` for the rationale behind the
        ``set_thread_job`` / ``reset_thread_job`` pairing.
        """
        token = set_thread_job(job)

        try:
            self._mark_stream_job_started(job, request)
            test_v = bool(request.test_mode)

            force_v = resolve_force_flag(
                enable_duplicate_check=request.enable_duplicate_check,
                test_mode=test_v,
            )

            if not request.source_path:
                raise RuntimeError("Stream source path is missing")

            manifest_path = stream_manifest.build_stream_manifest_path(
                request.release_name or Path(str(request.source_path)).stem
            )
            with self._lock:
                cleanup_paths = self._normalize_paths(job.get("cleanup_paths"))
                cleanup_paths.append(str(manifest_path))
                job["cleanup_paths"] = list(dict.fromkeys(cleanup_paths))
                self._persist_jobs_locked()

            stream_repost.stream_nzb_upload(
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
            self._mark_job_failed(job, e, log_prefix="STREAM JOB CRASHED")
            if job.get("source_monitor_id"):
                stream_monitors.record_stream_monitor_job(str(job.get("source_monitor_id")), job)
        finally:
            reset_thread_job(token)

    def get_statistics(self) -> dict[str, Any]:
        """Retrieve aggregated upload statistics from the database."""
        return db_stats.get_detailed_stats()


def get_upload_service() -> UploadService:
    """Dependency provider for the UploadService singleton."""
    return UploadService()


def build_queue_snapshot(service: "UploadService") -> dict[str, Any]:
    """Bucket active jobs into running/queued/finished with the queue control state.

    Single source of truth for this classification: both the GET /queue route and
    the headless `queue status` command call it, so adding a job status cannot
    make the WebUI and the CLI disagree.
    """
    all_jobs = service.get_active_jobs(compact=True)

    running = [j for j in all_jobs if j.get("status") in ("running", "stopping", "paused")]
    queued = sorted(
        [j for j in all_jobs if j.get("status") == "queued" or is_resumable_stopped(j)],
        key=lambda j: (-int(j.get("priority") or 0), j.get("started_at", "")),
    )
    finished = [
        j
        for j in all_jobs
        if j.get("status") in ("completed", "failed", "cancelled")
        or (j.get("status") == "stopped" and not is_resumable_stopped(j))
    ]
    return {
        "running": running,
        "queued": queued,
        "finished": finished,
        "control": service.get_queue_control_state(),
        "counts": {
            "running": len(running),
            "queued": len(queued),
            "finished": len(finished),
        },
    }


def get_recent_errors(limit: int = 25) -> list[dict[str, Any]]:
    """Recent failures, merging failed jobs and failed per-indexer uploads.

    Kept here rather than in each caller so the CLI, the HTTP API and the MCP
    endpoint all report the same thing.
    """
    capped = max(1, min(int(limit), 200))
    errors: list[dict[str, Any]] = []

    for job in db_job_history.get_job_history(limit=capped):
        message = job.get("error_message")
        if not message:
            continue
        errors.append(
            {
                "kind": "job",
                "job_id": job.get("job_id"),
                "category": job.get("category"),
                "when": job.get("completed_at") or job.get("started_at"),
                "error": str(message),
            }
        )

    # get_recent_uploads returns a paginated envelope; each item carries one
    # "destinations" entry per indexer, and that is where a failure is recorded.
    page = db_history.get_recent_uploads(limit=capped)
    for row in page.get("items", []) if isinstance(page, dict) else []:
        for destination in row.get("destinations", []) or []:
            if str(destination.get("status", "")).lower() not in {"failed", "error"}:
                continue
            errors.append(
                {
                    "kind": "upload",
                    "item_name": row.get("item_name"),
                    "indexer": destination.get("id"),
                    "when": destination.get("uploaded_at"),
                    "error": str(destination.get("error") or "").strip() or "unknown error",
                }
            )

    errors.sort(key=lambda entry: str(entry.get("when") or ""), reverse=True)
    return errors[:capped]


# core.proc records each job's tool processes here so stop/clear can terminate them.
proc.set_process_registry(UploadService)
