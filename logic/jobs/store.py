"""Job persistence: the job_queue_state.json file (+ .bak), its rows, and restore after restart."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from core.fs import atomic_write_text
from core.logging import log_info
from logic.jobs.models import (
    _FINISHED_JOB_RETENTION_COUNT,
    _FINISHED_JOB_RETENTION_DAYS,
    _TERMINAL_JOB_STATUSES,
    JobState,
    job_target_paths,
    normalize_job_name,
    normalize_job_path_identity,
    normalize_job_source,
    normalize_paths,
    normalize_run_after,
    parse_iso_datetime_utc,
    set_job_target_paths,
)


def is_resumable_stopped(job: dict[str, Any]) -> bool:
    """The one resumable-stopped rule, for engine jobs and compact API snapshots alike.

    A stopped processing job resumes on queue resume unless it had explicit paths and none
    remain. Compact snapshots carry has_explicit_paths/target_path_count instead of paths.
    """
    if str(job.get("status") or "") != "stopped":
        return False
    if str(job.get("job_type") or "processing") != "processing":
        return False
    if not job.get("has_explicit_paths"):
        return True
    return bool(
        job_target_paths(job)
        or str(job.get("_current_item_path") or "").strip()
        or int(job.get("target_path_count") or 0)
    )


def preserve_stopped_processing_job(job: dict[str, Any]) -> bool:
    """Keep a resumable stopped job's remaining paths (incl. the interrupted item); False if not resumable."""
    if str(job.get("status") or "") != "stopped":
        return False
    if str(job.get("job_type") or "processing") != "processing":
        return False

    has_explicit_paths = bool(job.get("has_explicit_paths"))
    resumable_paths = job_target_paths(job)
    current_path = str(job.pop("_current_item_path", "") or "").strip()
    if current_path:
        current_identity = normalize_job_path_identity(current_path)
        existing = {normalize_job_path_identity(path) for path in resumable_paths}
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


def _dedupe_recovery_paths(job: dict[str, Any]) -> list[str]:
    """Merge the active item, in-flight items, and remaining targets into one deduped path order."""
    recovery_paths = [
        str(job.get("_current_item_path") or "").strip(),
        *normalize_paths(job.get("_inflight_item_paths")),
        *job_target_paths(job),
    ]
    deduped: list[str] = []
    seen_path_identities: set[str] = set()
    for recovery_path in recovery_paths:
        identity = normalize_job_path_identity(recovery_path)
        if not identity or identity in seen_path_identities:
            continue
        seen_path_identities.add(identity)
        deduped.append(recovery_path)
    return deduped


def _build_job_persistence_payload(
    job: dict[str, Any],
    job_id: str,
    status: str,
    kwargs: dict[str, Any],
    paths: list[str],
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "category": str(job.get("category", "misc")),
        "source": normalize_job_source(job.get("source")),
        "status": status,
        "started_at": str(job.get("started_at") or datetime.now(timezone.utc).isoformat()),
        "created_at": str(job.get("created_at") or job.get("started_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
        "progress": str(job.get("progress") or ""),
        "progress_percent": int(job.get("progress_percent") or 0),
        "test_mode": bool(job.get("test_mode", False)),
        "display_name": normalize_job_name(job.get("display_name")),
        "run_after": normalize_run_after(job.get("run_after")),
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


def serialize_job(job: dict[str, Any]) -> Optional[dict[str, Any]]:
    """One persisted row for a job, or None when the job is not persisted."""
    status = str(job.get("status", "queued"))
    if status == "stopped":
        preserve_stopped_processing_job(job)
    elif status not in {"queued", "running", "paused", "stopping", *_TERMINAL_JOB_STATUSES}:
        return None

    kwargs = job.get("_kwargs")
    if not isinstance(kwargs, dict):
        kwargs = {}

    job_id = str(job.get("job_id", "")).strip()
    if not job_id:
        return None

    paths = _dedupe_recovery_paths(job)

    payload = _build_job_persistence_payload(job, job_id, status, kwargs, paths)
    if status in _TERMINAL_JOB_STATUSES or (status == "stopped" and not preserve_stopped_processing_job(job)):
        payload["kwargs"] = {}
        payload["paths"] = []
    return payload


def prune_finished_jobs(jobs: dict[str, dict[str, Any]]) -> None:
    """Bounded finished-job retention: newest N, none older than the retention window."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_FINISHED_JOB_RETENTION_DAYS)
    finished: list[tuple[datetime, str]] = []
    for job_id, job in jobs.items():
        status = str(job.get("status") or "")
        if status not in _TERMINAL_JOB_STATUSES and not (
            status == "stopped" and not preserve_stopped_processing_job(job)
        ):
            continue
        timestamp = parse_iso_datetime_utc(job.get("finished_at") or job.get("started_at"))
        finished.append((timestamp or datetime.min.replace(tzinfo=timezone.utc), job_id))

    finished.sort(reverse=True)
    retained = {job_id for _timestamp, job_id in finished[:_FINISHED_JOB_RETENTION_COUNT]}
    for timestamp, job_id in finished:
        if job_id not in retained or timestamp < cutoff:
            jobs.pop(job_id, None)


def build_restored_terminal_job(
    row: dict[str, Any],
    *,
    job_id: str,
    category: str,
    started_at: str,
    created_at: str,
    display_name: Optional[str],
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
            "source": normalize_job_source(row.get("source")),
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


def build_restored_queued_job(
    row: dict[str, Any],
    *,
    job_id: str,
    category: str,
    started_at: str,
    created_at: str,
    display_name: Optional[str],
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
    job["source"] = normalize_job_source(row.get("source"))
    job["_kwargs"] = kwargs
    if restored_paused:
        # No worker thread exists for it; resume re-queues it for a fresh start.
        job["pause_requested"] = True
        job["_restored_paused"] = True
    retry_request = row.get("retry_request")
    if isinstance(retry_request, dict) and retry_request:
        job["_retry_request"] = retry_request
    set_job_target_paths(job, row.get("paths"))

    prior_processed = int(row.get("items_processed") or 0)
    prior_total = int(row.get("items_total") or 0)
    if prior_processed > 0 or prior_total > 0:
        job["_resume_items_processed"] = prior_processed
        job["_resume_items_total"] = prior_total
        job["_resume_items_skipped"] = int(row.get("items_skipped") or 0)

    return job, restored_manual_stop


class JobStore:
    """The job-state file pair: primary job_queue_state.json and its .bak copy."""

    def __init__(self, state_path: Path, backup_path: Path) -> None:
        self.state_path = state_path
        self.backup_path = backup_path

    @staticmethod
    def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def persist(self, jobs: dict[str, dict[str, Any]], *, queue_paused: bool) -> None:
        """Prune finished jobs, then write every persisted row to the primary file and the backup."""
        prune_finished_jobs(jobs)
        payload = {
            "queue_processing_paused": bool(queue_paused),
            "jobs": [snap for snap in (serialize_job(job) for job in jobs.values()) if snap is not None],
        }

        try:
            self._write_atomic(self.state_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Failed to persist queued jobs: {exc}")
            return

        try:
            self._write_atomic(self.backup_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Failed to update queued jobs backup state: {exc}")

    def load(self) -> Optional[dict[str, Any]]:
        """Read the primary file, falling back to (and restoring from) the backup."""
        candidates = [
            ("primary", self.state_path),
            ("backup", self.backup_path),
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
                    self._write_atomic(self.state_path, data)
                except (OSError, TypeError, ValueError) as exc:
                    logger.warning(f"Failed to restore primary queued jobs state from backup: {exc}")
            return data

        return None

    def restore(self, jobs: dict[str, dict[str, Any]], *, queue_paused: bool) -> bool:
        """Rebuild persisted jobs into ``jobs`` and persist the result; returns the queue-paused flag."""
        data = self.load()
        if data is None:
            return queue_paused

        queue_paused = bool(data.get("queue_processing_paused", False))
        rows = data.get("jobs", [])
        if not isinstance(rows, list):
            return queue_paused

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

            display_name = normalize_job_name(row.get("display_name"))
            run_after = normalize_run_after(row.get("run_after"))
            priority = int(row.get("priority") or 0)

            persisted_paths = normalize_paths(row.get("paths"))
            persisted_finished = persisted_status in _TERMINAL_JOB_STATUSES or (
                persisted_status == "stopped" and not persisted_paths
            )
            if persisted_finished:
                jobs[job_id] = build_restored_terminal_job(
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

            job, is_manual_stop = build_restored_queued_job(
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
            jobs[job_id] = job
            recovered += 1

        if restored_manual_stop:
            queue_paused = True

        recovered_finished += self.import_legacy_finished(jobs)

        if recovered:
            log_info(f"Recovered {recovered} queued job(s) after restart.")
        if recovered_finished:
            log_info(f"Recovered {recovered_finished} recently finished job(s) after restart.")
        self.persist(jobs, queue_paused=queue_paused)
        return queue_paused

    def import_legacy_finished(self, jobs: dict[str, dict[str, Any]]) -> int:
        """One-time import of the server's separate finished-job file.

        The client's server kept finished jobs in job_finished_state.json (+ .bak).
        Rows not already restored are rebuilt as terminal jobs, the caller's
        persist writes them into the main state file, and the legacy file is
        renamed to *.migrated so the import runs once.
        """
        legacy_path = self.state_path.with_name("job_finished_state.json")
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
            if not job_id or job_id in jobs or status not in {"completed", "failed", "cancelled", "stopped"}:
                continue
            started_at = str(row.get("started_at") or datetime.now(timezone.utc).isoformat())
            jobs[job_id] = build_restored_terminal_job(
                row,
                job_id=job_id,
                category=str(row.get("category") or "misc"),
                started_at=started_at,
                created_at=str(row.get("created_at") or started_at),
                display_name=normalize_job_name(row.get("display_name")),
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
