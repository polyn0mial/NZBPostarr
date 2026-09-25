"""
Usenet-to-Usenet NZB streaming.

This module re-posts an existing NZB without staging the decoded payload on disk.
It reads article bodies directly from source NNTP servers, decodes yEnc in Python,
and feeds the reconstructed files to Nyuu through procjson:// inputs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Optional, Sequence

from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from core.config import NNTPServer, get_config
from core.database import (
    check_duplicate_dynamic,
    record_nntp_success,
)
from core.registry import get_enabled_indexers
from core.utils import (
    get_thread_job,
    log_info,
    run_command,
    start_watchdog_observer,
    stop_watchdog_observer,
    update_job_progress,
)
from logic.pipeline.posting import (
    _build_nyuu_command,
    _parse_nyuu_completion_stats,
    build_nyuu_progress_parser,
)
from logic.pipeline.submit import submit_and_record


class StreamError(RuntimeError):
    """Base stream failure."""


class ArticleUnavailableError(StreamError):
    """Raised when an article could not be retrieved from any source server."""


class NNTPProtocolError(StreamError):
    """Raised when an NNTP server returns an unexpected response."""


@dataclass(frozen=True, slots=True)
class StreamRequestOptions:
    """Normalized, transport-independent stream request fields."""

    source_path: str
    category: str
    submit_mode: str


STREAM_MONITOR_SETTLE_SECONDS = 15
_STREAM_MONITOR_CHECK_INTERVAL = 5

_stream_monitor_observer: Optional[Observer] = None
_stream_monitor_task: Optional[asyncio.Task[Any]] = None
_stream_monitor_entries: dict[str, dict[str, Any]] = {}
_stream_monitor_pending: dict[str, dict[str, float]] = {}
_stream_monitor_known: dict[str, set[str]] = {}
_stream_monitor_lock = threading.Lock()
_stream_monitor_state_loaded = False


def normalize_submit_mode(raw: Optional[str]) -> str:
    value = str(raw or "post_and_submit").strip().lower()
    if value in {"post_only", "post-only", "post"}:
        return "post_only"
    return "post_and_submit"


def normalize_stream_request(
    *,
    upload_filename: Optional[str],
    source_path: Optional[str],
    monitor_folder: bool,
    category: Optional[str],
    submit_mode: Optional[str],
) -> StreamRequestOptions:
    """Validate mutually exclusive request sources before route orchestration."""
    normalized_source = str(source_path or "").strip()
    normalized_filename = str(upload_filename or "").strip()
    has_upload = bool(normalized_filename)
    has_source = bool(normalized_source)

    if has_upload and has_source:
        raise StreamError("Choose either an uploaded NZB or a server-side path, not both")
    if not has_upload and not has_source:
        raise StreamError("Provide an NZB upload or a server-side path")
    if monitor_folder and has_upload:
        raise StreamError("Folder monitoring requires a server-side folder path")
    if has_upload and not normalized_filename.lower().endswith(".nzb"):
        raise StreamError("Only .nzb files are supported")

    return StreamRequestOptions(
        source_path=normalized_source,
        category=str(category or "").strip(),
        submit_mode=normalize_submit_mode(submit_mode),
    )


def _path_compare_key(path: str | Path) -> str:
    text = str(path)
    return text.casefold() if os.name == "nt" else text


def normalize_source_path(raw_path: str | Path) -> Path:
    path = Path(str(raw_path)).expanduser()
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def resolve_source_nzb_paths(raw_path: str | Path) -> list[Path]:
    """Resolve a server-side NZB file or directory into concrete NZB file paths."""
    source = normalize_source_path(raw_path)
    if not source.exists():
        raise StreamError(f"Source path does not exist: {source}")

    if source.is_file():
        if source.suffix.lower() != ".nzb":
            raise StreamError(f"Source path is not an NZB file: {source}")
        return [source]

    nzb_files = sorted(
        [
            path
            for path in source.rglob("*.nzb")
            if path.is_file() and not any(part.startswith(".") for part in path.relative_to(source).parts)
        ],
        key=lambda item: str(item).lower(),
    )
    if not nzb_files:
        raise StreamError(f"No .nzb files found under: {source}")
    return nzb_files


def resolve_posting_server(
    posting_server_name: Optional[str], servers: Optional[Sequence[NNTPServer]] = None
) -> NNTPServer:
    available = list(servers or _enabled_servers())
    if not available:
        raise StreamError("No enabled NNTP servers are configured")
    if not posting_server_name:
        return available[0]

    wanted = str(posting_server_name).strip().casefold()
    for server in available:
        if str(server.name).strip().casefold() == wanted:
            return server
    raise StreamError(f"Posting server '{posting_server_name}' was not found or is not enabled")


def build_stream_manifest_path(release_name: str) -> Path:
    conf = get_config()
    manifest_dir = Path(conf.script_dir) / "data" / "tmp" / "stream-manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    return manifest_dir / f"{_safe_output_name(release_name)}.{uuid.uuid4().hex[:8]}.json"


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
                {**row, "id": monitor_id, "folder_path": str(normalize_source_path(folder_path))}
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
    folder = normalize_source_path(folder_path)
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
                if _path_compare_key(entry.get("folder_path", "")) == _path_compare_key(folder)
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
            results.add(str(normalize_source_path(path)))
    except OSError:
        return set()
    return results


class _StreamMonitorEventHandler(FileSystemEventHandler):  # type: ignore[misc]
    def __init__(self, monitor_id: str, folder_path: str):
        super().__init__()
        self.monitor_id = monitor_id
        self.folder_path = normalize_source_path(folder_path)

    def _record_path(self, raw_path: str) -> None:
        path = normalize_source_path(raw_path)
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
        from logic.services import UploadService

        service = UploadService()
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
    except Exception as exc:  # pylint: disable=broad-exception-caught
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
        except Exception as exc:  # pylint: disable=broad-exception-caught
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


def _enabled_servers() -> list[NNTPServer]:
    conf = get_config()
    return [server for server in (conf.nntp_servers or []) if getattr(server, "enabled", True)]


def _ensure_message_id(message_id: str) -> str:
    text = str(message_id or "").strip()
    if not text:
        raise StreamError("NZB segment is missing a Message-ID")
    if text.startswith("<") and text.endswith(">"):
        return text
    return f"<{text}>"


def _parse_yenc_kv(line: bytes) -> dict[str, str]:
    text = line.decode("latin-1", errors="replace").strip()
    parts = text.split()
    values: dict[str, str] = {}
    for token in parts[1:]:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        values[key] = value
    return values


def decode_yenc_article(lines: Sequence[bytes]) -> tuple[bytes, dict[str, str]]:
    """Decode one yEnc article body into raw bytes."""
    ybegin: Optional[dict[str, str]] = None
    ypart: dict[str, str] = {}
    payload_lines: list[bytes] = []
    in_payload = False

    for raw in lines:
        line = raw.rstrip(b"\r\n")
        if line.startswith(b"=ybegin"):
            ybegin = _parse_yenc_kv(line)
            in_payload = True
            continue
        if line.startswith(b"=ypart"):
            ypart = _parse_yenc_kv(line)
            continue
        if line.startswith(b"=yend"):
            break
        if in_payload:
            payload_lines.append(line)

    if not ybegin:
        raise StreamError("Article is missing a yEnc header")

    encoded = b"".join(payload_lines)
    decoded = bytearray()
    escaped = False
    for value in encoded:
        if escaped:
            decoded.append((value - 106) % 256)
            escaped = False
            continue
        if value == 61:  # '='
            escaped = True
            continue
        decoded.append((value - 42) % 256)

    meta = dict(ybegin)
    meta.update({f"part_{k}": v for k, v in ypart.items()})
    return bytes(decoded), meta


def _guess_filename(subject: str) -> str:
    match = re.search(r'"([^"]+)"', subject)
    if match:
        return match.group(1).strip()
    subject = re.sub(r"\s+yEnc.*$", "", subject, flags=re.I).strip()
    return subject or "streamed.bin"


def _read_nzb_xml_root(source_path: Path) -> ET.Element:
    payload = source_path.read_bytes()
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]

    try:
        return ET.fromstring(payload)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise StreamError(f"Invalid NZB XML: {exc}") from exc


def read_nzb_head_metadata(source_path: Path, *, root: Optional[ET.Element] = None) -> dict[str, str]:
    if root is None:
        root = _read_nzb_xml_root(source_path)
    metadata: dict[str, str] = {}
    meta_nodes = root.findall(".//{*}head/{*}meta") + root.findall(".//head/meta")
    for meta_node in meta_nodes:
        key = str(meta_node.attrib.get("type") or "").strip().lower()
        value = str(meta_node.text or "").strip()
        if key and value and key not in metadata:
            metadata[key] = value
    return metadata


def _normalize_stream_category(raw: Optional[str]) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    primary = re.split(r"\s*(?:>|/|\\|\||:)\s*", text, maxsplit=1)[0].strip()
    slug = re.sub(r"[^a-z0-9]+", "_", primary.lower()).strip("_")

    if "anime" in lowered or slug == "anime":
        return "anime"
    if slug in {"movie", "movies", "film", "films"} or "movie" in lowered:
        return "movies"
    if slug in {"tv", "television", "show", "shows", "series"}:
        return "tv"
    if any(token in lowered for token in ("television", "series", "episode", "season")):
        return "tv"
    return slug or "misc"


def resolve_stream_category(source_path: Path, explicit_category: Optional[str] = None) -> str:
    explicit = _normalize_stream_category(explicit_category)
    if explicit:
        return explicit
    metadata = read_nzb_head_metadata(source_path)
    detected = _normalize_stream_category(metadata.get("category"))
    return detected or "misc"


def parse_nzb_structure(source_path: Path) -> dict[str, Any]:
    """Parse the NZB XML into a compact manifest skeleton."""
    root = _read_nzb_xml_root(source_path)
    metadata = read_nzb_head_metadata(source_path, root=root)

    files: list[dict[str, Any]] = []
    for file_node in root.findall(".//{*}file") + root.findall(".//file"):
        subject = str(file_node.attrib.get("subject") or "").strip()
        poster = str(file_node.attrib.get("poster") or "").strip()
        date = str(file_node.attrib.get("date") or "").strip()
        groups = [grp.text.strip() for grp in file_node.findall(".//{*}group") if grp.text and grp.text.strip()]
        if not groups:
            groups = [grp.text.strip() for grp in file_node.findall(".//group") if grp.text and grp.text.strip()]

        segments: list[dict[str, Any]] = []
        segment_nodes = file_node.findall(".//{*}segment") or file_node.findall(".//segment")
        for segment_node in segment_nodes:
            message_id = _ensure_message_id(segment_node.text or "")
            try:
                number = int(segment_node.attrib.get("number") or 0)
            except (TypeError, ValueError):
                number = 0
            try:
                article_bytes = int(segment_node.attrib.get("bytes") or 0)
            except (TypeError, ValueError):
                article_bytes = 0
            segments.append(
                {
                    "number": number,
                    "bytes": article_bytes,
                    "message_id": message_id,
                }
            )

        segments.sort(key=lambda entry: (entry["number"], entry["message_id"]))
        if not segments:
            continue

        files.append(
            {
                "subject": subject,
                "poster": poster,
                "date": date,
                "groups": groups,
                "name": _guess_filename(subject),
                "size": 0,
                "segments": segments,
            }
        )

    if not files:
        raise StreamError("NZB does not contain any <file> entries")

    return {
        "source_path": str(source_path),
        "source_name": source_path.name,
        "release_name": source_path.stem,
        "metadata": metadata,
        "files": files,
        "total_size": 0,
    }


class SimpleNNTPConnection:
    """Tiny NNTP client for BODY retrieval.

    Python 3.14 removed stdlib nntplib, so this keeps the feature self-contained.
    """

    def __init__(self, server: NNTPServer, timeout: float = 60.0):
        self.server = server
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.reader: Optional[BinaryIO] = None
        self.writer: Optional[BinaryIO] = None
        self._connect()

    def _connect(self) -> None:
        sock = socket.create_connection((self.server.host, int(self.server.port)), timeout=self.timeout)
        sock.settimeout(self.timeout)
        if self.server.ssl:
            context = ssl.create_default_context()
            sock = context.wrap_socket(sock, server_hostname=self.server.host)
        self.sock = sock
        self.reader = sock.makefile("rb")
        self.writer = sock.makefile("wb")

        code, message = self._read_status_line()
        if code not in {200, 201}:
            raise NNTPProtocolError(f"{self.server.name}: unexpected greeting {code} {message}")

        self._send_command("MODE READER", ok_codes={200, 201, 480, 500})
        self._send_command(f"AUTHINFO USER {self.server.user}", ok_codes={281, 381})
        self._send_command(f"AUTHINFO PASS {self.server.password}", ok_codes={281})

    def close(self) -> None:
        try:
            if self.writer is not None:
                try:
                    self.writer.write(b"QUIT\r\n")
                    self.writer.flush()
                except OSError:
                    pass
        finally:
            for stream in (self.reader, self.writer, self.sock):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
            self.reader = None
            self.writer = None
            self.sock = None

    def _readline(self) -> bytes:
        if self.reader is None:
            raise NNTPProtocolError(f"{self.server.name}: connection is closed")
        line = self.reader.readline()
        if not line:
            raise NNTPProtocolError(f"{self.server.name}: connection closed unexpectedly")
        return line

    def _read_status_line(self) -> tuple[int, str]:
        line = self._readline().decode("latin-1", errors="replace").rstrip("\r\n")
        if len(line) < 3 or not line[:3].isdigit():
            raise NNTPProtocolError(f"{self.server.name}: invalid NNTP response {line!r}")
        return int(line[:3]), line[4:] if len(line) > 4 else ""

    def _send_command(self, command: str, ok_codes: set[int]) -> tuple[int, str]:
        if self.writer is None:
            raise NNTPProtocolError(f"{self.server.name}: connection is closed")
        self.writer.write(command.encode("latin-1", errors="replace") + b"\r\n")
        self.writer.flush()
        code, message = self._read_status_line()
        if code not in ok_codes:
            raise NNTPProtocolError(f"{self.server.name}: {command} -> {code} {message}")
        return code, message

    def body(self, message_id: str) -> list[bytes]:
        if self.writer is None:
            raise NNTPProtocolError(f"{self.server.name}: connection is closed")

        self.writer.write(f"BODY {message_id}\r\n".encode("latin-1", errors="replace"))
        self.writer.flush()
        code, message = self._read_status_line()
        if code == 430:
            raise ArticleUnavailableError(f"{self.server.name}: article not found: {message_id}")
        if code != 222:
            raise NNTPProtocolError(f"{self.server.name}: BODY -> {code} {message}")

        lines: list[bytes] = []
        while True:
            line = self._readline()
            if line in {b".\r\n", b".\n", b"."}:
                break
            if line.startswith(b".."):
                line = line[1:]
            lines.append(line)
        return lines


class UsenetReader:
    """Article reader with provider fallback."""

    def __init__(self, servers: Optional[Sequence[NNTPServer]] = None):
        self.servers = list(servers or _enabled_servers())
        if not self.servers:
            raise StreamError("No enabled NNTP servers are configured")
        self._connections: dict[str, SimpleNNTPConnection] = {}
        self._preferred: Optional[str] = None

    def close(self) -> None:
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()

    def _get_connection(self, server: NNTPServer) -> SimpleNNTPConnection:
        conn = self._connections.get(server.name)
        if conn is None:
            conn = SimpleNNTPConnection(server)
            self._connections[server.name] = conn
        return conn

    def body(self, message_id: str) -> list[bytes]:
        ordered = list(self.servers)
        if self._preferred:
            ordered.sort(key=lambda srv: 0 if srv.name == self._preferred else 1)

        last_error: Optional[Exception] = None
        for server in ordered:
            try:
                conn = self._get_connection(server)
                lines = conn.body(message_id)
                self._preferred = server.name
                return lines
            except ArticleUnavailableError as exc:
                last_error = exc
                continue
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                old = self._connections.pop(server.name, None)
                if old is not None:
                    old.close()
                continue

        if last_error:
            raise ArticleUnavailableError(str(last_error)) from last_error
        raise ArticleUnavailableError(f"Article unavailable across all servers: {message_id}")


def probe_file_details(file_entry: dict[str, Any], reader: Optional[UsenetReader] = None) -> tuple[str, int]:
    """Determine the exact filename and raw size from the first segment's yEnc header."""
    own_reader = reader is None
    reader = reader or UsenetReader()
    try:
        lines = reader.body(file_entry["segments"][0]["message_id"])
        _, meta = decode_yenc_article(lines)
        name = str(meta.get("name") or file_entry.get("name") or _guess_filename(file_entry.get("subject", ""))).strip()
        try:
            size = int(meta.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if size <= 0:
            size = sum(int(seg.get("bytes") or 0) for seg in file_entry.get("segments", []))
        if size <= 0:
            raise StreamError(f"Unable to determine file size for {name}")
        return name, size
    finally:
        if own_reader:
            reader.close()


def prepare_stream_manifest(source_path: Path, release_name: Optional[str] = None) -> dict[str, Any]:
    manifest = parse_nzb_structure(source_path)
    reader = UsenetReader()
    try:
        total_size = 0
        for file_entry in manifest["files"]:
            name, size = probe_file_details(file_entry, reader=reader)
            file_entry["name"] = name
            file_entry["size"] = size
            total_size += size
        manifest["total_size"] = total_size
    finally:
        reader.close()

    if release_name:
        manifest["release_name"] = release_name.strip()
    return manifest


def write_stream_manifest(manifest: dict[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return destination


def _build_helper_command(manifest_path: Path, file_index: int) -> str:
    args = [
        sys.executable,
        "-m",
        "logic.usenet_stream",
        "emit",
        "--manifest",
        str(manifest_path),
        "--file-index",
        str(file_index),
    ]
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def build_procjson_inputs(manifest_path: Path, manifest: dict[str, Any]) -> list[str]:
    inputs: list[str] = []
    for idx, file_entry in enumerate(manifest.get("files", [])):
        command = _build_helper_command(manifest_path, idx)
        proc_spec = [file_entry["name"], int(file_entry["size"]), command]
        inputs.append("procjson://" + json.dumps(proc_spec, separators=(",", ":")))
    return inputs


def _safe_output_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return clean or "stream"


def _stream_itype(category: str) -> str:
    normalized = str(category or "misc").strip().lower()
    if normalized == "movies":
        return "Movie"
    if normalized == "tv":
        return "TV Show"
    return normalized.replace("_", " ").title() or "Misc"


def _target_indexers(target_indexer_id: Optional[str]) -> list[str]:
    conf = get_config()
    indexers = get_enabled_indexers(conf)
    if target_indexer_id:
        indexers = [idx for idx in indexers if idx.id == target_indexer_id]
    return [idx.id for idx in indexers]


def upload_stream_manifest(
    manifest_path: Path,
    release_name: str,
    server: NNTPServer,
    total_size: int,
    *,
    nzb_path: Optional[Path] = None,
) -> tuple[Optional[dict[str, Any]], Optional[Path]]:
    """Post a prepared stream manifest to Usenet using Nyuu."""
    conf = get_config()
    job = get_thread_job()
    if job:
        job["current_stage"] = f"STREAMING ({server.name})"

    generated_nzb = nzb_path or conf.get_nzb_path(_safe_output_name(release_name))
    generated_nzb.parent.mkdir(parents=True, exist_ok=True)
    if generated_nzb.exists():
        generated_nzb.unlink(missing_ok=True)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = build_procjson_inputs(manifest_path, manifest)
    if not inputs:
        raise StreamError("Prepared stream manifest does not contain any files")

    cmd = _build_nyuu_command(
        conf,
        server,
        generated_nzb,
        inputs,
        server.max_connections,
        check_connections=3,
        check_tries=5,
        group_pool=conf.alt_bins or ["alt.binaries.misc"],
    )

    update_job_progress(
        msg=f"Streaming {release_name} via {server.name}...",
        item_name=release_name,
        item_percent=0,
        item_size=total_size,
        speed="Starting...",
        eta="Starting...",
    )

    parser = build_nyuu_progress_parser(
        server_name=server.name,
        total_size=total_size,
        article_size=conf.article_size,
        action_label="Streaming",
        completion_label="Stream complete",
        verbose=bool(getattr(conf, "verbose", False)),
    )
    success, output = run_command(cmd, f"Nyuu-{server.name}", job, parser=parser, quiet=True)
    if not success:
        detail = next((line for line in reversed(output) if line), "")
        if detail:
            raise StreamError(detail)
        return None, generated_nzb

    if not generated_nzb.exists() or generated_nzb.stat().st_size < 100:
        actual_size = generated_nzb.stat().st_size if generated_nzb.exists() else 0
        raise StreamError(f"Nyuu finished but produced an invalid NZB ({actual_size} bytes)")

    update_job_progress(item_percent=100, eta="0s", msg=f"[{server.name}] Stream complete")
    return _parse_nyuu_completion_stats(output, total_size, server.name), generated_nzb


def stream_nzb_upload(
    *,
    source_path: Path,
    category: str,
    release_name: Optional[str] = None,
    target_indexer_id: Optional[str] = None,
    posting_server_name: Optional[str] = None,
    submit_mode: str = "post_and_submit",
    force: bool = False,
    test_mode: bool = False,
    manifest_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Stream an NZB payload from Usenet back to Usenet without staging decoded files on disk."""
    conf = get_config()
    servers = _enabled_servers()
    if not servers:
        raise StreamError("No enabled NNTP servers are configured")

    if not source_path.exists():
        raise StreamError(f"Source NZB does not exist: {source_path}")

    submit_mode = normalize_submit_mode(submit_mode)
    primary_server = resolve_posting_server(posting_server_name, servers)
    chosen_release = (release_name or source_path.stem).strip() or source_path.stem
    itype = _stream_itype(category)
    target_ids = [] if submit_mode == "post_only" else _target_indexers(target_indexer_id)
    manifest_path = manifest_path or build_stream_manifest_path(chosen_release)
    generated_nzb: Optional[Path] = None

    manifest = prepare_stream_manifest(source_path, chosen_release)
    write_stream_manifest(manifest, manifest_path)

    total_size = int(manifest.get("total_size") or 0)
    if total_size <= 0:
        raise StreamError("Unable to determine the streamed release size from the NZB")

    update_job_progress(total=1, processed=0, skipped=0, percent=0)

    if target_ids and not force:
        dupes = check_duplicate_dynamic(chosen_release, itype, target_ids)
        if all(dupes.get(dest) is not None for dest in target_ids):
            log_info(f"Skipping stream for {chosen_release}: already present on all target indexers.")
            update_job_progress(processed=0, skipped=1, percent=100, msg="Skipped - already uploaded")
            return {
                "status": "skipped",
                "release_name": chosen_release,
                "total_size": total_size,
                "manifest_path": str(manifest_path),
                "posting_server_name": primary_server.name,
                "submit_mode": submit_mode,
            }

    if test_mode:
        log_info(f"[TEST MODE] Parsed NZB stream manifest for {chosen_release} ({len(manifest['files'])} files)")
        update_job_progress(processed=1, skipped=0, percent=100, msg="Test mode complete")
        return {
            "status": "test",
            "release_name": chosen_release,
            "files": len(manifest["files"]),
            "total_size": total_size,
            "manifest_path": str(manifest_path),
            "posting_server_name": primary_server.name,
            "submit_mode": submit_mode,
        }

    upload_result, generated_nzb = upload_stream_manifest(
        manifest_path,
        chosen_release,
        primary_server,
        total_size,
        nzb_path=conf.get_nzb_path(_safe_output_name(f"{chosen_release}.stream")),
    )
    if not upload_result or generated_nzb is None:
        raise StreamError("Streaming post failed before Nyuu produced a usable NZB")

    record_nntp_success(chosen_release, total_size, itype)

    # Same submit semantics as queue posting: per-indexer isolation, and a
    # "duplicate" answer counts as already posted.
    api_results, _any_success = submit_and_record(
        [{"dests": list(target_ids), "priority": False}] if target_ids else [],
        conf=conf,
        name=chosen_release,
        nzb_path=generated_nzb,
        submission_category=category,
        item_size=total_size,
        key=chosen_release,
        itype=itype,
        item_path=Path(chosen_release),
        base_folder=None,
        category=category,
        test_mode=False,
        upload_result=upload_result,
    )
    submission_results = [(dest, ok or status == "duplicate", reason) for dest, ok, reason, status in api_results]

    success_count = sum(1 for _dest, ok, _reason in submission_results if ok)
    log_info(
        f"Streamed {chosen_release}: {len(manifest['files'])} files, {total_size} bytes, "
        f"{success_count}/{len(submission_results)} indexers accepted."
    )
    update_job_progress(processed=1, skipped=0, percent=100, msg="Stream complete")

    return {
        "status": "completed",
        "release_name": chosen_release,
        "files": len(manifest["files"]),
        "total_size": total_size,
        "indexer_results": submission_results,
        "manifest_path": str(manifest_path),
        "generated_nzb": str(generated_nzb),
        "posting_server_name": primary_server.name,
        "submit_mode": submit_mode,
    }


def emit_manifest_file(manifest_path: Path, file_index: int) -> int:
    """Helper entrypoint executed by Nyuu procjson:// inputs."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files", [])
    if file_index < 0 or file_index >= len(files):
        raise StreamError(f"File index out of range: {file_index}")

    file_entry = files[file_index]
    expected_size = int(file_entry.get("size") or 0)
    if expected_size <= 0:
        raise StreamError(f"Manifest entry does not have a valid size: {file_entry.get('name')}")

    reader = UsenetReader()
    bytes_written = 0
    try:
        for segment in file_entry.get("segments", []):
            lines = reader.body(segment["message_id"])
            payload, _meta = decode_yenc_article(lines)
            sys.stdout.buffer.write(payload)
            bytes_written += len(payload)
        sys.stdout.buffer.flush()
    finally:
        reader.close()

    if bytes_written != expected_size:
        raise StreamError(
            f"Decoded size mismatch for {file_entry.get('name')}: expected {expected_size}, wrote {bytes_written}"
        )
    return 0


def _cli() -> int:
    parser = argparse.ArgumentParser(prog="python -m logic.usenet_stream")
    subparsers = parser.add_subparsers(dest="command", required=True)

    emit_parser = subparsers.add_parser("emit", help="Emit one streamed file to stdout")
    emit_parser.add_argument("--manifest", required=True)
    emit_parser.add_argument("--file-index", required=True, type=int)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect an NZB and print the prepared manifest summary")
    inspect_parser.add_argument("source")
    inspect_parser.add_argument("--release-name")

    args = parser.parse_args()
    if args.command == "emit":
        return emit_manifest_file(Path(args.manifest), int(args.file_index))

    manifest = prepare_stream_manifest(Path(args.source), args.release_name)
    print(
        json.dumps(
            {
                "release_name": manifest["release_name"],
                "files": len(manifest["files"]),
                "total_size": manifest["total_size"],
                "source_name": manifest["source_name"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
