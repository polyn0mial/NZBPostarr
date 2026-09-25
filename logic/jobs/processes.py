"""Registry of external tool processes per job, and their termination."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psutil


class ProcessRegistry:
    """View over the engine's job_id -> [process] map; the engine's lock guards it."""

    def __init__(self, processes: dict[str, list[Any]], lock: threading.Lock) -> None:
        self._processes = processes
        self._lock = lock

    def register(self, job_id: str, process: Any) -> None:
        with self._lock:
            if job_id not in self._processes:
                self._processes[job_id] = []
            self._processes[job_id].append(process)

    def unregister(self, job_id: str, process: Optional[Any] = None) -> None:
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

    @staticmethod
    def collect_process_tree_pids(process: Any) -> list[int]:
        pid = getattr(process, "pid", None)
        if not pid:
            return []
        try:
            root = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            return []

        pids = [root.pid]
        try:
            pids.extend(child.pid for child in root.children(recursive=True))
        except (psutil.Error, OSError):
            pass
        return pids

    def terminate_locked(self, job_id: str, *, kill_delay_s: float = 0.5) -> int:
        """Terminate registered tool processes for a job, then schedule a quick kill fallback."""
        processes = list(self._processes.get(job_id) or [])
        if not processes:
            return 0

        pids: list[int] = []
        seen: set[int] = set()
        for process in processes:
            for pid in self.collect_process_tree_pids(process):
                if pid not in seen:
                    seen.add(pid)
                    pids.append(pid)

        terminated = 0
        for pid in reversed(pids):
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    proc.terminate()
                    terminated += 1
            except (psutil.Error, OSError):
                continue

        if pids:
            from logic.system.reaper import get_scheduler

            sched = get_scheduler()

            def kill_remaining(target_pids: list[int] = list(pids)) -> None:
                for target_pid in reversed(target_pids):
                    try:
                        proc = psutil.Process(target_pid)
                        if proc.is_running():
                            proc.kill()
                    except (psutil.Error, OSError):
                        continue

            sched.add_job(
                kill_remaining,
                "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=max(0.1, kill_delay_s)),
                id=f"kill_{job_id}_tree",
                replace_existing=True,
            )

        return terminated
