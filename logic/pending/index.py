"""The pending index: background snapshot refresh, filesystem watch and anime lookups."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler

from core import config as config_mod
from core.fs import start_watchdog_observer, stop_watchdog_observer
from logic.classify.anime import cached_lookup


class _PendingIndexEventHandler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, manager: "PendingIndexManager"):
        super().__init__()
        self._manager = manager

    def on_any_event(self, event: FileSystemEvent) -> None:
        # Skip directory-modified and file-created: torrent downloads produce one
        # file-created event per file and would cause a refresh storm.
        if event.is_directory and event.event_type == "modified":
            return
        if not event.is_directory and event.event_type == "created":
            return

        src_path = getattr(event, "src_path", "")
        if src_path:
            path = Path(str(src_path))
            if any(part.startswith(".") for part in path.parts):
                return

        self._manager.request_refresh(reason="watchdog")

class PendingIndexManager:
    """Maintains a background-refreshed pending snapshot."""

    def __init__(self, reconcile_interval_s: float = 180.0, watchdog_debounce_s: float = 2.5):
        self._reconcile_interval_s = max(5.0, float(reconcile_interval_s))
        self._watchdog_debounce_s = max(0.1, float(watchdog_debounce_s))
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._dirty_event = threading.Event()

        self._thread: Optional[threading.Thread] = None
        self._observer: Optional[Any] = None

        self._scan_fn: Optional[Callable[[], Dict[str, Any]]] = None

        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_ts: float = 0.0
        self._refreshing: bool = False
        self._ready: bool = False
        self._last_error: Optional[str] = None
        self._pending_reason: Optional[str] = None
        self._last_watchdog_request_ts: float = 0.0

    def configure(self, scan_fn: Callable[[], Dict[str, Any]]) -> None:
        with self._lock:
            self._scan_fn = scan_fn

    def start(self, watched_folders: list[Path]) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._restart_observer_locked(watched_folders)
                self.request_refresh(reason="start-reconfigure")
                return

            self._stop_event.clear()
            self._dirty_event.set()
            self._restart_observer_locked(watched_folders)
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
            self._stop_observer_locked()

        if worker and worker.is_alive():
            worker.join(timeout=5)

    def restart_watched_folders(self, watched_folders: list[Path]) -> None:
        with self._lock:
            self._restart_observer_locked(watched_folders)
        self.request_refresh(reason="watch-folders-restart")

    def request_refresh(self, reason: str = "manual") -> None:
        with self._lock:
            if reason == "watchdog":
                now = time.monotonic()
                if self._dirty_event.is_set():
                    return
                if (now - self._last_watchdog_request_ts) < self._watchdog_debounce_s:
                    return
                self._last_watchdog_request_ts = now
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

    def _stop_observer_locked(self) -> None:
        if self._observer is None:
            return
        stop_watchdog_observer(self._observer)
        self._observer = None

    def _restart_observer_locked(self, watched_folders: list[Path]) -> None:
        self._stop_observer_locked()
        if not watched_folders:
            return

        # File-created events are filtered out (they storm during active
        # downloads); renames, deletes and moves trigger a debounced refresh.
        handler = _PendingIndexEventHandler(self)
        try:
            observer, scheduled = start_watchdog_observer(
                [(handler, folder, True) for folder in watched_folders]
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"Could not start pending index watchdog: {exc}")
            return
        self._observer = observer
        if observer is not None:
            logger.debug(f"Pending index watchdog active for {scheduled} folder(s)")

_MANAGER = PendingIndexManager()

def get_pending_index_manager() -> PendingIndexManager:
    return _MANAGER

def collect_anime_check_names(data: Dict[str, Any]) -> list[str]:
    """Extract unique candidate titles for anime lookups from a pending snapshot."""
    seen: set[str] = set()
    names: list[str] = []
    video_itypes = {"TV Show", "Movie", "Anime"}

    def _add(name: str) -> None:
        cleaned = str(name or "").strip()
        identity = cleaned.casefold()
        if cleaned and identity not in seen:
            seen.add(identity)
            names.append(cleaned)

    def _add_item(item: Dict[str, Any], section: str = "") -> None:
        detector_candidates = item.get("_anime_lookup_candidates")
        if isinstance(detector_candidates, (list, tuple)):
            for candidate in detector_candidates:
                _add(str(candidate or ""))
        if item.get("itype") in video_itypes or section in {"tv", "movies", "anime"}:
            _add(str(item.get("name") or ""))

    items = data.get("items", {})

    for category_key, category_items in items.items():
        if category_key == "external" or not isinstance(category_items, list):
            continue
        for item in category_items:
            if not isinstance(item, dict):
                continue
            _add_item(item, category_key)

    for group in items.get("external", []):
        for item in group.get("items", []):
            if isinstance(item, dict):
                _add_item(item)

    return names

def collect_uncached_anime_check_names(data: Dict[str, Any]) -> list[str]:
    """Return anime-check candidates that are not already cached."""
    return [name for name in collect_anime_check_names(data) if cached_lookup(name) is None]
