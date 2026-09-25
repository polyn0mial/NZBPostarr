"""Runtime job checkpoints: per-item resume keys and cleanup paths persisted on the job."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from loguru import logger

from core.paths import checkpoint_key, path_key


def _append_cleanup_path(job: Optional[dict[str, Any]], path: Path) -> None:
    """Register a temporary file or directory to be removed when the job finishes."""
    if job is None:
        return
    cleanup_paths = job.setdefault("cleanup_paths", [])
    path_text = str(path)
    if path_text not in cleanup_paths:
        cleanup_paths.append(path_text)


def _normalize_runtime_target_paths(job: Optional[dict[str, Any]], fallback_paths: Optional[List[str]]) -> list[str]:
    """Return the mutable remaining-path queue for an active targeted job."""
    if job:
        current = job.get("target_paths")
        if isinstance(current, list) and current:
            return [str(path) for path in current if path]

    if not fallback_paths:
        return []

    normalized = [str(path) for path in fallback_paths if path]
    if job is not None:
        job["target_paths"] = normalized
    return normalized


def _persist_runtime_job_checkpoint(job: Optional[dict[str, Any]]) -> None:
    """Persist active queue state without coupling processing to the queue service."""
    if job is None:
        return
    callback = job.get("_persist_callback")
    if not callable(callback):
        return
    try:
        callback()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning(f"Could not persist runtime job checkpoint: {exc}")


def _begin_runtime_item_checkpoint(
    job: Optional[dict[str, Any]],
    path: Path,
    *,
    make_current: bool,
) -> None:
    if job is None:
        return
    path_text = str(path)
    path_key = checkpoint_key(path_text)
    inflight = [
        str(value)
        for value in (job.get("_inflight_item_paths") or [])
        if str(value).strip()
    ]
    if path_key and all(checkpoint_key(value) != path_key for value in inflight):
        inflight.append(path_text)
    job["_inflight_item_paths"] = inflight
    if make_current:
        job["_current_item_path"] = path_text
    _persist_runtime_job_checkpoint(job)


def _complete_runtime_item_checkpoint(job: Optional[dict[str, Any]], path: Path) -> None:
    if job is None:
        return
    path_key = checkpoint_key(path)
    job["_inflight_item_paths"] = [
        str(value)
        for value in (job.get("_inflight_item_paths") or [])
        if checkpoint_key(value) != path_key
    ]
    if checkpoint_key(job.get("_current_item_path")) == path_key:
        job.pop("_current_item_path", None)
    _persist_runtime_job_checkpoint(job)
