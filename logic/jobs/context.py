"""The job bound to the current thread/task, its pause/resume wait and its progress updates."""

from __future__ import annotations

import threading
import time
from contextvars import ContextVar
from typing import Any, Dict, Optional

from core import proc
from core.format import format_size

_job_ctx: ContextVar[Optional[Dict[str, Any]]] = ContextVar("job", default=None)
_job_lock = threading.Lock()


def set_thread_job(job_dict: Optional[Dict[str, Any]]) -> Any:
    """Bind ``job_dict`` to the current thread/task context.

    Returns the contextvar token so callers can ``reset_thread_job`` later
    and restore the prior value. The token is opaque -- treat it as private.
    Callers that don't need to restore (e.g. the lifetime of the worker
    thread is the lifetime of the job) may discard the token.
    """
    return _job_ctx.set(job_dict)


def reset_thread_job(token: Any) -> None:
    """Restore the prior thread-job binding for ``token``.

    Safe to call with a stale token: the underlying ``ContextVar`` raises
    ``LookupError`` / ``ValueError`` when the token can't be applied (e.g.
    different context); we swallow those because the caller has already
    decided the binding is no longer wanted.
    """
    if token is None:
        return
    try:
        _job_ctx.reset(token)
    except (LookupError, ValueError):
        # Token belongs to a different context (e.g. a thread that already
        # exited). The next ``get_thread_job`` will simply observe whatever
        # value is currently set, which is the desired fallback.
        _job_ctx.set(None)


def get_thread_job() -> Optional[Dict[str, Any]]:
    return _job_ctx.get()


def wait_for_job_resume(job: Optional[Dict[str, Any]], check_interval: float = 0.2) -> bool:
    """Block while a job is paused; return False when a stop is requested."""
    if not job:
        return True

    if job.get("pause_requested") and job.get("status") != "paused":
        job["status"] = "paused"
        job["progress"] = "Paused by user"
        job["speed"] = "Paused"
        job["current_stage"] = "PAUSED"
        callback = job.get("_pause_ack_callback")
        if callable(callback):
            callback()

    while job.get("pause_requested"):
        if job.get("stop_requested"):
            return False
        time.sleep(check_interval)

    return not bool(job.get("stop_requested"))


def update_job_progress(**kwargs: Any) -> None:
    """Update progress state for the current thread's job."""
    job = _job_ctx.get()
    if not job:
        return

    # Map kwargs to job keys
    mapping = {
        "msg": "progress",
        "processed": "items_processed",
        "total": "items_total",
        "percent": "progress_percent",
        "status": "status",
        "item_name": "current_item",
        "item_percent": "item_percent",
        "speed": "speed",
        "eta": "eta",
        "skipped": "items_skipped",
    }

    with _job_lock:
        for k, v in kwargs.items():
            if k == "item_size":
                job["item_size_str"] = format_size(v)
            elif k == "key":
                continue  # Handled below
            elif k == "item_name":
                # Only let priority labels or new item names win
                current = job.get("current_item", "")
                is_prio = "Priority" in str(v)
                curr_is_prio = "Priority" in str(current)

                # If we have a priority label already, don't let a non-priority one overwrite it
                # UNLESS the non-priority one is a completely different item name
                if curr_is_prio and not is_prio:
                    # Check if names (ignoring Priority prefix) are different
                    curr_clean = current.replace("Priority ", "")
                    new_clean = str(v).replace("Priority ", "")
                    if curr_clean == new_clean:
                        continue

                job["current_item"] = v
            elif k in mapping:
                job[mapping[k]] = v

        # Handle multi-item tracking (Dual Uploads)
        key = kwargs.get("key")
        if key:
            if "item_percents" not in job:
                job["item_percents"] = {}
            if "item_speeds" not in job:
                job["item_speeds"] = {}

            if "item_percent" in kwargs:
                # Store as float for precision, but dashboard often expects int
                val = float(kwargs["item_percent"])
                job["item_percents"][key] = val

                # RECALCULATE GLOBAL ITEM PERCENT
                # We always want to show the average of active items
                active_percents = [v for v in job["item_percents"].values() if v is not None]
                if active_percents:
                    job["item_percent"] = int(sum(active_percents) / len(active_percents))

            if "speed" in kwargs:
                # Store the speed and ensure it's a clean, stripped string
                job["item_speeds"][key] = str(kwargs["speed"]).strip()

                # Aggregate active speeds (e.g. "10 MB/s | 12 MB/s")
                all_speeds = [v for v in job["item_speeds"].values() if v and v != "Starting..."]
                if all_speeds:
                    job["speed"] = " | ".join(dict.fromkeys(all_speeds))



proc.install_job_hooks(progress=update_job_progress, wait_for_resume=wait_for_job_resume)
