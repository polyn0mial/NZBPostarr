"""Process lifecycle: self-restart, stop-all, rollback and the deployed revision."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from loguru import logger

from core.config import APP_ROOT, get_config


def runtime_revision() -> Dict[str, Any]:
    """Return the source revision recorded by the deployment workflow."""
    revision_path = APP_ROOT / "data" / "deployed_revision.json"
    if revision_path.exists():
        try:
            payload = json.loads(revision_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("revision"):
                return {
                    "revision": str(payload["revision"]),
                    "dirty": bool(payload.get("dirty", False)),
                    "deployed_at": payload.get("deployed_at"),
                }
        except (OSError, ValueError, TypeError):
            pass
    env_revision = os.getenv("NZBPOSTARR_REVISION", "").strip()
    return {"revision": env_revision or "unknown", "dirty": False, "deployed_at": None}


def stop_all_service_activity(clear_staged_items: bool, wait_timeout_seconds: float) -> Dict[str, Any]:
    """Stop active jobs, clear waiting work, and wait until quiet."""
    # Deferred: logic.runtime loads the whole job engine, and tests patch runtime.ensure_engine_started.
    from logic.runtime import ensure_engine_started

    stop_result = ensure_engine_started().stop_all_jobs_and_wait(
        clear_staged_items=clear_staged_items,
        wait_timeout_s=wait_timeout_seconds,
    )
    status = "stopped"
    message = "All uploads stopped and queue cleared."
    if stop_result.get("timed_out"):
        status = "partial"
        message = "Stop requested, but some work was still shutting down when the timeout expired."

    return {
        "status": status,
        "message": message,
        "stop": stop_result,
    }


def restart_service(
    delay_seconds: float,
    stop_before_restart: bool,
    clear_staged_items: bool,
    wait_timeout_seconds: float,
) -> Dict[str, Any]:
    """Optionally stop all work, then schedule a process restart without changing files."""
    stop_result: Optional[Dict[str, Any]] = None
    if stop_before_restart:
        # Deferred: logic.runtime loads the whole job engine, and tests patch runtime.ensure_engine_started.
        from logic.runtime import ensure_engine_started

        stop_result = ensure_engine_started().stop_all_jobs_and_wait(
            clear_staged_items=clear_staged_items,
            wait_timeout_s=wait_timeout_seconds,
        )

    schedule_restart(delay_seconds=delay_seconds)
    return {
        "status": "scheduled",
        "delay_seconds": delay_seconds,
        "stop": stop_result,
    }


def rollback_update(backup_id: str, restart: bool) -> Dict[str, Any]:
    """Restore files from a previous updater snapshot."""
    from logic.system import updater  # deferred: updater imports schedule_restart from this module

    return updater.rollback_to_backup(backup_id=backup_id, restart=restart)


def schedule_restart(delay_seconds: float = 2.0) -> None:
    """Spawn a fresh process and terminate the current one after a short delay."""

    cmd = [sys.executable, *sys.argv]
    try:
        restart_port = int(get_config().port)
    except Exception:  # a config that no longer loads must not block the restart; fall back to the default port
        restart_port = 8000

    def _restart_worker() -> None:
        time.sleep(max(0.1, float(delay_seconds)))
        spawned = False
        try:
            kwargs: Dict[str, Any] = {
                "cwd": str(APP_ROOT),
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }

            if os.name == "nt":
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                subprocess.Popen(cmd, **kwargs)
            else:
                kwargs["start_new_session"] = True
                supervisor = r"""
import os
import signal
import subprocess
import sys
import time

cmd = sys.argv[1:]
time.sleep(float(os.environ.get("NZBPOSTARR_RESTART_DELAY", "1.0") or "1.0"))
try:
    import psutil
    port = int(os.environ.get("NZBPOSTARR_RESTART_PORT", "8000") or "8000")
    listeners = []
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == psutil.CONN_LISTEN and getattr(conn.laddr, "port", None) == port and conn.pid:
            if conn.pid == os.getpid():
                continue
            try:
                proc = psutil.Process(conn.pid)
                cmdline = " ".join(proc.cmdline()).lower()
            except Exception:
                cmdline = ""
            if "main.py" in cmdline or "nzbpostarr" in cmdline:
                listeners.append(conn.pid)
    for pid in sorted(set(listeners)):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if listeners:
        time.sleep(1.5)
    for pid in sorted(set(listeners)):
        try:
            proc = psutil.Process(pid)
            if proc.is_running():
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
except Exception:
    pass
subprocess.Popen(cmd, cwd=os.getcwd(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
"""
                env = os.environ.copy()
                env["NZBPOSTARR_RESTART_DELAY"] = "1.0"
                env["NZBPOSTARR_RESTART_PORT"] = str(restart_port)
                subprocess.Popen([sys.executable, "-c", supervisor, *cmd], env=env, **kwargs)
            spawned = True
            logger.info("Updater: restart process spawned successfully")
        except Exception as exc:
            logger.error(f"Updater: failed to spawn restart process: {exc}")
        if spawned:
            os._exit(0)

    threading.Thread(target=_restart_worker, daemon=True, name="nzbpostarr-restart").start()
