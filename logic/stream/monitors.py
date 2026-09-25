"""
Stream monitors: watch folders for new .nzb files and queue a repost job for each.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from core.config import get_config
from core.fs import start_watchdog_observer, stop_watchdog_observer
from core.paths import path_key, resolve_path
from logic.stream.nntp import StreamError
from logic.stream.repost import (
    _normalize_stream_category,
    normalize_submit_mode,
    resolve_posting_server,
    resolve_stream_category,
)


STREAM_MONITOR_SETTLE_SECONDS = 15
_STREAM_MONITOR_CHECK_INTERVAL = 5
_stream_monitor_observer: Optional[Observer] = None
_stream_monitor_task: Optional[asyncio.Task[Any]] = None
_stream_monitor_entries: dict[str, dict[str, Any]] = {}
_stream_monitor_pending: dict[str, dict[str, float]] = {}
_stream_monitor_known: dict[str, set[str]] = {}
_stream_monitor_lock = threading.Lock()
_stream_monitor_state_loaded = False


def _stream_monitor_state_path() -> Path:
    conf = get_config()
    state_dir = Path(conf.script_dir) / "data" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / "stream_monitors.json"


def _serialize_monitor(entry: dict[str, Any]) -> dict[str, Any]:
    last_job = entry.get("last_job") if isinstance(entry.get("last_job"), dict) else None
    raw_category = str(entry.get("category") or "").strip()
    return {
        "id": str(entry.get("id") or ""),
        "folder_path": str(entry.get("folder_path") or ""),
        "category": raw_category,
        "uses_nzb_category": not bool(raw_category),
        "posting_server_name": entry.get("posting_server_name"),
        "submit_mode": normalize_submit_mode(entry.get("submit_mode")),
        "indexer_id": entry.get("indexer_id"),
        "enable_duplicate_check": bool(entry.get("enable_duplicate_check", True)),
        "test_mode": bool(entry.get("test_mode", False)),
        "created_at": str(entry.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
        "last_job": _serialize_monitor_job(last_job) if last_job else None,
    }


def _serialize_monitor_job(job: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(job, dict):
        return None

    fields = {
        "job_id",
        "status",
        "progress",
        "progress_percent",
        "current_item",
        "item_percent",
        "speed",
        "eta",
        "current_stage",
        "items_processed",
        "items_total",
        "items_skipped",
        "total_bytes",
        "started_at",
        "display_name",
        "summary",
        "item_size_str",
        "source_monitor_id",
    }
    snapshot = {key: job.get(key) for key in fields if key in job}
    if snapshot.get("status") in {"completed", "failed", "stopped", "cancelled"}:
        snapshot["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return snapshot


def _save_stream_monitors_locked() -> None:
    payload = {"monitors": [_serialize_monitor(entry) for entry in _stream_monitor_entries.values()]}
    _stream_monitor_state_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_stream_monitors_locked() -> None:
    global _stream_monitor_state_loaded
    if _stream_monitor_state_loaded:
        return

    _stream_monitor_entries.clear()
    state_path = _stream_monitor_state_path()
    if state_path.exists():
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning(f"Failed to read stream monitor state: {exc}")
            payload = {}

        for row in payload.get("monitors", []) if isinstance(payload, dict) else []:
            if not isinstance(row, dict):
                continue
            folder_path = row.get("folder_path")
            monitor_id = str(row.get("id") or uuid.uuid4().hex[:8])
            if not folder_path:
                continue
            entry = _serialize_monitor(
                {**row, "id": monitor_id, "folder_path": str(resolve_path(folder_path))}
            )
            _stream_monitor_entries[monitor_id] = entry

    _stream_monitor_state_loaded = True


def list_stream_monitors() -> list[dict[str, Any]]:
    with _stream_monitor_lock:
        _load_stream_monitors_locked()
        return sorted(
            (_serialize_monitor(entry) for entry in _stream_monitor_entries.values()),
            key=lambda item: item["folder_path"].lower(),
        )


def add_stream_monitor(
    *,
    folder_path: str,
    category: str,
    posting_server_name: Optional[str] = None,
    submit_mode: str = "post_and_submit",
    indexer_id: Optional[str] = None,
    enable_duplicate_check: bool = True,
    test_mode: bool = False,
) -> dict[str, Any]:
    folder = resolve_path(folder_path)
    if not folder.exists() or not folder.is_dir():
        raise StreamError(f"Monitor path must be an existing folder: {folder}")

    resolved_server = resolve_posting_server(posting_server_name)
    normalized_mode = normalize_submit_mode(submit_mode)

    with _stream_monitor_lock:
        _load_stream_monitors_locked()
        existing = next(
            (
                monitor_id
                for monitor_id, entry in _stream_monitor_entries.items()
                if path_key(entry.get("folder_path", "")) == path_key(folder)
            ),
            None,
        )
        monitor_id = existing or uuid.uuid4().hex[:8]
        entry = {
            "id": monitor_id,
            "folder_path": str(folder),
            "category": _normalize_stream_category(category),
            "posting_server_name": resolved_server.name,
            "submit_mode": normalized_mode,
            "indexer_id": indexer_id or None,
            "enable_duplicate_check": bool(enable_duplicate_check),
            "test_mode": bool(test_mode),
            "created_at": _stream_monitor_entries.get(monitor_id, {}).get(
                "created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            ),
            "last_job": _serialize_monitor_job(_stream_monitor_entries.get(monitor_id, {}).get("last_job")),
        }
        _stream_monitor_entries[monitor_id] = entry
        _save_stream_monitors_locked()
        return _serialize_monitor(entry)


def remove_stream_monitor(monitor_id: str) -> bool:
    with _stream_monitor_lock:
        _load_stream_monitors_locked()
        removed = _stream_monitor_entries.pop(str(monitor_id), None)
        _stream_monitor_pending.pop(str(monitor_id), None)
        _stream_monitor_known.pop(str(monitor_id), None)
        _save_stream_monitors_locked()
        return removed is not None


def record_stream_monitor_job(monitor_id: Optional[str], job: Optional[dict[str, Any]]) -> None:
    if not monitor_id or not isinstance(job, dict):
        return

    snapshot = _serialize_monitor_job(job)
    if not snapshot:
        return

    with _stream_monitor_lock:
        _load_stream_monitors_locked()
        entry = _stream_monitor_entries.get(str(monitor_id))
        if not entry:
            return
        entry["last_job"] = snapshot
        _save_stream_monitors_locked()


def _scan_monitor_nzb_files(folder_path: str) -> set[str]:
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        return set()
    results: set[str] = set()
    try:
        for path in folder.rglob("*.nzb"):
            if not path.is_file():
                continue
            try:
                rel_parts = path.relative_to(folder).parts
            except ValueError:
                rel_parts = path.parts
            if any(part.startswith(".") for part in rel_parts):
                continue
            results.add(str(resolve_path(path)))
    except OSError:
        return set()
    return results


class _StreamMonitorEventHandler(FileSystemEventHandler):  # type: ignore[misc]  # follow_imports=skip makes the base Any
    def __init__(self, monitor_id: str, folder_path: str):
        super().__init__()
        self.monitor_id = monitor_id
        self.folder_path = resolve_path(folder_path)

    def _record_path(self, raw_path: str) -> None:
        path = resolve_path(raw_path)
        if path.suffix.lower() != ".nzb":
            return
        try:
            path.relative_to(self.folder_path)
        except ValueError:
            return

        normalized = str(path)
        with _stream_monitor_lock:
            known = _stream_monitor_known.setdefault(self.monitor_id, set())
            if normalized in known:
                return
            pending = _stream_monitor_pending.setdefault(self.monitor_id, {})
            pending[normalized] = time.time()

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._record_path(os.fsdecode(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._record_path(os.fsdecode(event.src_path))

    def on_moved(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._record_path(os.fsdecode(getattr(event, "dest_path", event.src_path)))


def _queue_monitored_stream(entry: dict[str, Any], nzb_path: Path) -> None:
    try:
        from logic.runtime import ensure_engine_started

        service = ensure_engine_started()
        job_id = service.start_usenet_stream_job(
            category=resolve_stream_category(nzb_path, entry.get("category")),
            stream_source_path=str(nzb_path),
            stream_source_name=nzb_path.name,
            test_mode=bool(entry.get("test_mode", False)),
            enable_duplicate_check=bool(entry.get("enable_duplicate_check", True)),
            indexer_id=entry.get("indexer_id"),
            posting_server_name=entry.get("posting_server_name"),
            submit_mode=normalize_submit_mode(entry.get("submit_mode")),
            source_monitor_id=str(entry.get("id") or ""),
        )
        record_stream_monitor_job(str(entry.get("id") or ""), service.get_job(job_id))
        logger.info(f"📡 Stream monitor queued job {job_id} for {nzb_path.name}")
    except Exception as exc:  # pylint: disable=broad-exception-caught  # one bad file must not stop the monitor loop
        logger.error(f"📡 Stream monitor failed to queue {nzb_path}: {exc}")


async def _stream_monitor_loop() -> None:
    while True:
        try:
            await asyncio.sleep(_STREAM_MONITOR_CHECK_INTERVAL)
            now = time.time()
            ready: list[tuple[dict[str, Any], Path]] = []

            with _stream_monitor_lock:
                for monitor_id, files in list(_stream_monitor_pending.items()):
                    entry = _stream_monitor_entries.get(monitor_id)
                    if not entry:
                        continue
                    settled = [path for path, ts in files.items() if (now - ts) >= STREAM_MONITOR_SETTLE_SECONDS]
                    for path_str in settled:
                        files.pop(path_str, None)
                        path = Path(path_str)
                        if not path.exists():
                            continue
                        _stream_monitor_known.setdefault(monitor_id, set()).add(path_str)
                        ready.append((_serialize_monitor(entry), path))

            for entry, nzb_path in ready:
                _queue_monitored_stream(entry, nzb_path)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # pylint: disable=broad-exception-caught  # the watcher loop must outlive any tick
            logger.error(f"📡 Stream monitor loop error: {exc}")
            await asyncio.sleep(5)


async def start_stream_monitors() -> None:
    global _stream_monitor_observer, _stream_monitor_task

    with _stream_monitor_lock:
        _load_stream_monitors_locked()
        entries = list(_stream_monitor_entries.values())

    if not entries:
        logger.debug("📡 Stream monitor: no watched folders configured")
        return
    if _stream_monitor_observer is not None:
        logger.debug("📡 Stream monitor already running")
        return

    watch_specs: list[tuple[Any, Path, bool]] = []
    scheduled = 0
    with _stream_monitor_lock:
        _stream_monitor_pending.clear()
        _stream_monitor_known.clear()
        for entry in entries:
            folder_path = str(entry.get("folder_path") or "")
            folder = Path(folder_path)
            if not folder.exists() or not folder.is_dir():
                logger.warning(f"📡 Stream monitor skipping non-existent folder {folder_path}")
                continue
            _stream_monitor_known[str(entry["id"])] = _scan_monitor_nzb_files(folder_path)
            watch_specs.append((_StreamMonitorEventHandler(str(entry["id"]), folder_path), folder, True))

    observer, scheduled = start_watchdog_observer(watch_specs, observer_factory=Observer)
    if observer is None or scheduled == 0:
        return

    _stream_monitor_observer = observer
    _stream_monitor_task = asyncio.create_task(_stream_monitor_loop())
    logger.info(f"📡 Stream monitor watching {scheduled} folder(s)")


async def stop_stream_monitors() -> None:
    global _stream_monitor_observer, _stream_monitor_task

    if _stream_monitor_task and not _stream_monitor_task.done():
        _stream_monitor_task.cancel()
        try:
            await _stream_monitor_task
        except asyncio.CancelledError:
            pass
    _stream_monitor_task = None

    if _stream_monitor_observer is not None:
        stop_watchdog_observer(_stream_monitor_observer)
        _stream_monitor_observer = None

    with _stream_monitor_lock:
        _stream_monitor_pending.clear()
        _stream_monitor_known.clear()


async def restart_stream_monitors() -> None:
    await stop_stream_monitors()
    await start_stream_monitors()
