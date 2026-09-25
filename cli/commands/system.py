"""`system`: update and service lifecycle controls."""

from __future__ import annotations

import argparse
import sys
import time

from cli.output import _emit_result


def _run_launcher_control(flag: str) -> tuple[int, str]:
    """Run one safe launcher lifecycle action in a separate process."""
    import subprocess

    from core.config import APP_ROOT

    completed = subprocess.run(
        [sys.executable, str(APP_ROOT / "main.py"), flag],
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    detail = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode, detail


def _managed_daemon_is_running() -> bool:
    return _run_launcher_control("--status")[0] == 0


def _restart_managed_daemon(delay_seconds: float = 0.0) -> tuple[bool, str]:
    stop_rc, stop_detail = _run_launcher_control("--stop")
    if stop_rc != 0:
        return False, stop_detail or "The managed daemon could not be stopped."
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    start_rc, start_detail = _run_launcher_control("--daemon")
    if start_rc != 0:
        return False, start_detail or "The managed daemon could not be started."
    return True, start_detail or "NZBPostarr daemon restarted."


def cmd_system(args: argparse.Namespace) -> int:
    """Run deployment controls through the same updater and queue services as the API."""
    from logic.system import updater
    from logic.services import get_upload_service

    command = getattr(args, "system_command", None)
    update_command = getattr(args, "update_command", None)
    try:
        if command == "update" and update_command == "check":
            payload = updater.check_for_updates()
            if payload.get("check_error"):
                payload["status"] = "error"
                payload["message"] = f"Update check failed: {payload['check_error']}"
                return _emit_result(args, payload, rc=1)
            payload.setdefault("status", "ok")
            return _emit_result(args, payload)
        if command == "update" and update_command == "install":
            payload = updater.install_from_github(version=args.version, restart=False)
            payload["restart_required"] = not args.no_restart
            payload["message"] = (
                "Update installed. Restart NZBPostarr with 'system restart' or your process supervisor."
                if not args.no_restart
                else "Update installed without requesting a restart."
            )
            return _emit_result(args, payload)
        if command == "update" and update_command == "rollback":
            payload = updater.rollback_to_backup(args.backup_id, restart=False)
            payload["restart_required"] = not args.no_restart
            payload["message"] = (
                "Rollback applied. Restart NZBPostarr with 'system restart' or your process supervisor."
                if not args.no_restart
                else "Rollback applied without requesting a restart."
            )
            return _emit_result(args, payload)
        if command == "restart":
            stopped = None
            if not args.no_stop:
                stopped = get_upload_service().stop_all_jobs_and_wait(
                    clear_staged_items=not args.keep_staged_items,
                    wait_timeout_s=args.wait_timeout,
                )
                if stopped.get("timed_out") and not args.force:
                    return _emit_result(
                        args,
                        {
                            "status": "partial",
                            "message": "Work did not stop before the timeout; restart was not attempted. Use --force to proceed.",
                            "stop": stopped,
                            "restart_required": True,
                        },
                        rc=1,
                    )
            if not _managed_daemon_is_running():
                return _emit_result(
                    args,
                    {
                        "status": "restart_required",
                        "message": "No managed daemon is running. Restart NZBPostarr with your process supervisor or operator workflow.",
                        "stop": stopped,
                        "restart_required": True,
                    },
                    rc=1,
                )
            restarted, detail = _restart_managed_daemon(delay_seconds=args.delay)
            return _emit_result(
                args,
                {
                    "status": "restarted" if restarted else "error",
                    "message": detail,
                    "stop": stopped,
                    "restart_required": not restarted,
                },
                rc=0 if restarted else 1,
            )
        if command == "stop-all":
            result = get_upload_service().stop_all_jobs_and_wait(
                clear_staged_items=not args.keep_staged_items,
                wait_timeout_s=args.wait_timeout,
            )
            status = "partial" if result.get("timed_out") else "stopped"
            message = (
                "Stop requested, but some work was still shutting down when the timeout expired."
                if result.get("timed_out")
                else "All uploads stopped and waiting work cleared."
            )
            return _emit_result(
                args,
                {"status": status, "message": message, "stop": result},
                rc=1 if result.get("timed_out") else 0,
            )
    except updater.UpdateError as exc:
        return _emit_result(args, {"status": "error", "message": str(exc)}, human=f"Error: {exc}", rc=1)
    except Exception as exc:
        message = f"System command failed: {exc}"
        return _emit_result(args, {"status": "error", "message": message}, human=f"Error: {message}", rc=1)

    return _emit_result(
        args,
        {"status": "error", "message": "No system command given."},
        human="Error: use 'system --help' to see available commands.",
        rc=1,
    )
