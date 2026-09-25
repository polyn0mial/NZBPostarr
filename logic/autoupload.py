"""Auto-upload: watch the configured roots and queue items once they settle."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from core.fs import start_watchdog_observer, stop_watchdog_observer
from logic.classify.content import detect_auto_category
from logic.classify.hints import infer_folder_category_hint


# Settle time - wait this many seconds after the last modification before uploading
SETTLE_SECONDS = 30

# How often (seconds) the settle-checker evaluates pending items
_SETTLE_CHECK_INTERVAL = 5

@dataclass
class _PendingItemState:
    last_event_monotonic: float
    configured_category: str

@dataclass
class _FolderMonitorRuntime:
    observer: Any = None
    settle_task: Optional[asyncio.Task] = None
    pending_items: Dict[str, Dict[str, _PendingItemState]] = field(default_factory=dict)
    known_items: Dict[str, Set[str]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def clear(self) -> None:
        with self.lock:
            self.pending_items.clear()
            self.known_items.clear()

_runtime = _FolderMonitorRuntime()

def _get_monitored_folders() -> list[Dict[str, Any]]:
    """Retrieve configured folder_paths entries that have monitor enabled."""
    from core.config import get_config

    conf = get_config()
    monitored = []
    folder_entries = conf.get_folder_path_entries()
    for fp in folder_entries:
        path = fp.get("path", "")
        monitor = fp.get("monitor", False)
        if monitor and path:
            monitored.append(
                {"path": path, "category": str(fp.get("category") or "external").strip().lower() or "external"}
            )
    return monitored

def _scan_folder(folder_path: str) -> Set[str]:
    """Return the set of top-level item names (files/dirs) in a folder."""
    p = Path(folder_path)
    if not p.exists() or not p.is_dir():
        return set()
    try:
        return {item.name for item in p.iterdir() if not item.name.startswith(".")}
    except PermissionError:
        return set()

class _FolderEventHandler(FileSystemEventHandler):
    """Watchdog handler that records new top-level items as pending."""

    def __init__(self, folder_path: str, configured_category: str):
        super().__init__()
        self.folder_path = folder_path
        self.configured_category = str(configured_category or "external").strip().lower() or "external"
        self._folder = Path(folder_path)

    def _extract_top_level_name(self, src_path: str) -> Optional[str]:
        try:
            rel = Path(os.fsdecode(src_path)).relative_to(self._folder)
        except (ValueError, TypeError):
            return None

        top_level = rel.parts[0] if rel.parts else None
        if not top_level or top_level.startswith("."):
            return None
        return top_level

    def _record_event(self, event: FileSystemEvent) -> None:
        """Record a filesystem event, mapping it to its top-level item."""
        top_level = self._extract_top_level_name(getattr(event, "src_path", ""))
        if not top_level:
            return

        with _runtime.lock:
            known = _runtime.known_items.get(self.folder_path, set())
            if top_level in known:
                return
            pending = _runtime.pending_items.setdefault(self.folder_path, {})
            pending[top_level] = _PendingItemState(
                last_event_monotonic=time.monotonic(),
                configured_category=self.configured_category,
            )

    def _discard_item(self, src_path: str) -> None:
        top_level = self._extract_top_level_name(src_path)
        if not top_level:
            return

        with _runtime.lock:
            _runtime.pending_items.get(self.folder_path, {}).pop(top_level, None)
            _runtime.known_items.get(self.folder_path, set()).discard(top_level)

    def on_created(self, event: FileSystemEvent) -> None:
        self._record_event(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._record_event(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._record_event(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._discard_item(getattr(event, "src_path", ""))

def _resolve_monitored_category(configured_category: str, item_path: Path, folder_path: str = "") -> str:
    normalized = str(configured_category or "").strip().lower()
    if normalized in {"", "external", "auto"}:
        return detect_auto_category(item_path, infer_folder_category_hint(folder_path or item_path.parent))
    return normalized

def _trigger_uploads(folder_path: str, item_categories: Dict[str, str]) -> None:
    """Start explicit-path upload jobs grouped by auto-detected category."""
    try:
        from logic.services import get_upload_service

        service = get_upload_service()
        grouped_paths: Dict[str, list[str]] = {}
        for item_name, configured_category in item_categories.items():
            item_path = Path(folder_path) / item_name
            if not item_path.exists():
                continue
            category = _resolve_monitored_category(configured_category, item_path, folder_path)
            grouped_paths.setdefault(category, []).append(str(item_path))

        if not grouped_paths:
            return

        for started in service.start_path_jobs(grouped_paths, reuse_running=False, source="folder-monitor"):
            logger.info(f"📡 Monitor auto-started upload job {started['job_id']} for category '{started['category']}'")
    except Exception as e:
        logger.error(f"📡 Monitor failed to start upload jobs for '{folder_path}': {e}")

async def _settle_loop() -> None:
    """Periodically check if pending items have settled and trigger uploads."""
    while True:
        try:
            await asyncio.sleep(_SETTLE_CHECK_INTERVAL)

            now = time.monotonic()
            settled_by_folder: Dict[str, Dict[str, str]] = {}

            with _runtime.lock:
                for folder_path, items in list(_runtime.pending_items.items()):
                    settled_names = {
                        name: state.configured_category
                        for name, state in items.items()
                        if (now - state.last_event_monotonic) >= SETTLE_SECONDS
                    }

                    if settled_names:
                        # Remove settled items from pending and add to baseline
                        for name in settled_names:
                            del items[name]
                        _runtime.known_items.setdefault(folder_path, set()).update(settled_names)

                        settled_by_folder.setdefault(folder_path, {}).update(settled_names)

            # Trigger uploads outside the lock
            for folder_path, items in settled_by_folder.items():
                logger.info(
                    f"📡 Monitor detected {len(items)} settled item(s): "
                    f"{', '.join(sorted(items)[:5])}"
                    f"{'...' if len(items) > 5 else ''}"
                )
                _trigger_uploads(folder_path, items)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"📡 Monitor settle-check error: {e}")
            await asyncio.sleep(5)

async def start_folder_monitor() -> None:
    """Start the watchdog-based folder monitor."""
    folders = _get_monitored_folders()
    if not folders:
        logger.debug("📡 Folder Monitor: No monitored folders configured, skipping start")
        return

    if _runtime.observer is not None:
        logger.debug("📡 Folder Monitor already running")
        return

    # Build baseline and schedule watchers
    watch_specs: list[tuple[Any, Path, bool]] = []
    for f in folders:
        folder_path = f["path"]
        configured_category = str(f.get("category") or "external")
        p = Path(folder_path)
        if not p.exists() or not p.is_dir():
            logger.warning(f"📡 Monitor: Skipping non-existent folder {folder_path}")
            continue

        # Record baseline - these items won't trigger uploads
        with _runtime.lock:
            _runtime.known_items[folder_path] = _scan_folder(folder_path)
            count = len(_runtime.known_items[folder_path])
        logger.debug(f"  Monitor baseline: {folder_path} ({count} items)")

        watch_specs.append((_FolderEventHandler(folder_path, configured_category), p, True))

    observer, scheduled = start_watchdog_observer(watch_specs, observer_factory=Observer)
    if observer is None or scheduled == 0:
        return
    _runtime.observer = observer

    # Start the async settle-checker
    _runtime.settle_task = asyncio.create_task(_settle_loop())

    logger.info(f"📡 Folder Monitor watching {scheduled} folder(s) [watchdog]")

async def stop_folder_monitor() -> None:
    """Stop the folder monitor."""
    if _runtime.settle_task and not _runtime.settle_task.done():
        _runtime.settle_task.cancel()
        try:
            await _runtime.settle_task
        except asyncio.CancelledError:
            pass
    _runtime.settle_task = None

    if _runtime.observer is not None:
        stop_watchdog_observer(_runtime.observer)
        _runtime.observer = None

    _runtime.clear()
    logger.info("📡 Folder Monitor stopped")

async def restart_folder_monitor() -> None:
    """Restart the monitor (e.g. after settings change)."""
    await stop_folder_monitor()
    await start_folder_monitor()
