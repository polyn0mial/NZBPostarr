"""Background pending-index manager.

Keeps the expensive pending snapshot warm off the request path. The index is
refreshed on manual requests plus periodic reconcile.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loguru import logger

from core import config as config_mod


class PendingIndexManager:
    """Maintains a background-refreshed pending snapshot."""

    def __init__(self, reconcile_interval_s: float = 180.0):
        self._reconcile_interval_s = max(5.0, float(reconcile_interval_s))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._dirty_event = threading.Event()

        self._thread: Optional[threading.Thread] = None

        self._scan_fn: Optional[Callable[[], Dict[str, Any]]] = None

        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_ts: float = 0.0
        self._refreshing: bool = False
        self._ready: bool = False
        self._last_error: Optional[str] = None
        self._pending_reason: Optional[str] = None

    def configure(self, scan_fn: Callable[[], Dict[str, Any]]) -> None:
        with self._lock:
            self._scan_fn = scan_fn

    def start(self, watched_folders: list[Path]) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._log_refresh_mode(watched_folders)
                self.request_refresh(reason="start-reconfigure")
                return

            self._stop_event.clear()
            self._dirty_event.set()
            self._log_refresh_mode(watched_folders)
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="nzbpostarr-pending-index",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_event.set()
            self._dirty_event.set()
            worker = self._thread
            self._thread = None

        if worker and worker.is_alive():
            worker.join(timeout=5)

    def restart_watched_folders(self, watched_folders: list[Path]) -> None:
        with self._lock:
            self._log_refresh_mode(watched_folders)
        self.request_refresh(reason="watch-folders-restart")

    def request_refresh(self, reason: str = "manual") -> None:
        with self._lock:
            self._pending_reason = reason
        logger.debug(f"Pending index marked dirty ({reason})")
        self._dirty_event.set()

    def set_snapshot(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._snapshot = data
            self._snapshot_ts = time.time()
            self._ready = True
            self._last_error = None

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = self._snapshot.copy() if isinstance(self._snapshot, dict) else None
            return {
                "snapshot": snapshot,
                "snapshot_ts": self._snapshot_ts,
                "ready": self._ready,
                "refreshing": self._refreshing,
                "last_error": self._last_error,
            }

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            triggered = self._dirty_event.wait(timeout=self._reconcile_interval_s)
            if self._stop_event.is_set():
                break

            if triggered:
                self._dirty_event.clear()

            self._run_refresh_once()

    def _run_refresh_once(self) -> None:
        started = time.perf_counter()
        with self._lock:
            scan_fn = self._scan_fn
            if not callable(scan_fn):
                return
            self._refreshing = True
            reason = self._pending_reason or "reconcile"
            self._pending_reason = None

        try:
            data = scan_fn()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            elapsed = time.perf_counter() - started
            logger.warning(f"Pending index refresh failed after {elapsed:.3f}s ({reason}): {exc}")
            with self._lock:
                self._last_error = str(exc)
        else:
            elapsed = time.perf_counter() - started
            summary = data.get("summary", {}) if isinstance(data, dict) else {}
            total = summary.get("total", 0) if isinstance(summary, dict) else 0
            message = f"Pending index refresh completed in {elapsed:.3f}s ({reason}, total={total})"
            conf = getattr(config_mod, "_GLOBAL_CONFIG", None)
            if bool(getattr(conf, "verbose", False)):
                if elapsed >= 1.0:
                    logger.debug(message)
                else:
                    logger.log("VERBOSE", message)
            with self._lock:
                self._snapshot = data
                self._snapshot_ts = time.time()
                self._ready = True
                self._last_error = None
        finally:
            with self._lock:
                self._refreshing = False

    @staticmethod
    def _log_refresh_mode(watched_folders: list[Path]) -> None:
        # Recursive watchdog observers over large torrent roots can create an
        # inotify storm and pin the app CPU, making the UI unresponsive. Keep
        # refreshes explicit/periodic so the queue stays usable.
        if watched_folders:
            logger.debug(
                "Pending index filesystem watchdog disabled; "
                f"using manual/periodic refresh for {len(watched_folders)} folder(s)"
            )


_MANAGER = PendingIndexManager()


def get_pending_index_manager() -> PendingIndexManager:
    return _MANAGER
