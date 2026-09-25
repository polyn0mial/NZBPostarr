"""Read-only job views: queue snapshot, job item listings and recent errors.

The WebUI routes, the MCP endpoint and the headless CLI all read through here, so a
job status can never be classified differently by two callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Optional, Protocol

from core.db import history as db_history
from core.db import job_history as db_job_history
from logic.jobs.models import job_target_paths, normalize_paths
from logic.jobs.store import is_resumable_stopped, preserve_stopped_processing_job

if TYPE_CHECKING:
    from logic.jobs.engine import JobEngine

_SNAPSHOT_EXCLUDED_KEYS = frozenset(
    {
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
)


class QueueSource(Protocol):
    def get_active_jobs(self, *, compact: bool = False) -> list[dict[str, Any]]: ...

    def get_queue_control_state(self) -> dict[str, Any]: ...


def job_snapshots(jobs: Iterable[dict[str, Any]], *, compact: bool = False) -> list[dict[str, Any]]:
    """Public copies of the visible jobs; compact ones carry path counts instead of paths."""
    snapshots: list[dict[str, Any]] = []
    for job in jobs:
        if job.get("_hide_while_stopping") and str(job.get("status") or "") == "stopping":
            continue
        snapshot = {k: v for k, v in job.items() if k not in _SNAPSHOT_EXCLUDED_KEYS}
        if compact:
            target_paths = job_target_paths(job)
            snapshot.pop("target_paths", None)
            snapshot["has_explicit_paths"] = bool(target_paths)
            snapshot["target_path_count"] = len(target_paths)
            events = snapshot.get("events")
            if isinstance(events, list):
                snapshot["events"] = events[-3:]
        snapshots.append(snapshot)
    return snapshots


def queue_control_state(jobs: Iterable[dict[str, Any]], *, paused: bool) -> dict[str, Any]:
    active = next(
        (
            {
                "job_id": j.get("job_id"),
                "status": j.get("status"),
                "category": j.get("category"),
                "display_name": j.get("display_name"),
            }
            for j in jobs
            if j.get("status") in ("running", "paused", "stopping")
        ),
        None,
    )
    return {
        "paused": bool(paused),
        "active": active,
    }


class PersistedQueue:
    """A read-only queue over persisted job state, for callers that must not start the engine."""

    def __init__(self, jobs: dict[str, dict[str, Any]], *, paused: bool) -> None:
        self._jobs = jobs
        self._paused = paused

    def get_active_jobs(self, *, compact: bool = False) -> list[dict[str, Any]]:
        return job_snapshots(self._jobs.values(), compact=compact)

    def get_queue_control_state(self) -> dict[str, Any]:
        return queue_control_state(self._jobs.values(), paused=self._paused)


def build_queue_snapshot(service: QueueSource) -> dict[str, Any]:
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


def _item_rows(paths: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, path in enumerate(paths, start=1):
        text = str(path)
        rows.append({"index": idx, "path": text, "name": Path(text).name or text})
    return rows


def queued_job_items(engine: JobEngine, job_id: str) -> Optional[list[dict[str, Any]]]:
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return None
        status = str(job.get("status") or "")
        if status == "stopped":
            if not preserve_stopped_processing_job(job):
                return None
        elif status != "queued":
            return None
        return _item_rows(job_target_paths(job))


def active_job_items(engine: JobEngine, job_id: str) -> Optional[list[dict[str, Any]]]:
    """Return remaining explicit paths for an active job on demand."""
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job or str(job.get("status") or "") not in {"running", "paused", "stopping"}:
            return None
        paths = job_target_paths(job)
        if not paths:
            paths = normalize_paths(job.get("_snapshot_paths"))
        return _item_rows(paths)


def finished_job_items(engine: JobEngine, job_id: str) -> Optional[list[dict[str, Any]]]:
    """Return item paths for a recently finished job (current session only)."""
    with engine._lock:
        job = engine._jobs.get(job_id)
        if not job:
            return None
        status = str(job.get("status") or "")
        if status not in {"completed", "stopped", "failed", "cancelled"}:
            return None
        return _item_rows(normalize_paths(job.get("_completed_paths")))


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
