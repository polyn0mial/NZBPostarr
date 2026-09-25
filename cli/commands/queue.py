"""`queue`: inspect or control the shared upload queue.

App modules are imported inside the functions that use them, so the CLI starts without loading the app.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from cli.output import _emit_result


def _cmd_queue_status(args: argparse.Namespace) -> int:
    """Show running/queued/finished jobs and the queue-pause state.

    Reads the persisted job state without building the engine, so it never starts the
    scheduler. Shares build_queue_snapshot with GET /queue so the CLI and the WebUI can
    never disagree about how a job is classified.
    """
    from core.config import get_config
    from logic.jobs.store import JobStore, state_paths
    from logic.jobs.views import PersistedQueue, build_queue_snapshot

    jobs, paused = JobStore(*state_paths(get_config().script_dir)).load_readonly()
    payload = build_queue_snapshot(PersistedQueue(jobs, paused=paused))
    running = payload["running"]
    queued = payload["queued"]
    finished = payload["finished"]
    control = payload["control"]

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print("━" * 60)
    print("  NZBPostarr - Queue Status")
    print("━" * 60)
    print(f"  Queue paused: {bool(control.get('paused'))}")
    active = control.get("active")
    if active:
        print(f"  Active job:   {active.get('job_id')} [{active.get('status')}] ({active.get('category')})")

    for label, jobs in (("Running", running), ("Queued", queued), ("Finished", finished)):
        print(f"\n  {label}: {len(jobs)}")
        for job in jobs:
            print(f"    - {job.get('job_id')} [{job.get('status')}] {job.get('category')} - {job.get('progress') or ''}")

    print("━" * 60)
    return 0


def cmd_queue_job(args: argparse.Namespace, service: Any) -> int:
    """Control a single job by ID. Mirrors the per-job /jobs/{job_id}/* routes."""
    job_command = getattr(args, "job_command", None)
    job_id = getattr(args, "job_id", None)

    if job_command == "pause":
        if not service.pause_job(job_id):
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found or not running"},
                human="Error: job not found or not running",
                rc=1,
            )
        job = service.get_job(job_id) or {}
        pause_pending = bool(job.get("pause_requested")) and job.get("status") == "running"
        return _emit_result(
            args,
            {
                "status": job.get("status", "paused"),
                "job_id": job_id,
                "message": (
                    "Pause requested; waiting for the current safe checkpoint."
                    if pause_pending
                    else "Job paused; the scheduler lane is available."
                ),
            },
        )

    if job_command == "resume":
        if not service.resume_job(job_id):
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found or not paused"},
                human="Error: job not found or not paused",
                rc=1,
            )
        job = service.get_job(job_id) or {}
        queued = bool(job.get("resume_requested"))
        return _emit_result(
            args,
            {
                "status": job.get("status", "running"),
                "job_id": job_id,
                "message": "Job queued to resume when the scheduler lane is available." if queued else "Job resumed.",
            },
        )

    if job_command == "stop":
        clear = getattr(args, "clear", False)
        ok = service.stop_and_clear_job(job_id) if clear else service.stop_job(job_id)
        if not ok:
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found"},
                human="Error: job not found",
                rc=1,
            )
        return _emit_result(
            args,
            {
                "status": "clearing" if clear else "stopping",
                "job_id": job_id,
                "message": "Job stopping and clearing from the queue." if clear else "Termination signal sent to job.",
            },
        )

    if job_command == "retry":
        ok, new_job_id, reason = service.retry_job(job_id)
        if not ok:
            message = "Job not found" if reason == "not-found" else "Job is not eligible for retry"
            return _emit_result(
                args,
                {"status": "error", "message": message},
                human=f"Error: {message}",
                rc=1,
            )
        return _emit_result(
            args,
            {
                "status": "queued",
                "job_id": new_job_id,
                "retry_of": job_id,
                "message": f"Retry queued as {new_job_id}",
            },
        )

    if job_command == "promote":
        if not service.promote_job(job_id):
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found or not queued"},
                human="Error: job not found or not queued",
                rc=1,
            )
        return _emit_result(args, {"status": "promoted", "job_id": job_id})

    print("Error: no per-job command given. Use '--headless queue job --help' to see available commands.")
    return 1


def cmd_queue(args: argparse.Namespace) -> int:
    """Inspect or control the shared upload queue (mirrors the /queue routes).

    Every action here calls the same JobEngine methods
    that app.py's /queue and /jobs routes call - no queue logic lives here.
    """
    action = getattr(args, "queue_command", None) or "status"
    if action == "status":
        return _cmd_queue_status(args)

    from logic.runtime import ensure_engine_started

    service = ensure_engine_started()

    if action == "pause":
        service.pause_queue(pause_active=True)
        return _emit_result(
            args,
            {
                "status": "paused",
                "message": "Queue processing paused. New jobs remain queued until resumed.",
                "control": service.get_queue_control_state(),
            },
        )

    if action == "resume":
        service.resume_queue()
        return _emit_result(
            args,
            {
                "status": "running",
                "message": "Queue processing resumed.",
                "control": service.get_queue_control_state(),
            },
        )

    if action == "stop":
        if getattr(args, "clear", False):
            result = service.stop_queue_and_clear()
            payload = {"status": "clearing", "message": "Active job stopping and queue clearing.", **result}
        else:
            service.stop_queue()
            payload = {
                "status": "stopped",
                "message": "Active job stopping. Queue processing is paused.",
                "control": service.get_queue_control_state(),
            }
        return _emit_result(args, payload)

    if action == "clear":
        cleared = service.clear_queued_jobs()
        return _emit_result(args, {"cleared": cleared}, human=f"Cleared {cleared} queued job(s).")

    if action == "revalidate":
        result = service.revalidate_queued_jobs(include_paused=not getattr(args, "skip_paused", False))
        payload = {"status": "success", **result}
        human = f"Inspected {result['inspected']}, updated {result['updated']}, cancelled {result['cancelled']}."
        return _emit_result(args, payload, human=human)

    if action == "job":
        return cmd_queue_job(args, service)

    print("Error: no queue command given. Use '--headless queue --help' to see available commands.")
    return 1
