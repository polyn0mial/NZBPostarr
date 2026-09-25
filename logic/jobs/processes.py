"""Registry of external tool processes per job, and their termination."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psutil

from core.scheduler import get_scheduler
from logic.system.reaper import kill_survivors, terminate_process_tree


class ProcessRegistry:
    """View over the engine's job_id -> [process] map; the engine's lock guards it."""

    def __init__(self, processes: dict[str, list[Any]], lock: threading.Lock) -> None:
        self._processes = processes
        self._lock = lock

    def register_process(self, job_id: str, process: Any) -> None:
        with self._lock:
            if job_id not in self._processes:
                self._processes[job_id] = []
            self._processes[job_id].append(process)

    def unregister_process(self, job_id: str, process: Optional[Any] = None) -> None:
        with self._lock:
            if job_id in self._processes:
                if process:
                    try:
                        self._processes[job_id].remove(process)
                        if not self._processes[job_id]:
                            del self._processes[job_id]
                    except ValueError:
                        pass
                else:
                    del self._processes[job_id]

    def terminate_locked(self, job_id: str, *, kill_delay_s: float = 0.5) -> int:
        """SIGTERM every registered tool process tree of a job now, SIGKILL survivors after kill_delay_s.

        The caller holds the engine lock, so the kill is scheduled rather than waited for; the
        tree walk and both signals are logic.system.reaper's (the one tree kill).
        """
        signalled: list[psutil.Process] = []
        seen: set[int] = set()
        for process in list(self._processes.get(job_id) or []):
            # Registered entries are Popen-like; anything without a pid has nothing to signal.
            pid = getattr(process, "pid", None)
            if not pid or pid in seen:
                continue
            try:
                tree = terminate_process_tree(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            for proc in tree:
                if proc.pid not in seen:
                    seen.add(proc.pid)
                    signalled.append(proc)

        if signalled:
            get_scheduler().add_job(
                kill_survivors,
                "date",
                args=[signalled],
                run_date=datetime.now(timezone.utc) + timedelta(seconds=max(0.1, kill_delay_s)),
                id=f"kill_{job_id}_tree",
                replace_existing=True,
            )

        return len(signalled)
