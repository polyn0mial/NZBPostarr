import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import psutil
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from core import database
from core.config import get_config
from core.utils import (
    atomic_write_text,
    log_info,
    log_success,
    normalize_submission_category,
    reset_thread_job,
    set_thread_job,
)
try:
    from logic.pending_scan import looks_like_generic_tv_season_folder
except Exception:
    def looks_like_generic_tv_season_folder(name: str) -> bool:
        s = str(name or "")
        return bool(
            re.search(
                r"(?i)(?:^|[\s._-])season[\s._-]*\d{1,2}(?:$|[\s._-])|(?:^|[\s._-])s\d{1,2}(?:$|[\s._-])",
                s,
            )
        ) and not bool(re.search(r"(?i)s\d{1,2}[\s._-]*e\d{1,3}|\d{1,2}x\d{1,3}", s))
from logic import usenet_stream


_QUEUE_SOURCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:WEB(?:[.\s_-]?DL|[.\s_-]?Rip|[.\s_-]?HD)?|WEBDL|WEBRip|WEBHD|BluRay|BDRip|BRRip|REMUX|HDRip|PDRip|HDTV|PDTV|"
    r"SDTV|TVRip|SATRip|DSR|DVB|DVDRip|DVD|VHS(?:Rip)?|DV|UHD|AMZN|NF|NFLX|DSNP|PCOK|HMAX|MAX|HULU|ATVP|AUBC|iT|iP|STAN|CR|"
    r"PMTP|PMNT|CTV|CBC|BBC|PBS|TBS|TNT|NBC|ABC|CBS|FOX|HBO|SHOWTIME|SHO)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_FINISHED_JOB_RETENTION_COUNT = 100
_FINISHED_JOB_RETENTION_DAYS = 30
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})


class JobState(BaseModel):
    """Canonical job state shape for in-memory tracking and API serialization."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    category: str
    status: str
    started_at: str
    created_at: Optional[str] = None

    items_processed: int = 0
    items_total: int = 0
    items_skipped: int = 0
    total_bytes: int = 0

    progress: str = "Starting..."
    progress_percent: int = 0

    current_item: Optional[str] = None
    item_percent: int = 0
    speed: Optional[str] = None
    eta: Optional[str] = None
    current_stage: Optional[str] = "INITIALIZING"
    test_mode: bool = False
    display_name: Optional[str] = None
    run_after: Optional[str] = None
    priority: int = 0
    attempt_count: int = 0
    retry_of: Optional[str] = None
    retried_as: Optional[str] = None
    retry_eligible: bool = False
    last_error: Optional[str] = None
    events: list[dict[str, str]] = Field(default_factory=list)


@dataclass(frozen=True)
class ProcessingJobRequest:
    """Normalized execution request for a processing job."""

    category: str
    limit: Optional[int] = None
    skip_packs: bool = False
    skip_episodes: bool = False
    test_mode: bool = False
    target_indexer_id: Optional[str] = None
    target_indexer_ids: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    item_hints: tuple[dict[str, Any], ...] = ()
    enable_duplicate_check: Optional[bool] = None
    force: Optional[bool] = None


@dataclass(frozen=True)
class StreamJobRequest:
    """Normalized execution request for a streamed NZB repost job."""

    category: str
    source_path: Optional[str]
    release_name: Optional[str] = None
    test_mode: bool = False
    target_indexer_id: Optional[str] = None
    posting_server_name: Optional[str] = None
    submit_mode: str = "post_and_submit"
    enable_duplicate_check: Optional[bool] = None


@dataclass(frozen=True)
class QueueStartSummary:
    """Prepared queue-start state after staged-item validation."""

    runnable_items: tuple[dict[str, Any], ...]
    skipped_items: tuple[tuple[dict[str, Any], str], ...]


class QueueServiceMixin:
    _QUEUE_INVALID_CATEGORY_VALUES = {"", "external", "null", "none", "unknown", "undefined"}

    @staticmethod
    def _normalize_job_source(source: Any) -> str:
        cleaned = " ".join(str(source or "").strip().split())
        return cleaned[:80] if cleaned else "unknown"

    @classmethod
    def _normalize_queue_category(cls, value: Any) -> str:
        return normalize_submission_category(value)

    @classmethod
    def _queue_item_category_from_itype(cls, value: Any) -> str:
        return cls._normalize_queue_category(value)

    @staticmethod
    def _queue_item_label(item: dict[str, Any], fallback_index: int) -> str:
        name = str(item.get("name") or "").strip()
        path_text = str(item.get("path") or "").strip()
        if name:
            return name
        if path_text:
            return Path(path_text).name or path_text
        return f"item #{fallback_index}"

    @classmethod
    def _derive_queue_item_category(cls, item: dict[str, Any]) -> tuple[str, str]:
        manual_category = cls._normalize_queue_category(item.get("manual_category"))
        path_text = str(item.get("path") or "").strip()
        if path_text:
            try:
                from logic.pending_scan import resolve_explicit_path

                resolved = resolve_explicit_path(
                    Path(path_text),
                    category_hint=manual_category,
                    itype_hint=str(item.get("itype") or "") if manual_category else "",
                    respect_explicit_hint=bool(manual_category),
                )
                resolved_category = cls._normalize_queue_category(resolved.category)
                if resolved_category and resolved_category not in cls._QUEUE_INVALID_CATEGORY_VALUES:
                    source = "manual_category" if manual_category else f"path:{resolved.detection_method}"
                    return resolved_category, source
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Queue item category recovery failed for {path_text}: {exc}")
            return "", ""

        detected_category = cls._normalize_queue_category(item.get("detected_category"))
        itype_category = cls._queue_item_category_from_itype(item.get("itype"))
        explicit_category = cls._normalize_queue_category(item.get("category"))
        for candidate, source in (
            (manual_category, "manual_category"),
            (detected_category, "detected_category"),
            (itype_category, "itype"),
            (explicit_category, "category"),
        ):
            if candidate and candidate not in cls._QUEUE_INVALID_CATEGORY_VALUES:
                return candidate, source

        return "", ""

    @classmethod
    def _prepare_queue_start_items(cls, items: list[dict[str, Any]]) -> QueueStartSummary:
        from logic.pending_scan import begin_scan_cache, end_scan_cache

        runnable_items: list[dict[str, Any]] = []
        skipped_items: list[tuple[dict[str, Any], str]] = []

        # Many staged items in the same root share filesystem walks; reuse a
        # per-call cache so resolve_explicit_path doesn't re-walk every item.
        _cache_token = begin_scan_cache()
        try:
            return cls._prepare_queue_start_items_inner(items, runnable_items, skipped_items)
        finally:
            end_scan_cache(_cache_token)

    @classmethod
    def _prepare_queue_start_items_inner(
        cls,
        items: list[dict[str, Any]],
        runnable_items: list[dict[str, Any]],
        skipped_items: list[tuple[dict[str, Any], str]],
    ) -> QueueStartSummary:
        for index, item in enumerate(items, start=1):
            queue_item = dict(item) if isinstance(item, dict) else {}

            if bool(queue_item.get("auto_select_ignored")):
                skipped_items.append((queue_item, "ignored item"))
                continue

            path_text = str(queue_item.get("path") or "").strip()
            if not path_text:
                skipped_items.append((queue_item, "missing path"))
                continue
            if not Path(path_text).exists():
                skipped_items.append((queue_item, "path not found"))
                continue

            category, source = cls._derive_queue_item_category(queue_item)
            if not category or category in cls._QUEUE_INVALID_CATEGORY_VALUES:
                skipped_items.append((queue_item, "missing category"))
                continue
            if category == "misc":
                skipped_items.append((queue_item, "category resolved to MISC"))
                continue

            queue_item["category"] = category
            if not str(queue_item.get("detected_category") or "").strip():
                queue_item["detected_category"] = category
            if source:
                queue_item["queue_category_source"] = source
            logger.debug(
                f"Queue start prepared '{cls._queue_item_label(queue_item, index)}' as "
                f"{category} via {source or 'unknown'}"
            )
            runnable_items.append(queue_item)

        return QueueStartSummary(
            runnable_items=tuple(cls._inject_inferred_tv_pack_queue_items(runnable_items)),
            skipped_items=tuple(skipped_items),
        )

    @staticmethod
    def _queue_item_path_text(item: dict[str, Any]) -> str:
        return str(item.get("path") or "").strip()

    @classmethod
    def _has_queue_source_token(cls, text: str) -> bool:
        return bool(_QUEUE_SOURCE_TOKEN_RE.search(str(text or "")))

    @classmethod
    def _looks_like_queue_tv_pack_folder(cls, path: Path, episode_paths: list[Path]) -> bool:
        if not path.is_dir() or len(episode_paths) < 2:
            return False
        folder_name = path.name.lower()
        if not cls._has_queue_source_token(folder_name):
            return False
        if looks_like_generic_tv_season_folder(folder_name):
            return False
        if re.search(r"(?:^|[^a-z0-9])s\d{1,2}(?:[^a-z0-9]|$)", folder_name):
            return True
        if any(token in folder_name for token in ("season", "complete")):
            return True
        return False

    @classmethod
    def _inject_inferred_tv_pack_queue_items(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert season-folder pack rows before staged episode files, and expand
        submitted season-folder items with their child episode files after the pack."""
        import re as _re
        _VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts"}
        _EPISODE_RE = _re.compile(r"[Ss]\d{1,2}[Ee]\d{1,2}")
        TV_CATS = {"tv", "anime"}

        existing_dirs = {
            os.path.normpath(cls._queue_item_path_text(item))
            for item in items
            if str(item.get("category") or "").strip().lower() in TV_CATS
            and cls._queue_item_path_text(item)
            and Path(cls._queue_item_path_text(item)).is_dir()
        }
        episodes_by_parent: dict[Path, list[Path]] = {}
        for item in items:
            if str(item.get("category") or "").strip().lower() not in TV_CATS:
                continue
            path_text = cls._queue_item_path_text(item)
            if not path_text:
                continue
            path = Path(path_text)
            if path.is_file():
                episodes_by_parent.setdefault(path.parent, []).append(path)

        pack_parents = {
            parent
            for parent, episode_paths in episodes_by_parent.items()
            if os.path.normpath(str(parent)) not in existing_dirs
            and cls._looks_like_queue_tv_pack_folder(parent, episode_paths)
        }

        episodes_already_included: set[str] = {
            os.path.normpath(cls._queue_item_path_text(it))
            for it in items
            if cls._queue_item_path_text(it) and Path(cls._queue_item_path_text(it)).is_file()
        }

        injected: list[dict[str, Any]] = []
        inserted: set[Path] = set()
        for item in items:
            path_text = cls._queue_item_path_text(item)
            path = Path(path_text) if path_text else Path(".")
            parent = path.parent if path.is_file() else None
            if parent in pack_parents and parent not in inserted:
                injected.append(cls._build_inferred_pack_row(parent))
                inserted.add(parent)
            injected.append(item)
            # Expand submitted pack folder items with their child episode files
            if (
                path_text
                and path.is_dir()
                and str(item.get("category") or "").strip().lower() in TV_CATS
            ):
                cat = str(item.get("category") or "tv").strip().lower()
                injected.extend(
                    cls._expand_queue_pack_children(path, cat, _VIDEO_EXTS, _EPISODE_RE, episodes_already_included)
                )

        if inserted:
            logger.info(f"[QUEUE-START] inferred {len(inserted)} TV season pack row(s) from staged episodes")
        return injected

    @staticmethod
    def _build_inferred_pack_row(parent: Path) -> dict[str, Any]:
        return {
            "path": str(parent),
            "name": parent.name,
            "category": "tv",
            "detected_category": "tv",
            "itype": "TV Show",
            "is_dir": True,
            "queue_category_source": "inferred-season-pack",
        }

    @staticmethod
    def _expand_queue_pack_children(
        path: Path,
        cat: str,
        video_exts: set[str],
        episode_re: Any,
        episodes_already_included: set[str],
    ) -> list[dict[str, Any]]:
        """Build injected episode rows for video files under a submitted pack folder."""
        expanded: list[dict[str, Any]] = []
        try:
            child_eps = sorted(
                (
                    child for child in path.iterdir()
                    if child.is_file()
                    and child.suffix.lower() in video_exts
                    and episode_re.search(child.name)
                    and os.path.normpath(str(child)) not in episodes_already_included
                ),
                key=lambda p: p.name.lower(),
            )
            for ep in child_eps:
                expanded.append({
                    "path": str(ep),
                    "name": ep.name,
                    "category": cat,
                    "detected_category": cat,
                    "itype": "TV Episode",
                    "is_dir": False,
                    "queue_category_source": "inferred-from-pack",
                })
                episodes_already_included.add(os.path.normpath(str(ep)))
            if child_eps:
                logger.info(f"[QUEUE-START] expanded {path.name}: {len(child_eps)} episode(s) injected")
        except OSError:
            pass
        return expanded

    @staticmethod
    def _build_processing_request(category: str, kwargs: dict[str, Any], paths: list[str]) -> ProcessingJobRequest:
        return ProcessingJobRequest(
            category=category,
            limit=kwargs.get("limit"),
            skip_packs=bool(kwargs.get("skip_packs", False)),
            skip_episodes=bool(kwargs.get("skip_episodes", False)),
            test_mode=bool(kwargs.get("test_mode", False)),
            target_indexer_id=kwargs.get("indexer_id"),
            target_indexer_ids=tuple(str(value) for value in (kwargs.get("indexer_ids") or []) if str(value).strip()),
            paths=tuple(paths),
            item_hints=tuple(dict(item) for item in (kwargs.get("item_hints") or []) if isinstance(item, dict)),
            enable_duplicate_check=kwargs.get("enable_duplicate_check", True),
            force=kwargs.get("force"),
        )

    @staticmethod
    def _build_stream_request(category: str, kwargs: dict[str, Any]) -> StreamJobRequest:
        return StreamJobRequest(
            category=category,
            source_path=kwargs.get("stream_source_path"),
            release_name=kwargs.get("release_name"),
            test_mode=bool(kwargs.get("test_mode", False)),
            target_indexer_id=kwargs.get("indexer_id"),
            posting_server_name=kwargs.get("posting_server_name"),
            submit_mode=str(kwargs.get("submit_mode", "post_and_submit") or "post_and_submit"),
            enable_duplicate_check=kwargs.get("enable_duplicate_check", True),
        )

    def _consume_job_launch_state_locked(self, job: dict[str, Any]) -> tuple[str, Any, list[str], Optional[str]]:
        stored_kwargs = job.get("_kwargs", {})
        if not isinstance(stored_kwargs, dict):
            stored_kwargs = {}
        kwargs = dict(stored_kwargs)

        paths = self._normalize_paths(job.get("_paths"))
        if not paths:
            paths = self._normalize_paths(job.get("target_paths"))
        job["_snapshot_paths"] = list(paths)
        job.pop("_paths", None)
        job_type = str(kwargs.get("job_type") or job.get("job_type") or "processing")
        cleanup_paths = self._normalize_paths(kwargs.pop("cleanup_paths", None))
        source_monitor_id = str(kwargs.get("source_monitor_id") or job.get("source_monitor_id") or "") or None

        job["job_type"] = job_type
        if cleanup_paths:
            job["cleanup_paths"] = cleanup_paths
        if source_monitor_id:
            job["source_monitor_id"] = source_monitor_id

        if job_type == "usenet_stream":
            request = self._build_stream_request(str(job.get("category") or "misc"), kwargs)
        else:
            request = self._build_processing_request(str(job.get("category") or "misc"), kwargs, paths)

        return job_type, request, cleanup_paths, source_monitor_id

    @staticmethod
    def _record_job_event(job: dict[str, Any], event_type: str, message: str) -> None:
        events = job.get("events")
        if not isinstance(events, list):
            events = []
        events.append(
            {
                "at": datetime.now(timezone.utc).isoformat(),
                "type": str(event_type),
                "message": str(message)[:240],
            }
        )
        job["events"] = events[-30:]

    @staticmethod
    def _build_retry_request(kwargs: dict[str, Any], paths: list[str]) -> dict[str, Any]:
        retry_kwargs = {
            key: value
            for key, value in kwargs.items()
            if key not in {"cleanup_paths", "source_monitor_id", "manifest_path"}
        }
        # The job-state file is JSON, so normalize Path/tuple/model-like values
        # at creation instead of discovering an unserializable retry later.
        normalized_kwargs = json.loads(json.dumps(retry_kwargs, default=str))
        return {"kwargs": normalized_kwargs, "paths": list(paths)}

    def _set_job_running_state_locked(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        job["progress"] = "Starting..."
        job["speed"] = "Starting..."
        job["eta"] = "Starting..."
        job["current_stage"] = "INITIALIZING"
        job["stop_requested"] = False
        job["pause_requested"] = False
        job["started_at"] = datetime.now(timezone.utc).isoformat()
        job["attempt_count"] = int(job.get("attempt_count") or 0) + 1
        self._record_job_event(
            job,
            "started",
            f"Attempt {job['attempt_count']} started",
        )

    def _dispatch_job_execution(self, job: dict[str, Any], request: Any) -> None:
        if isinstance(request, StreamJobRequest):
            self._execute_usenet_stream_job(job, request)
            return
        self._execute_processing_job(job, request)

    def _initialize_queue_state(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, list[Any]] = {}
        self._suspended_pids: dict[str, set[int]] = {}
        self._queue_processing_paused = False
        self._queue_scheduler_stop = threading.Event()
        self._queue_scheduler_thread: Optional[threading.Thread] = None
        self._queue_items: list[dict[str, Any]] = database.db_load_queue()

        state_dir = get_config().script_dir / "data" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_state_path = state_dir / "job_queue_state.json"
        self._jobs_state_backup_path = state_dir / "job_queue_state.json.bak"

        with self._lock:
            self._restore_jobs_from_disk_locked()

        self._queue_scheduler_thread = threading.Thread(target=self._queue_scheduler_loop, daemon=True)
        self._queue_scheduler_thread.start()
        self._try_start_queued()

    @staticmethod
    def _normalize_paths(paths: Any) -> list[str]:
        if not paths:
            return []
        if isinstance(paths, (str, Path)):
            return [str(paths)]
        normalized: list[str] = []
        for value in paths:
            text = str(value).strip()
            if text:
                normalized.append(text)
        return normalized

    @staticmethod
    def group_item_paths_by_category(
        items: list[dict[str, Any]],
        default_category: str = "",
    ) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for item in items:
            category = str(item.get("category") or default_category).strip()
            path = item.get("path")
            if not path or not category:
                continue
            grouped.setdefault(category, []).append(str(path))
        return grouped

    def start_path_jobs(self, grouped_paths: dict[str, list[str]], **kwargs: Any) -> list[dict[str, Any]]:
        """Start one queued job per category for a grouped set of explicit paths."""
        started: list[dict[str, Any]] = []
        for category, raw_paths in grouped_paths.items():
            paths = self._normalize_paths(raw_paths)
            if not paths:
                continue
            request = ProcessingJobRequest(
                category=str(category or "misc"),
                paths=tuple(paths),
                test_mode=bool(kwargs.get("test_mode", False)),
                target_indexer_id=kwargs.get("indexer_id"),
                target_indexer_ids=tuple(
                    str(value) for value in (kwargs.get("indexer_ids") or []) if str(value).strip()
                ),
                enable_duplicate_check=kwargs.get("enable_duplicate_check", True),
            )
            job_id = self.start_processing_job_request(
                request,
                reuse_running=kwargs.get("reuse_running", True),
                source=kwargs.get("source"),
                job_name=kwargs.get("job_name"),
                run_after=kwargs.get("run_after"),
            )
            started.append(
                {
                    "job_id": job_id,
                    "category": str(category or "misc"),
                    "paths": paths,
                }
            )
        return started

    def _get_job_target_paths(self, job: dict[str, Any]) -> list[str]:
        raw_paths = job.get("_paths")
        if raw_paths is None:
            raw_paths = job.get("target_paths")
        return self._normalize_paths(raw_paths)

    def _set_job_target_paths(self, job: dict[str, Any], paths: Any) -> None:
        normalized = self._normalize_paths(paths)
        job["_paths"] = normalized
        job["has_explicit_paths"] = bool(normalized)
        if normalized:
            job["target_paths"] = normalized
        else:
            job.pop("target_paths", None)

    @staticmethod
    def _normalize_job_path_identity(path: Any) -> Optional[str]:
        text = str(path or "").strip()
        if not text:
            return None
        normalized = os.path.normpath(text)
        if not os.path.isabs(normalized):
            normalized = os.path.abspath(normalized)
        normalized = normalized.replace('\\', '/')
        return normalized.casefold() if os.name == "nt" else normalized

    @classmethod
    def _normalize_job_path_mapping(cls, paths: Any) -> tuple[list[str], dict[str, str]]:
        normalized_paths = cls._normalize_paths(paths)
        mapping: dict[str, str] = {}
        for path in normalized_paths:
            identity = cls._normalize_job_path_identity(path)
            if identity:
                mapping.setdefault(identity, path)
        return normalized_paths, mapping

    @classmethod
    def _match_job_paths_by_identity(cls, current_paths: Any, requested_paths: Any) -> Optional[list[str]]:
        current, current_map = cls._normalize_job_path_mapping(current_paths)
        requested = cls._normalize_paths(requested_paths)
        if len(requested) != len(current):
            return None

        matched: list[str] = []
        seen_identities: set[str] = set()
        for raw_path in requested:
            identity = cls._normalize_job_path_identity(raw_path)
            if not identity or identity in seen_identities or identity not in current_map:
                return None
            matched.append(current_map[identity])
            seen_identities.add(identity)

        if len(seen_identities) != len(current_map):
            return None
        return matched

    def start_processing_job_request(self, request: ProcessingJobRequest, **job_kwargs: Any) -> str:
        """Start a processing job from an explicit normalized request."""
        request = self._with_inferred_tv_pack_request_paths(request)
        request = self._collapse_overlapping_tv_request_paths(request)
        return self.start_upload_job(
            category=request.category,
            limit=request.limit,
            skip_packs=request.skip_packs,
            skip_episodes=request.skip_episodes,
            test_mode=request.test_mode,
            indexer_id=request.target_indexer_id,
            indexer_ids=list(request.target_indexer_ids) or None,
            paths=list(request.paths) or None,
            item_hints=list(request.item_hints) or None,
            enable_duplicate_check=request.enable_duplicate_check,
            force=request.force,
            **job_kwargs,
        )

    @classmethod
    def _request_path_category(cls, path: Path, hint: dict[str, Any]) -> str:
        """Resolve rewrite eligibility from the path, allowing a manual override only."""
        manual_category = cls._normalize_queue_category(hint.get("manual_category"))
        if manual_category and manual_category not in cls._QUEUE_INVALID_CATEGORY_VALUES:
            return manual_category

        try:
            from logic.pending_scan import resolve_explicit_path

            resolved = resolve_explicit_path(path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Queue path category resolution failed for {path}: {exc}")
            return ""
        return cls._normalize_queue_category(resolved.category)

    @classmethod
    def _with_inferred_tv_pack_request_paths(cls, request: ProcessingJobRequest) -> ProcessingJobRequest:
        """Insert season-folder paths into explicit TV episode-only job requests."""
        paths = list(request.paths)
        if not paths:
            return request

        hints_by_path: dict[str, dict[str, Any]] = {}
        for hint in request.item_hints:
            path_text = str(hint.get("path") or "").strip()
            if path_text:
                hints_by_path[path_text] = dict(hint)

        existing = {os.path.normpath(path) for path in paths if str(path).strip()}
        episodes_by_parent: dict[Path, list[Path]] = {}
        for path_text in paths:
            path = Path(str(path_text))
            hint = hints_by_path.get(str(path_text), {})
            if cls._request_path_category(path, hint) != "tv":
                continue
            if path.is_file():
                episodes_by_parent.setdefault(path.parent, []).append(path)

        pack_parents = {
            parent
            for parent, episode_paths in episodes_by_parent.items()
            if os.path.normpath(str(parent)) not in existing
            and cls._looks_like_queue_tv_pack_folder(parent, episode_paths)
        }
        if not pack_parents:
            return request

        expanded_paths: list[str] = []
        expanded_hints: list[dict[str, Any]] = []
        inserted: set[Path] = set()
        for path_text in paths:
            path = Path(str(path_text))
            parent = path.parent if path.is_file() else None
            if parent in pack_parents and parent not in inserted:
                parent_text = str(parent)
                expanded_paths.append(parent_text)
                expanded_hints.append(
                    {
                        "path": parent_text,
                        "name": parent.name,
                        "category": "tv",
                        "detected_category": "tv",
                        "itype": "TV Show",
                        "is_dir": True,
                        "queue_category_source": "inferred-season-pack",
                    }
                )
                inserted.add(parent)
            expanded_paths.append(str(path_text))
            hint = hints_by_path.get(str(path_text))
            if hint is not None:
                expanded_hints.append(hint)

        if not inserted:
            return request

        logger.info(f"[QUEUE-CREATE] inferred {len(inserted)} TV season pack path(s) before job start")
        return ProcessingJobRequest(
            category=request.category,
            limit=request.limit,
            skip_packs=request.skip_packs,
            skip_episodes=request.skip_episodes,
            test_mode=request.test_mode,
            target_indexer_id=request.target_indexer_id,
            target_indexer_ids=request.target_indexer_ids,
            paths=tuple(expanded_paths),
            item_hints=tuple(expanded_hints),
            enable_duplicate_check=request.enable_duplicate_check,
            force=request.force,
        )

    @staticmethod
    def _clone_request_with_paths(
        request: ProcessingJobRequest,
        paths: list[str],
        hints: list[dict[str, Any]],
    ) -> ProcessingJobRequest:
        return ProcessingJobRequest(
            category=request.category,
            limit=request.limit,
            skip_packs=request.skip_packs,
            skip_episodes=request.skip_episodes,
            test_mode=request.test_mode,
            target_indexer_id=request.target_indexer_id,
            target_indexer_ids=request.target_indexer_ids,
            paths=tuple(paths),
            item_hints=tuple(hints),
            enable_duplicate_check=request.enable_duplicate_check,
            force=request.force,
        )

    @classmethod
    def _dedup_result_or_original(
        cls,
        request: ProcessingJobRequest,
        original_paths: list[str],
        deduped_paths: list[str],
        deduped_hints: list[dict[str, Any]],
    ) -> ProcessingJobRequest:
        if len(deduped_paths) == len(original_paths):
            return request
        logger.info(
            f"[QUEUE-CREATE] collapsed {len(original_paths) - len(deduped_paths)} duplicate path identity(s) before job start"
        )
        return cls._clone_request_with_paths(request, deduped_paths, deduped_hints)

    @classmethod
    def _collapse_overlapping_tv_request_paths(cls, request: ProcessingJobRequest) -> ProcessingJobRequest:
        """Drop ancestor TV directories when explicit child season folders are also selected."""
        paths = list(request.paths)
        if len(paths) < 2:
            return request

        hints_by_path: dict[str, dict[str, Any]] = {}
        for hint in request.item_hints:
            path_text = str(hint.get("path") or "").strip()
            if path_text:
                hints_by_path[path_text] = dict(hint)

        deduped_paths: list[str] = []
        deduped_hints: list[dict[str, Any]] = []
        seen_identities: set[str] = set()
        for path_text in paths:
            identity = cls._normalize_job_path_identity(path_text)
            if not identity or identity in seen_identities:
                continue
            seen_identities.add(identity)
            deduped_paths.append(path_text)
            hint = hints_by_path.get(path_text)
            if hint is not None:
                deduped_hints.append(hint)

        if len(deduped_paths) < 2:
            return cls._dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

        tv_dir_entries: list[tuple[int, Path]] = []
        for idx, path_text in enumerate(deduped_paths):
            try:
                path = Path(path_text)
            except TypeError:
                continue
            if not path.is_dir():
                continue

            hint = hints_by_path.get(path_text, {})
            if cls._request_path_category(path, hint) != "tv":
                continue
            tv_dir_entries.append((idx, path))

        if len(tv_dir_entries) < 2:
            return cls._dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

        try:
            resolved_entries = [(idx, path.resolve()) for idx, path in tv_dir_entries]
        except OSError:
            resolved_entries = [(idx, path) for idx, path in tv_dir_entries]

        drop_indices: set[int] = set()
        for idx, path in resolved_entries:
            for other_idx, other_path in resolved_entries:
                if other_idx == idx:
                    continue
                try:
                    is_descendant = other_path.is_relative_to(path)
                except AttributeError:
                    is_descendant = str(other_path).startswith(str(path).rstrip("\\/") + os.sep)
                if is_descendant and other_path != path:
                    drop_indices.add(idx)
                    break

        if not drop_indices:
            return cls._dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

        new_paths: list[str] = []
        new_hints: list[dict[str, Any]] = []
        for idx, path_text in enumerate(deduped_paths):
            if idx in drop_indices:
                continue
            new_paths.append(path_text)
            hint = hints_by_path.get(path_text)
            if hint is not None:
                new_hints.append(hint)

        logger.info(
            f"[QUEUE-CREATE] collapsed {len(paths) - len(new_paths)} overlapping ancestor/duplicate TV path(s) before starting job"
        )
        return cls._clone_request_with_paths(request, new_paths, new_hints)

    def start_processing_job_requests(self, requests: list[ProcessingJobRequest], **job_kwargs: Any) -> list[str]:
        """Start multiple normalized processing job requests."""
        return [self.start_processing_job_request(request, **job_kwargs) for request in requests]

    def _preserve_stopped_processing_job_locked(self, job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "stopped":
            return False
        if str(job.get("job_type") or "processing") != "processing":
            return False

        has_explicit_paths = bool(job.get("has_explicit_paths"))
        resumable_paths = self._get_job_target_paths(job)
        current_path = str(job.pop("_current_item_path", "") or "").strip()
        if current_path:
            current_identity = self._normalize_job_path_identity(current_path)
            existing = {self._normalize_job_path_identity(path) for path in resumable_paths}
            if current_identity and current_identity not in existing:
                resumable_paths = [current_path, *resumable_paths]

        if has_explicit_paths:
            if not resumable_paths:
                return False
            job["target_paths"] = list(resumable_paths)
        else:
            job.pop("target_paths", None)

        progress_text = str(job.get("progress") or "").strip().lower()
        if not progress_text or progress_text == "stopping...":
            job["progress"] = "Stopped by user"

        return True

    def _is_resumable_stopped_job_locked(self, job: dict[str, Any]) -> bool:
        if str(job.get("status") or "") != "stopped":
            return False
        return self._preserve_stopped_processing_job_locked(job)

    def _restore_stopped_job_to_queue_locked(self, job: dict[str, Any], *, progress: Optional[str] = None) -> bool:
        if not self._preserve_stopped_processing_job_locked(job):
            return False

        job["status"] = "queued"
        job["stop_requested"] = False
        job["pause_requested"] = False
        job["current_stage"] = "QUEUED"
        job["speed"] = None
        job["eta"] = None
        job["item_percent"] = 0
        if progress:
            job["progress"] = progress
        elif not str(job.get("progress") or "").strip():
            job["progress"] = "Queued - waiting for queue resume."
        self._record_job_event(job, "requeued", str(job["progress"]))
        return True

    @staticmethod
    def _normalize_job_name(name: Any) -> Optional[str]:
        if name is None:
            return None
        cleaned = " ".join(str(name).split()).strip()
        if not cleaned:
            return None
        return cleaned[:80]

    @staticmethod
    def _job_category_label(category: str) -> str:
        key = str(category or "").strip().lower()
        labels = {
            "tv": "TV",
            "movies": "Movies",
            "anime": "Anime",
            "misc": "Misc",
            "both": "Both",
            "mixed": "Mixed",
            "selected": "Selected",
            "all": "All",
        }
        if key in labels:
            return labels[key]
        if not key:
            return "Job"
        return key.replace("_", " ").title()

    @classmethod
    def _default_job_name(cls, category: str, item_count: int, paths: Any = None) -> str:
        normalized_paths = cls._normalize_paths(paths)
        if normalized_paths:
            path_objects = [Path(path) for path in normalized_paths]
            if len(path_objects) == 1:
                candidate = path_objects[0] if path_objects[0].suffix == "" else path_objects[0].parent
            else:
                try:
                    candidate = Path(os.path.commonpath([str(path) for path in path_objects]))
                except ValueError:
                    candidate = path_objects[0].parent
                if candidate in path_objects and candidate.suffix:
                    candidate = candidate.parent
            if candidate.name:
                return cls._normalize_job_name(candidate.name) or cls._job_category_label(category)
        return "Selected Upload" if str(category or "").strip().lower() in {"mixed", "both", "selected"} else cls._job_category_label(category)

    @staticmethod
    def _parse_iso_datetime_utc(raw: Any) -> Optional[datetime]:
        if raw is None:
            return None
        text = str(raw).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            dt = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @classmethod
    def _normalize_run_after(cls, raw: Any) -> Optional[str]:
        dt = cls._parse_iso_datetime_utc(raw)
        return dt.isoformat() if dt else None

    def _dedupe_recovery_paths(self, job: dict[str, Any]) -> list[str]:
        """Merge the active item, in-flight items, and remaining targets into one deduped path order."""
        recovery_paths = [
            str(job.get("_current_item_path") or "").strip(),
            *self._normalize_paths(job.get("_inflight_item_paths")),
            *self._get_job_target_paths(job),
        ]
        deduped: list[str] = []
        seen_path_identities: set[str] = set()
        for recovery_path in recovery_paths:
            identity = self._normalize_job_path_identity(recovery_path)
            if not identity or identity in seen_path_identities:
                continue
            seen_path_identities.add(identity)
            deduped.append(recovery_path)
        return deduped

    def _serialize_job_for_persistence(self, job: dict[str, Any]) -> Optional[dict[str, Any]]:
        status = str(job.get("status", "queued"))
        if status == "stopped":
            self._preserve_stopped_processing_job_locked(job)
        elif status not in {"queued", "running", "paused", "stopping", *_TERMINAL_JOB_STATUSES}:
            return None

        kwargs = job.get("_kwargs")
        if not isinstance(kwargs, dict):
            kwargs = {}

        job_id = str(job.get("job_id", "")).strip()
        if not job_id:
            return None

        paths = self._dedupe_recovery_paths(job)

        payload = self._build_job_persistence_payload(job, job_id, status, kwargs, paths)
        if status in _TERMINAL_JOB_STATUSES or (
            status == "stopped" and not self._is_resumable_stopped_job_locked(job)
        ):
            payload["kwargs"] = {}
            payload["paths"] = []
        return payload

    def _build_job_persistence_payload(
        self,
        job: dict[str, Any],
        job_id: str,
        status: str,
        kwargs: dict[str, Any],
        paths: list[str],
    ) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "category": str(job.get("category", "misc")),
            "source": self._normalize_job_source(job.get("source")),
            "status": status,
            "started_at": str(job.get("started_at") or datetime.now(timezone.utc).isoformat()),
            "created_at": str(job.get("created_at") or job.get("started_at") or ""),
            "finished_at": str(job.get("finished_at") or ""),
            "progress": str(job.get("progress") or ""),
            "progress_percent": int(job.get("progress_percent") or 0),
            "test_mode": bool(job.get("test_mode", False)),
            "display_name": self._normalize_job_name(job.get("display_name")),
            "run_after": self._normalize_run_after(job.get("run_after")),
            "priority": int(job.get("priority") or 0),
            "job_type": str(job.get("job_type") or "processing"),
            "items_processed": int(job.get("items_processed") or 0),
            "items_total": int(job.get("items_total") or 0),
            "items_skipped": int(job.get("items_skipped") or 0),
            "total_bytes": int(job.get("total_bytes") or 0),
            "summary": dict(job.get("summary") or {}),
            "attempt_count": int(job.get("attempt_count") or 0),
            "retry_of": str(job.get("retry_of") or "") or None,
            "retried_as": str(job.get("retried_as") or "") or None,
            "retry_eligible": bool(job.get("retry_eligible", False)),
            "last_error": str(job.get("last_error") or "") or None,
            "events": list(job.get("events") or [])[-30:],
            "retry_request": (
                dict(job.get("_retry_request") or {})
                if status == "failed" and bool(job.get("retry_eligible"))
                else {}
            ),
            "kwargs": kwargs,
            "paths": paths,
        }

    def _prune_finished_jobs_locked(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=_FINISHED_JOB_RETENTION_DAYS)
        finished: list[tuple[datetime, str]] = []
        for job_id, job in self._jobs.items():
            status = str(job.get("status") or "")
            if status not in _TERMINAL_JOB_STATUSES and not (
                status == "stopped" and not self._is_resumable_stopped_job_locked(job)
            ):
                continue
            timestamp = self._parse_iso_datetime_utc(job.get("finished_at") or job.get("started_at"))
            finished.append((timestamp or datetime.min.replace(tzinfo=timezone.utc), job_id))

        finished.sort(reverse=True)
        retained = {job_id for _timestamp, job_id in finished[:_FINISHED_JOB_RETENTION_COUNT]}
        for timestamp, job_id in finished:
            if job_id not in retained or timestamp < cutoff:
                self._jobs.pop(job_id, None)

    def _persist_jobs_locked(self) -> None:
        self._prune_finished_jobs_locked()
        payload = {
            "queue_processing_paused": bool(self._queue_processing_paused),
            "jobs": [
                snap
                for snap in (self._serialize_job_for_persistence(job) for job in self._jobs.values())
                if snap is not None
            ],
        }

        try:
            self._write_jobs_state_file_atomic(self._jobs_state_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Failed to persist queued jobs: {exc}")
            return

        try:
            self._write_jobs_state_file_atomic(self._jobs_state_backup_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(f"Failed to update queued jobs backup state: {exc}")

    def _persist_runtime_checkpoint(self) -> None:
        """Persist an active job at a safe item boundary for crash recovery."""
        with self._lock:
            self._persist_jobs_locked()

    def _write_jobs_state_file_atomic(self, path: Path, payload: dict[str, Any]) -> None:
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))

    def _load_jobs_state_payload_locked(self) -> Optional[dict[str, Any]]:
        candidates = [
            ("primary", self._jobs_state_path),
            ("backup", self._jobs_state_backup_path),
        ]

        for label, path in candidates:
            if not path.exists():
                continue
            try:
                raw = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning(f"Could not read {label} queued jobs state from {path}: {exc}")
                continue

            if not raw:
                logger.warning(f"Ignoring empty {label} queued jobs state file at {path}")
                continue

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                logger.warning(f"Ignoring corrupt {label} queued jobs state file at {path}: {exc}")
                continue

            if not isinstance(data, dict):
                logger.warning(f"Ignoring malformed {label} queued jobs state file at {path}")
                continue

            if label == "backup":
                try:
                    self._write_jobs_state_file_atomic(self._jobs_state_path, data)
                except (OSError, TypeError, ValueError) as exc:
                    logger.warning(f"Failed to restore primary queued jobs state from backup: {exc}")
            return data

        return None

    def _restore_jobs_from_disk_locked(self) -> None:
        data = self._load_jobs_state_payload_locked()
        if data is None:
            return

        if isinstance(data, dict):
            self._queue_processing_paused = bool(data.get("queue_processing_paused", False))
            rows = data.get("jobs", [])
        else:
            rows = []
        if not isinstance(rows, list):
            return

        recovered = 0
        recovered_finished = 0
        restored_manual_stop = False
        for row in rows:
            if not isinstance(row, dict):
                continue

            job_id = str(row.get("job_id") or "").strip() or str(uuid.uuid4())[:8]
            category = str(row.get("category") or "misc")
            started_at = str(row.get("started_at") or datetime.now(timezone.utc).isoformat())
            created_at = str(row.get("created_at") or started_at)
            persisted_status = str(row.get("status") or "queued")

            kwargs = row.get("kwargs")
            if not isinstance(kwargs, dict):
                kwargs = {}

            display_name = self._normalize_job_name(row.get("display_name"))
            run_after = self._normalize_run_after(row.get("run_after"))
            priority = int(row.get("priority") or 0)

            persisted_paths = self._normalize_paths(row.get("paths"))
            persisted_finished = persisted_status in _TERMINAL_JOB_STATUSES or (
                persisted_status == "stopped" and not persisted_paths
            )
            if persisted_finished:
                terminal_job: dict[str, Any] = JobState(
                    job_id=job_id,
                    category=category,
                    status=persisted_status,
                    started_at=started_at,
                    created_at=created_at,
                    progress=str(row.get("progress") or ""),
                    progress_percent=int(row.get("progress_percent") or 0),
                    test_mode=bool(row.get("test_mode", False)),
                    display_name=display_name,
                    priority=priority,
                    attempt_count=int(row.get("attempt_count") or 0),
                    retry_of=str(row.get("retry_of") or "") or None,
                    retried_as=str(row.get("retried_as") or "") or None,
                    retry_eligible=bool(row.get("retry_eligible", False)),
                    last_error=str(row.get("last_error") or "") or None,
                    events=list(row.get("events") or [])[-30:],
                ).model_dump()
                terminal_job.update(
                    {
                        "source": self._normalize_job_source(row.get("source")),
                        "job_type": str(row.get("job_type") or "processing"),
                        "finished_at": str(row.get("finished_at") or started_at),
                        "items_processed": int(row.get("items_processed") or 0),
                        "items_total": int(row.get("items_total") or 0),
                        "items_skipped": int(row.get("items_skipped") or 0),
                        "total_bytes": int(row.get("total_bytes") or 0),
                        "summary": dict(row.get("summary") or {}),
                    }
                )
                retry_request = row.get("retry_request")
                if isinstance(retry_request, dict) and retry_request:
                    terminal_job["_retry_request"] = retry_request
                self._jobs[job_id] = terminal_job
                recovered_finished += 1
                continue

            progress = str(row.get("progress") or "")
            if persisted_status == "stopped":
                progress = "Recovered after restart - waiting for queue resume."
                restored_manual_stop = True
            elif persisted_status in {"running", "stopping", "paused"}:
                progress = "Recovered after restart - re-queued."
            elif not progress:
                progress = "Recovered after restart - queued."

            job: dict[str, Any] = JobState(
                job_id=job_id,
                category=category,
                status="queued",
                started_at=started_at,
                created_at=created_at,
                progress=progress,
                speed=None,
                eta=None,
                current_stage="QUEUED",
                test_mode=bool(row.get("test_mode", False)),
                display_name=display_name,
                run_after=run_after,
                priority=priority,
                attempt_count=int(row.get("attempt_count") or 0),
                retry_of=str(row.get("retry_of") or "") or None,
                retried_as=str(row.get("retried_as") or "") or None,
                events=list(row.get("events") or [])[-30:],
            ).model_dump()
            if not display_name:
                job.pop("display_name", None)
            if not run_after:
                job.pop("run_after", None)
            job["source"] = self._normalize_job_source(row.get("source"))
            job["_kwargs"] = kwargs
            retry_request = row.get("retry_request")
            if isinstance(retry_request, dict) and retry_request:
                job["_retry_request"] = retry_request
            self._set_job_target_paths(job, row.get("paths"))

            prior_processed = int(row.get("items_processed") or 0)
            prior_total = int(row.get("items_total") or 0)
            if prior_processed > 0 or prior_total > 0:
                job["_resume_items_processed"] = prior_processed
                job["_resume_items_total"] = prior_total
                job["_resume_items_skipped"] = int(row.get("items_skipped") or 0)

            self._jobs[job_id] = job
            recovered += 1

        if restored_manual_stop:
            self._queue_processing_paused = True

        if recovered:
            log_info(f"Recovered {recovered} queued job(s) after restart.")
        if recovered_finished:
            log_info(f"Recovered {recovered_finished} recently finished job(s) after restart.")
        self._persist_jobs_locked()

    def _finalize_job_locked(
        self,
        job: dict[str, Any],
        *,
        duration_sec: float,
        clear_active_fields: bool,
        remove_from_active: bool,
    ) -> None:
        from logic.stats_engine import format_seconds

        job_id = str(job.get("job_id", ""))
        duration_str = format_seconds(duration_sec)
        status = str(job.get("status") or "")
        clear_after_stop = bool(job.pop("_clear_after_stop", False))

        if status not in {"stopped", "failed"} and (bool(job.get("stop_requested")) or status == "stopping"):
            job["status"] = "stopped"
            job["progress"] = "Stopped by user"
            status = "stopped"
            logger.info(f"Job {job_id} stopped by user.")

        if status == "stopped" and clear_after_stop:
            job["status"] = "cancelled"
            job["progress"] = "Cancelled (queue cleared)"
            status = "cancelled"

        if status not in {"stopped", "failed", "cancelled"}:
            job["status"] = "completed"
            job["progress"] = f"Finished in {duration_str}"
            job["progress_percent"] = 100
            log_success(f"Job {job_id} completed.")
        elif status == "stopped" and self._is_resumable_stopped_job_locked(job):
            self._queue_processing_paused = True

        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        if job.get("status") == "failed":
            job["last_error"] = str(job.get("progress") or "Job failed")[:500]
            job["retry_eligible"] = bool(
                str(job.get("job_type") or "processing") == "processing"
                and isinstance(job.get("_retry_request"), dict)
            )
        else:
            job["last_error"] = None
            job["retry_eligible"] = False
        self._record_job_event(
            job,
            str(job.get("status") or "finished"),
            str(job.get("progress") or "Job finished"),
        )

        snapshot = self._normalize_paths(job.get("_snapshot_paths"))
        if snapshot:
            job["_completed_paths"] = snapshot

        database.save_job_history(
            job_id,
            category=job.get("category"),
            status=job.get("status"),
            items_processed=job.get("items_processed", 0),
            items_total=job.get("items_total", 0),
            items_skipped=job.get("items_skipped", 0),
            total_bytes=job.get("total_bytes", 0) or 0,
            duration_seconds=duration_sec,
            test_mode=bool(job.get("test_mode", False)),
            started_at=job.get("started_at"),
            completed_at=job["finished_at"],
            error_message=str(job.get("progress")) if job.get("status") == "failed" else None,
        )

        job["summary"] = {
            "duration": duration_str,
            "processed": f"{job.get('items_processed', 0)}/{job.get('items_total', 0)}",
            "skipped": job.get("items_skipped", 0),
            "total_bytes": job.get("total_bytes", 0),
        }

        if clear_active_fields:
            for key in ["current_item", "item_percent", "speed", "eta", "current_stage"]:
                job[key] = None

        self._cleanup_job_artifacts_locked(job)
        self._preserve_stopped_processing_job_locked(job)

        if job.get("source_monitor_id"):
            usenet_stream.record_stream_monitor_job(str(job.get("source_monitor_id")), job)

        if remove_from_active or clear_after_stop:
            self._jobs.pop(job_id, None)

    def _cleanup_job_artifacts_locked(self, job: dict[str, Any]) -> None:
        if job.get("_artifacts_cleaned"):
            return

        cleanup_paths = self._normalize_paths(job.get("cleanup_paths"))
        kwargs = job.get("_kwargs")
        if isinstance(kwargs, dict):
            cleanup_paths.extend(self._normalize_paths(kwargs.get("cleanup_paths")))

        seen: set[str] = set()
        for raw in cleanup_paths:
            path_str = str(raw).strip()
            if not path_str or path_str in seen:
                continue
            seen.add(path_str)
            try:
                path = Path(path_str)
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

        job["_artifacts_cleaned"] = True

    def register_process(self, job_id: str, process: Any) -> None:
        with self._lock:
            if job_id not in self._processes:
                self._processes[job_id] = []
            self._processes[job_id].append(process)

    @staticmethod
    def _collect_process_tree_pids(process: Any) -> list[int]:
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

    def _suspend_job_processes_locked(self, job_id: str) -> int:
        processes = self._processes.get(job_id) or []
        suspended: set[int] = self._suspended_pids.get(job_id, set())
        count = 0

        for proc in list(processes):
            for pid in self._collect_process_tree_pids(proc):
                if pid in suspended:
                    continue
                try:
                    psutil.Process(pid).suspend()
                    suspended.add(pid)
                    count += 1
                except (psutil.Error, OSError):
                    continue

        if suspended:
            self._suspended_pids[job_id] = suspended
        return count

    def _resume_job_processes_locked(self, job_id: str) -> int:
        resumed = 0
        for pid in list(self._suspended_pids.get(job_id, set())):
            try:
                psutil.Process(pid).resume()
                resumed += 1
            except (psutil.Error, OSError):
                pass
        self._suspended_pids.pop(job_id, None)
        return resumed

    def _terminate_job_processes_locked(self, job_id: str, *, kill_delay_s: float = 0.5) -> int:
        """Terminate registered tool processes for a job, then schedule a quick kill fallback."""
        processes = list(self._processes.get(job_id) or [])
        if not processes:
            return 0

        self._resume_job_processes_locked(job_id)

        pids: list[int] = []
        seen: set[int] = set()
        for process in processes:
            for pid in self._collect_process_tree_pids(process):
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
            from logic.process_reaper import get_scheduler

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

    def unregister_process(self, job_id: str, process: Optional[Any] = None) -> None:
        with self._lock:
            if job_id in self._processes:
                if process:
                    try:
                        self._processes[job_id].remove(process)
                        if not self._processes[job_id]:
                            del self._processes[job_id]
                            self._suspended_pids.pop(job_id, None)
                    except ValueError:
                        pass
                else:
                    del self._processes[job_id]
                    self._suspended_pids.pop(job_id, None)

    def pause_job(self, job_id: str) -> bool:
        lane_released = False
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            status = str(job.get("status"))
            if status == "paused" or (status == "running" and job.get("pause_requested")):
                return True
            if status != "running":
                return False

            job["pause_requested"] = True
            suspended = self._suspend_job_processes_locked(job_id)
            if suspended:
                job["status"] = "paused"
                job["progress"] = "Paused by user"
                job["speed"] = "Paused"
                job["current_stage"] = "PAUSED"
                lane_released = True
            else:
                job["progress"] = "Pause requested - waiting for a safe checkpoint..."
                job["_pause_ack_callback"] = lambda: self._acknowledge_python_pause(job_id, job)
            self._record_job_event(
                job,
                "paused" if suspended else "pause-requested",
                str(job.get("progress") or "Pause requested"),
            )
            logger.debug(f"Pause requested for job {job_id}; suspended {suspended} process(es)")
            self._persist_jobs_locked()
        if lane_released:
            self._try_start_queued()
        return True

    def _acknowledge_python_pause(self, job_id: str, expected_job: dict[str, Any]) -> None:
        """Release the scheduler lane after a Python worker reaches a pause checkpoint."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not expected_job or not job.get("pause_requested") or job.get("status") != "paused":
                return
            job.pop("_pause_ack_callback", None)
            self._persist_jobs_locked()
        self._try_start_queued()

    def get_queue_control_state(self) -> dict[str, Any]:
        with self._lock:
            active = next(
                (
                    {
                        "job_id": j.get("job_id"),
                        "status": j.get("status"),
                        "category": j.get("category"),
                        "display_name": j.get("display_name"),
                    }
                    for j in self._jobs.values()
                    if j.get("status") in ("running", "paused", "stopping")
                ),
                None,
            )
            return {
                "paused": bool(self._queue_processing_paused),
                "active": active,
            }

    def get_work_activity_state(self) -> dict[str, Any]:
        with self._lock:
            active_statuses = ("running", "paused", "stopping")
            active_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "category": str(job.get("category") or "misc"),
                    "source": self._normalize_job_source(job.get("source")),
                    "status": str(job.get("status") or "unknown"),
                    "display_name": job.get("display_name"),
                    "progress": str(job.get("progress") or ""),
                }
                for job in self._jobs.values()
                if str(job.get("status") or "") in active_statuses
            ]
            queued_jobs = [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "category": str(job.get("category") or "misc"),
                    "source": self._normalize_job_source(job.get("source")),
                    "status": str(job.get("status") or "unknown"),
                    "display_name": job.get("display_name"),
                }
                for job in self._jobs.values()
                if str(job.get("status") or "") == "queued" or self._is_resumable_stopped_job_locked(job)
            ]

            return {
                "queue_paused": bool(self._queue_processing_paused),
                "active_jobs": active_jobs,
                "queued_jobs": queued_jobs,
                "active_count": len(active_jobs),
                "queued_count": len(queued_jobs),
                "staged_count": len(self._queue_items),
            }

    def pause_queue(self, pause_active: bool = True) -> bool:
        to_pause: list[str] = []
        with self._lock:
            self._queue_processing_paused = True
            if pause_active:
                to_pause = [jid for jid, job in self._jobs.items() if job.get("status") == "running"]
            self._persist_jobs_locked()

        for job_id in to_pause:
            self.pause_job(job_id)
        return True

    def resume_queue(self) -> bool:
        with self._lock:
            self._queue_processing_paused = False
            for job in self._jobs.values():
                if job.get("status") == "running" and job.get("pause_requested"):
                    job["pause_requested"] = False
                    job.pop("_pause_ack_callback", None)
                    job["progress"] = "Pause cancelled"
                elif job.get("status") == "paused":
                    job["resume_requested"] = True
                    job["progress"] = "Resume queued - waiting for scheduler lane..."
            for job in self._jobs.values():
                if self._is_resumable_stopped_job_locked(job):
                    self._restore_stopped_job_to_queue_locked(
                        job,
                        progress="Queued - waiting for current job to finish...",
                    )
            self._persist_jobs_locked()

        self._try_start_queued()
        return True

    def stop_queue(self, *, clear_after_stop: bool = False) -> bool:
        to_stop: list[str] = []
        with self._lock:
            self._queue_processing_paused = not clear_after_stop
            to_stop = [jid for jid, job in self._jobs.items() if job.get("status") in ("running", "paused", "stopping")]
            self._persist_jobs_locked()

        for job_id in to_stop:
            self.stop_job(job_id, clear_after_stop=clear_after_stop)
        return True

    def stop_queue_and_clear(self) -> dict[str, Any]:
        self.stop_queue(clear_after_stop=True)
        cleared_jobs = self.clear_queued_jobs()
        with self._lock:
            self._queue_processing_paused = False
            self._persist_jobs_locked()
        return {
            "cleared_jobs": int(cleared_jobs),
            "control": self.get_queue_control_state(),
        }

    def stop_all_jobs_and_wait(
        self,
        *,
        clear_staged_items: bool = True,
        wait_timeout_s: float = 15.0,
        poll_interval_s: float = 0.25,
    ) -> dict[str, Any]:
        self.stop_queue()
        cleared_jobs = self.clear_queued_jobs()
        cleared_items = self.clear_queue_items() if clear_staged_items else 0

        timed_out = False
        deadline = time.monotonic() + max(0.0, float(wait_timeout_s))
        final_state = self.get_work_activity_state()

        while final_state["active_count"] > 0:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(max(0.05, float(poll_interval_s)))
            final_state = self.get_work_activity_state()

        return {
            "queue_paused": bool(final_state["queue_paused"]),
            "cleared_jobs": int(cleared_jobs),
            "cleared_staged_items": int(cleared_items),
            "timed_out": bool(timed_out),
            "active_jobs_remaining": final_state["active_jobs"],
            "queued_jobs_remaining": final_state["queued_jobs"],
            "active_count": int(final_state["active_count"]),
            "queued_count": int(final_state["queued_count"]),
            "staged_count": int(final_state["staged_count"]),
        }

    def rename_job(self, job_id: str, name: Optional[str]) -> tuple[bool, Optional[str]]:
        normalized = self._normalize_job_name(name)
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None
            if normalized:
                job["display_name"] = normalized
            else:
                job.pop("display_name", None)
            self._record_job_event(job, "renamed", f"Renamed to {normalized or 'default name'}")
            self._persist_jobs_locked()
            return True, job.get("display_name")

    def set_queued_job_schedule(self, job_id: str, run_after: Optional[str]) -> tuple[bool, Optional[str], str]:
        normalized = self._normalize_run_after(run_after)
        if run_after not in (None, "") and normalized is None:
            return False, None, "invalid-datetime"

        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None, "not-found"
            status = str(job.get("status"))
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return False, None, "not-queued"
            elif status != "queued":
                return False, None, "not-queued"

            if normalized:
                job["run_after"] = normalized
                due_at = self._parse_iso_datetime_utc(normalized)
                if due_at and due_at > datetime.now(timezone.utc):
                    job["progress"] = f"Scheduled for {due_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
                else:
                    job["progress"] = "Queued - waiting for current job to finish..."
            else:
                job.pop("run_after", None)
                job["progress"] = "Queued - waiting for current job to finish..."

            current = job.get("run_after")
            self._record_job_event(
                job,
                "scheduled" if current else "schedule-cleared",
                str(job.get("progress") or "Schedule updated"),
            )
            self._persist_jobs_locked()

        self._try_start_queued()
        return True, current, "ok"

    def resume_job(self, job_id: str) -> bool:
        should_try_start = False
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            status = str(job.get("status"))
            if status == "running":
                if job.get("pause_requested"):
                    job["pause_requested"] = False
                    job.pop("_pause_ack_callback", None)
                    job["progress"] = "Pause cancelled"
                    self._record_job_event(job, "pause-cancelled", str(job["progress"]))
                    self._persist_jobs_locked()
                return True
            if status == "stopped":
                if not self._restore_stopped_job_to_queue_locked(
                    job,
                    progress="Queued - waiting for current job to finish...",
                ):
                    return False

                self._queue_processing_paused = False
                queued = [queued_job for queued_job in self._jobs.values() if queued_job.get("status") == "queued"]
                if queued:
                    earliest = min(str(queued_job.get("started_at") or "") for queued_job in queued)
                    try:
                        dt = datetime.fromisoformat(earliest)
                        job["started_at"] = (dt - timedelta(seconds=1)).isoformat()
                    except (TypeError, ValueError):
                        job["started_at"] = datetime.now(timezone.utc).isoformat()
                else:
                    job["started_at"] = datetime.now(timezone.utc).isoformat()

                self._persist_jobs_locked()
                should_try_start = True
            elif status == "paused":
                lane_busy = any(
                    other.get("status") in {"running", "stopping"}
                    for other_id, other in self._jobs.items()
                    if other_id != job_id
                )
                if lane_busy or self._queue_processing_paused:
                    job["resume_requested"] = True
                    job["progress"] = "Resume queued - waiting for scheduler lane..."
                    self._record_job_event(job, "resume-requested", str(job["progress"]))
                else:
                    self._resume_paused_job_locked(job_id, job)
                self._persist_jobs_locked()
            else:
                return False

        if should_try_start:
            self._try_start_queued()
        return True

    def _resume_paused_job_locked(self, job_id: str, job: dict[str, Any]) -> None:
        job["resume_requested"] = False
        job["pause_requested"] = False
        job.pop("_pause_ack_callback", None)
        job["status"] = "running"
        job["progress"] = "Resumed"
        self._record_job_event(job, "resumed", "Job resumed")
        if str(job.get("speed") or "").strip().lower() == "paused":
            job["speed"] = "Starting..."
        if str(job.get("current_stage") or "") == "PAUSED":
            job["current_stage"] = "UPLOADING"

        resumed = self._resume_job_processes_locked(job_id)
        logger.debug(f"Resumed job {job_id}; resumed {resumed} process(es)")

    def stop_job(self, job_id: str, *, clear_after_stop: bool = False) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False

            if job.get("status") == "queued":
                log_info(f"Cancelled queued job {job_id} ({job['category']})")
                job["status"] = "cancelled"
                job["progress"] = "Cancelled (was queued)"
                self._record_job_event(job, "cancelled", str(job["progress"]))
                self._cleanup_job_artifacts_locked(job)
                if job.get("source_monitor_id"):
                    usenet_stream.record_stream_monitor_job(str(job.get("source_monitor_id")), job)
                job.pop("_kwargs", None)
                job.pop("_paths", None)
                if clear_after_stop:
                    self._jobs.pop(job_id, None)
                self._persist_jobs_locked()
                return True

            if job.get("status") in {"running", "paused", "stopping"}:
                prev_status = str(job.get("status"))
                if prev_status == "stopping":
                    if clear_after_stop:
                        job["_clear_after_stop"] = True
                        job["_hide_while_stopping"] = True
                        job["progress"] = "Stopping... clearing when safe"
                        self._queue_processing_paused = False
                        self._persist_jobs_locked()
                    return True
                log_info(f"STOP REQUESTED for job {job_id} ({job['category']})", "WARN")
                job["stop_requested"] = True
                job["pause_requested"] = False
                job["status"] = "stopping"
                job["progress"] = "Stopping... clearing when safe" if clear_after_stop else "Stopping..."
                if clear_after_stop:
                    job["_clear_after_stop"] = True
                    job["_hide_while_stopping"] = True
                self._record_job_event(job, "stop-requested", str(job["progress"]))
                self._persist_jobs_locked()

                terminated = self._terminate_job_processes_locked(job_id, kill_delay_s=0.5 if clear_after_stop else 1.0)
                logger.debug(f"Termination signal sent to {terminated} process(es) for job {job_id}")
                return True
        return False

    def stop_and_clear_job(self, job_id: str) -> bool:
        return self.stop_job(job_id, clear_after_stop=True)

    def _find_reusable_running_job(self, category: str) -> Optional[str]:
        """Return the job_id of an existing running/paused job in this category, if any.

        Extracted from start_upload_job to keep its own branching down.
        """
        with self._lock:
            for job in self._jobs.values():
                if job["category"] == category and job["status"] in ("running", "paused"):
                    return str(job["job_id"])
        return None

    def _determine_job_wait_state(self, run_after_dt: Optional[datetime]) -> tuple[bool, bool, str, str]:
        """Return (has_running, deferred, wait_msg, schedule_label) for a job about to be created.

        schedule_label is "" unless the job is deferred. Extracted from
        start_upload_job to keep its own branching down.
        """
        with self._lock:
            deferred = bool(run_after_dt and run_after_dt > datetime.now(timezone.utc))
            has_running = (
                bool(self._queue_processing_paused)
                or any(job["status"] in ("running", "stopping") for job in self._jobs.values())
                or deferred
            )

        schedule_label = ""
        if deferred and run_after_dt:
            schedule_label = run_after_dt.astimezone().strftime("%Y-%m-%d %H:%M")
            wait_msg = f"Scheduled for {schedule_label}"
        elif has_running:
            wait_msg = "Queued - waiting for current job to finish..."
        else:
            wait_msg = "Starting..."
        return has_running, deferred, wait_msg, schedule_label

    def _build_new_job_state(
        self,
        category: str,
        has_running: bool,
        wait_msg: str,
        display_name: str,
        run_after: str,
        attempt_count_base: int,
        retry_of: Optional[str],
        job_type: str,
        source: str,
        paths: list,
        kwargs: dict,
    ) -> tuple[str, dict]:
        """Build, register, and persist the in-memory job dict for a new upload job.

        Returns (job_id, job). Extracted from start_upload_job to keep its own
        branching down.
        """
        job_id = str(uuid.uuid4())[:8]
        created_at = datetime.now(timezone.utc).isoformat()
        job: dict[str, Any] = JobState(
            job_id=job_id,
            category=category,
            status="queued" if has_running else "running",
            started_at=created_at,
            created_at=created_at,
            progress=wait_msg,
            speed=None if has_running else "Starting...",
            eta=None if has_running else "Starting...",
            current_stage="QUEUED" if has_running else "INITIALIZING",
            test_mode=bool(kwargs.get("test_mode", False)),
            display_name=display_name,
            run_after=run_after,
            priority=int(kwargs.pop("priority", 0) or 0),
            attempt_count=attempt_count_base,
            retry_of=retry_of,
        ).model_dump()
        if not display_name:
            job.pop("display_name", None)
        if not run_after:
            job.pop("run_after", None)
        job["job_type"] = job_type
        job["source"] = source
        if kwargs.get("source_monitor_id"):
            job["source_monitor_id"] = str(kwargs.get("source_monitor_id"))
        job["_kwargs"] = kwargs
        self._set_job_target_paths(job, paths)
        if job_type == "processing":
            job["_retry_request"] = self._build_retry_request(kwargs, paths)
        self._record_job_event(
            job,
            "created",
            f"Created from {source}" + (f" as retry of {retry_of}" if retry_of else ""),
        )
        with self._lock:
            self._jobs[job_id] = job
            self._persist_jobs_locked()
        return job_id, job

    def start_upload_job(self, category: str, **kwargs: Any) -> str:
        reuse_running = bool(kwargs.pop("reuse_running", True))
        raw_paths = kwargs.pop("paths", None)
        targeted_request = raw_paths is not None or kwargs.get("item_hints") is not None
        paths = self._normalize_paths(raw_paths)
        display_name = self._normalize_job_name(kwargs.pop("job_name", None))
        run_after = self._normalize_run_after(kwargs.pop("run_after", None))
        retry_of = str(kwargs.pop("retry_of", "") or "") or None
        attempt_count_base = max(0, int(kwargs.pop("attempt_count_base", 0) or 0))
        job_type = str(kwargs.get("job_type") or "processing")
        source = self._normalize_job_source(kwargs.pop("source", None))
        run_after_dt = self._parse_iso_datetime_utc(run_after)
        if job_type == "processing" and targeted_request and not paths:
            raise ValueError("No valid items remained after queue filtering")
        if not display_name and paths:
            display_name = self._default_job_name(category, len(paths), paths)

        if reuse_running:
            existing_job_id = self._find_reusable_running_job(category)
            if existing_job_id:
                return existing_job_id

        has_running, deferred, wait_msg, schedule_label = self._determine_job_wait_state(run_after_dt)

        job_id, job = self._build_new_job_state(
            category, has_running, wait_msg, display_name, run_after,
            attempt_count_base, retry_of, job_type, source, paths, kwargs,
        )

        if has_running:
            if deferred and run_after_dt:
                log_info(f"Job {job_id} ({category}, source={source}) scheduled for {schedule_label}.")
            else:
                log_info(f"Job {job_id} ({category}, source={source}) queued - waiting for active job to finish.")
            return job_id

        self._launch_job(job)
        return job_id

    def _launch_job(self, job: dict[str, Any]) -> None:
        start_time_float = time.time()
        with self._lock:
            job["_persist_callback"] = self._persist_runtime_checkpoint
            _job_type, request, _cleanup_paths, source_monitor_id = self._consume_job_launch_state_locked(job)
            self._set_job_running_state_locked(job)
            self._persist_jobs_locked()

        if source_monitor_id:
            usenet_stream.record_stream_monitor_job(source_monitor_id, job)

        category = str(job.get("category") or "misc")
        source = self._normalize_job_source(job.get("source"))
        logger.info(f"Launching job {job.get('job_id')} ({category}, source={source})")

        def run() -> None:
            try:
                self._dispatch_job_execution(job, request)
            finally:
                duration_sec = time.time() - start_time_float
                with self._lock:
                    self._finalize_job_locked(
                        job,
                        duration_sec=duration_sec,
                        clear_active_fields=True,
                        remove_from_active=False,
                    )
                    self._persist_jobs_locked()
                self._try_start_queued()

        threading.Thread(target=run, daemon=True).start()

    def start_usenet_stream_job(
        self,
        *,
        category: str,
        stream_source_path: str,
        stream_source_name: str,
        release_name: Optional[str] = None,
        test_mode: bool = False,
        enable_duplicate_check: bool = True,
        indexer_id: Optional[str] = None,
        posting_server_name: Optional[str] = None,
        submit_mode: str = "post_and_submit",
        cleanup_paths: Optional[list[str]] = None,
        source_monitor_id: Optional[str] = None,
    ) -> str:
        base_name = self._normalize_job_name(release_name) or Path(stream_source_name).stem
        job_name = f"NZB Stream - {base_name}"
        return self.start_upload_job(
            category=category,
            reuse_running=False,
            source="usenet-stream",
            job_type="usenet_stream",
            stream_source_path=stream_source_path,
            stream_source_name=stream_source_name,
            release_name=base_name,
            test_mode=test_mode,
            enable_duplicate_check=enable_duplicate_check,
            indexer_id=indexer_id,
            posting_server_name=posting_server_name,
            submit_mode=submit_mode,
            cleanup_paths=cleanup_paths,
            source_monitor_id=source_monitor_id,
            job_name=job_name,
        )

    def _try_start_queued(self) -> None:
        with self._lock:
            if self._queue_processing_paused:
                return
            has_running = any(job["status"] in ("running", "stopping") for job in self._jobs.values())
            if has_running:
                return

            now_utc = datetime.now(timezone.utc)
            candidates: list[dict[str, Any]] = []
            for job in self._jobs.values():
                if job.get("status") == "paused" and job.get("resume_requested"):
                    candidates.append(job)
                    continue
                if job.get("status") != "queued":
                    continue
                due_at = self._parse_iso_datetime_utc(job.get("run_after"))
                if due_at and due_at > now_utc:
                    continue
                candidates.append(job)
            if not candidates:
                return
            next_job = min(
                candidates,
                key=lambda job: (
                    -int(job.get("priority") or 0),
                    str(job.get("started_at") or ""),
                ),
            )

            if next_job.get("status") == "paused":
                self._resume_paused_job_locked(str(next_job.get("job_id") or ""), next_job)
                self._persist_jobs_locked()
                return

        log_info(
            f"Dequeuing job {next_job['job_id']} ({next_job['category']}, source={self._normalize_job_source(next_job.get('source'))})..."
        )
        self._launch_job(next_job)

    def _queue_scheduler_loop(self) -> None:
        while not self._queue_scheduler_stop.wait(15):
            try:
                self._try_start_queued()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Queue scheduler loop error: {exc}")

    def get_active_jobs(self, *, compact: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            exclude = {
                "_kwargs",
                "_retry_request",
                "_paths",
                "_current_item_path",
                "_inflight_item_paths",
                "_current_prepare_tmp",
                "_clear_after_stop",
                "_hide_while_stopping",
                "_pause_ack_callback",
                "_persist_callback",
            }
            visible_jobs = [
                job
                for job in self._jobs.values()
                if not (job.get("_hide_while_stopping") and str(job.get("status") or "") == "stopping")
            ]
            snapshots: list[dict[str, Any]] = []
            for job in visible_jobs:
                snapshot = {k: v for k, v in job.items() if k not in exclude}
                if compact:
                    target_paths = self._get_job_target_paths(job)
                    snapshot.pop("target_paths", None)
                    snapshot["has_explicit_paths"] = bool(target_paths)
                    snapshot["target_path_count"] = len(target_paths)
                    events = snapshot.get("events")
                    if isinstance(events, list):
                        snapshot["events"] = events[-3:]
                snapshots.append(snapshot)
            return snapshots

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._jobs.get(job_id)

    def retry_job(self, job_id: str) -> tuple[bool, Optional[str], str]:
        """Create one new processing job from a failed job's immutable request."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None, "not-found"
            if job.get("status") != "failed" or not job.get("retry_eligible"):
                return False, None, "not-retryable"

            retry_request = job.get("_retry_request")
            if not isinstance(retry_request, dict):
                return False, None, "missing-request"
            retry_kwargs = retry_request.get("kwargs")
            if not isinstance(retry_kwargs, dict):
                return False, None, "missing-request"

            category = str(job.get("category") or "misc")
            paths = self._normalize_paths(retry_request.get("paths"))
            kwargs = dict(retry_kwargs)
            display_name = self._normalize_job_name(job.get("display_name")) or self._job_category_label(category)
            attempt_count = int(job.get("attempt_count") or 0)
            job["retry_eligible"] = False
            self._record_job_event(job, "retry-requested", "Retry requested")
            self._persist_jobs_locked()

        kwargs.update(
            reuse_running=False,
            source="retry",
            retry_of=job_id,
            attempt_count_base=attempt_count,
            job_name=f"{display_name} retry",
        )
        if paths:
            kwargs["paths"] = paths
        else:
            kwargs.pop("paths", None)

        try:
            new_job_id = self.start_upload_job(category, **kwargs)
        except Exception:
            with self._lock:
                original = self._jobs.get(job_id)
                if original:
                    original["retry_eligible"] = True
                    self._record_job_event(original, "retry-failed", "Unable to create retry job")
                    self._persist_jobs_locked()
            raise

        with self._lock:
            original = self._jobs.get(job_id)
            if original:
                original["retried_as"] = new_job_id
                self._record_job_event(original, "retried", f"Retry queued as {new_job_id}")
                self._persist_jobs_locked()
        return True, new_job_id, "queued"

    def _snapshot_revalidation_targets(self, include_paused: bool) -> list[dict[str, Any]]:
        """Collect snapshots of queued/paused processing jobs. Caller holds self._lock."""
        snapshots: list[dict[str, Any]] = []
        for job in self._jobs.values():
            status = str(job.get("status") or "")
            if status not in ("queued", "paused"):
                continue
            if not include_paused and status == "paused":
                continue
            if str(job.get("job_type") or "processing") != "processing":
                continue
            snapshots.append(
                {
                    "job_id": str(job.get("job_id") or ""),
                    "status": status,
                    "category": str(job.get("category") or "misc"),
                    "display_name": job.get("display_name"),
                    "paths": self._get_job_target_paths(job),
                    "kwargs": dict(job.get("_kwargs") or {}),
                }
            )
        return snapshots

    def _revalidation_hint_map(self, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        hint_map: dict[str, dict[str, Any]] = {}
        for hint in snapshot.get("kwargs", {}).get("item_hints") or []:
            if not isinstance(hint, dict):
                continue
            identity = self._normalize_job_path_identity(hint.get("path"))
            if not identity:
                continue
            hint_map[identity] = {
                k: v
                for k, v in dict(hint).items()
                if k
                not in {
                    "auto_select_ignored",
                    "skipped",
                    "completed",
                    "indexers",
                    "indexer_errors",
                }
            }
        return hint_map

    def _build_revalidation_candidates(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        current_paths = [p for p in snapshot.get("paths", []) if str(p or "").strip()]
        hint_map = self._revalidation_hint_map(snapshot)
        candidates: list[dict[str, Any]] = []
        for path_text in current_paths:
            identity = self._normalize_job_path_identity(path_text)
            hint = dict(hint_map.get(identity, {}))
            hint["path"] = path_text
            if "category" not in hint and snapshot.get("category"):
                hint["category"] = snapshot["category"]
            candidates.append(hint)
        return candidates

    def _apply_revalidation_result(
        self, snapshot: dict[str, Any], prepared: Any
    ) -> Optional[tuple[str, dict[str, Any]]]:
        """Apply one job's revalidation outcome. Returns ("updated"|"cancelled", job_update) or None."""
        new_paths = [
            str(item.get("path") or "").strip()
            for item in prepared.runnable_items
            if str(item.get("path") or "").strip()
        ]
        new_hints = [dict(item) for item in prepared.runnable_items if isinstance(item, dict)]
        new_categories = sorted(
            {
                str(item.get("category") or "").strip().lower()
                for item in new_hints
                if str(item.get("category") or "").strip()
            }
        )
        new_category = new_categories[0] if len(new_categories) == 1 else ("mixed" if new_categories else "")

        with self._lock:
            job = self._jobs.get(snapshot["job_id"])
            if not job:
                return None
            current_status = str(job.get("status") or "")
            if current_status not in ("queued", "paused"):
                return None

            if new_paths:
                self._set_job_target_paths(job, new_paths)
                kwargs = dict(job.get("_kwargs") or {})
                kwargs["item_hints"] = tuple(new_hints)
                job["_kwargs"] = kwargs
                if new_category:
                    job["category"] = new_category
                if not str(job.get("display_name") or "").strip():
                    job["display_name"] = self._default_job_name(
                        str(job.get("category") or "misc"),
                        len(new_paths),
                        new_paths,
                    )
                job["progress"] = "Revalidated against current rules"
                outcome = (
                    "updated",
                    {
                        "job_id": snapshot["job_id"],
                        "status": current_status,
                        "changed": True,
                        "kept": len(new_paths),
                        "skipped": len(prepared.skipped_items),
                    },
                )
            else:
                job["status"] = "cancelled"
                job["progress"] = "Cancelled after revalidation"
                job["stop_requested"] = False
                job["pause_requested"] = False
                job.pop("_kwargs", None)
                job.pop("_paths", None)
                self._cleanup_job_artifacts_locked(job)
                outcome = (
                    "cancelled",
                    {
                        "job_id": snapshot["job_id"],
                        "status": current_status,
                        "changed": True,
                        "kept": 0,
                        "skipped": len(prepared.skipped_items),
                    },
                )
            self._persist_jobs_locked()
            return outcome

    def revalidate_queued_jobs(self, *, include_paused: bool = True) -> dict[str, Any]:
        """Re-scan queued/paused processing jobs against the current rules."""
        with self._lock:
            snapshots = self._snapshot_revalidation_targets(include_paused)

        if not snapshots:
            return {"inspected": 0, "updated": 0, "cancelled": 0, "jobs": []}

        from logic.pending_scan import begin_scan_cache, end_scan_cache

        inspected = 0
        updated = 0
        cancelled = 0
        job_updates: list[dict[str, Any]] = []
        cache_token = begin_scan_cache()
        try:
            for snapshot in snapshots:
                inspected += 1
                candidates = self._build_revalidation_candidates(snapshot)
                prepared = self._prepare_queue_start_items(candidates)
                outcome = self._apply_revalidation_result(snapshot, prepared)
                if outcome is None:
                    continue
                kind, job_update = outcome
                if kind == "updated":
                    updated += 1
                else:
                    cancelled += 1
                job_updates.append(job_update)
        finally:
            end_scan_cache(cache_token)

        return {
            "inspected": inspected,
            "updated": updated,
            "cancelled": cancelled,
            "jobs": job_updates,
        }

    def delete_job(self, job_id: str) -> bool:
        self.stop_job(job_id)

        with self._lock:
            if job_id in self._jobs:
                self._cleanup_job_artifacts_locked(self._jobs[job_id])
                del self._jobs[job_id]
                if job_id in self._processes:
                    del self._processes[job_id]
                self._suspended_pids.pop(job_id, None)
                self._persist_jobs_locked()

                from logic.process_reaper import get_scheduler

                get_scheduler().add_job(
                    self._try_start_queued,
                    "date",
                    run_date=datetime.now(timezone.utc) + timedelta(seconds=0.5),
                )
                return True
        return False

    def clear_completed_jobs(self) -> int:
        with self._lock:
            to_delete = [
                job_id
                for job_id, job in self._jobs.items()
                if job["status"] in ["completed", "failed", "cancelled"]
                or (job.get("status") == "stopped" and not self._is_resumable_stopped_job_locked(job))
            ]
            for job_id in to_delete:
                del self._jobs[job_id]
            self._persist_jobs_locked()
            return len(to_delete)

    def clear_queued_jobs(self) -> int:
        with self._lock:
            cleared = 0
            to_delete: list[str] = []

            for job_id, job in self._jobs.items():
                status = str(job.get("status") or "")
                if status == "queued" or self._is_resumable_stopped_job_locked(job):
                    job["status"] = "cancelled"
                    job["progress"] = "Cancelled (queue cleared)"
                    self._cleanup_job_artifacts_locked(job)
                    job.pop("_kwargs", None)
                    job.pop("_paths", None)
                    to_delete.append(job_id)
                    cleared += 1
                    continue

                if status == "stopping" and bool(job.get("stop_requested")):
                    job["_clear_after_stop"] = True
                    job["progress"] = "Stopping... queued for removal"
                    cleared += 1

            for job_id in to_delete:
                del self._jobs[job_id]
            self._persist_jobs_locked()
            return cleared

    def promote_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = str(job.get("status") or "")
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return False
            elif status != "queued":
                return False
            queued = [
                queued_job
                for queued_job in self._jobs.values()
                if queued_job["status"] == "queued" or self._is_resumable_stopped_job_locked(queued_job)
            ]
            highest_priority = max((int(queued_job.get("priority") or 0) for queued_job in queued), default=0)
            job["priority"] = highest_priority + 1
            self._record_job_event(job, "promoted", f"Promoted to priority {job['priority']}")
            self._persist_jobs_locked()
            return True

    def set_job_priority(self, job_id: str, priority: int) -> tuple[bool, Optional[int]]:
        normalized = max(-100, min(100, int(priority)))
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False, None
            if str(job.get("status") or "") not in {"queued", "paused"}:
                return False, None
            job["priority"] = normalized
            self._record_job_event(job, "priority-changed", f"Priority set to {normalized}")
            self._persist_jobs_locked()
        self._try_start_queued()
        return True, normalized

    def get_queued_job_items(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            status = str(job.get("status") or "")
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return None
            elif status != "queued":
                return None

            items: list[dict[str, Any]] = []
            for idx, path in enumerate(self._get_job_target_paths(job), start=1):
                text = str(path)
                items.append({"index": idx, "path": text, "name": Path(text).name or text})
            return items

    def get_active_job_items(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        """Return remaining explicit paths for an active job on demand."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or str(job.get("status") or "") not in {"running", "paused", "stopping"}:
                return None
            paths = self._get_job_target_paths(job)
            if not paths:
                paths = self._normalize_paths(job.get("_snapshot_paths"))
            items: list[dict[str, Any]] = []
            for idx, path in enumerate(paths, start=1):
                text = str(path)
                items.append({"index": idx, "path": text, "name": Path(text).name or text})
            return items

    def get_finished_job_items(self, job_id: str) -> Optional[list[dict[str, Any]]]:
        """Return item paths for a recently finished job (current session only)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            status = str(job.get("status") or "")
            if status not in {"completed", "stopped", "failed", "cancelled"}:
                return None
            paths = self._normalize_paths(job.get("_completed_paths"))
            return [
                {"index": index, "path": str(path), "name": Path(str(path)).name or str(path)}
                for index, path in enumerate(paths, 1)
            ]

    def reorder_queued_job_items(self, job_id: str, ordered_paths: list[str]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = str(job.get("status") or "")
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return False
            elif status != "queued":
                return False

            current = self._get_job_target_paths(job)
            new_order = self._match_job_paths_by_identity(current, ordered_paths)
            if new_order is None:
                return False

            self._set_job_target_paths(job, new_order)
            self._persist_jobs_locked()
            return True

    def reorder_active_job_items(self, job_id: str, ordered_paths: list[str]) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") not in ("running", "paused"):
                return False

            current = self._get_job_target_paths(job)
            new_order = self._match_job_paths_by_identity(current, ordered_paths)
            if new_order is None:
                return False

            self._set_job_target_paths(job, new_order)
            self._persist_jobs_locked()
            return True

    def remove_queued_job_item(self, job_id: str, path: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = str(job.get("status") or "")
            if status == "stopped":
                if not self._is_resumable_stopped_job_locked(job):
                    return False
            elif status != "queued":
                return False

            current = self._get_job_target_paths(job)
            target_identity = self._normalize_job_path_identity(path)
            if not target_identity:
                return False

            updated = [
                current_path
                for current_path in current
                if self._normalize_job_path_identity(current_path) != target_identity
            ]
            if len(updated) == len(current):
                return False
            if updated:
                self._set_job_target_paths(job, updated)
            else:
                self._jobs.pop(job_id, None)

            self._persist_jobs_locked()
            return True

    def get_queue_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._queue_items)

    def add_queue_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            added = database.db_add_queue_items(items)
            if not added:
                return []

            original_by_identity: dict[str, dict[str, Any]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                identity = self._normalize_job_path_identity(item.get("path"))
                if identity:
                    original_by_identity[identity] = dict(item)

            merged_added: list[dict[str, Any]] = []
            for row in added:
                merged = dict(row)
                identity = self._normalize_job_path_identity(row.get("path"))
                extras = dict(original_by_identity.get(identity, {}))
                extras.pop("id", None)
                extras.pop("added_at", None)
                merged.update(extras)
                merged_added.append(merged)

            self._queue_items.extend(merged_added)
        return merged_added

    def remove_queue_item(self, item_id: int) -> bool:
        with self._lock:
            removed = database.db_remove_queue_item(item_id)
            if removed:
                self._queue_items = [queue_item for queue_item in self._queue_items if queue_item["id"] != item_id]
        return removed

    def clear_queue_items(self) -> int:
        with self._lock:
            count = database.db_clear_queue()
            self._queue_items.clear()
        return count

    def reorder_queue_items(self, item_ids: list[int]) -> bool:
        with self._lock:
            id_map = {queue_item["id"]: queue_item for queue_item in self._queue_items}
            if set(item_ids) != set(id_map.keys()):
                return False
            ok = database.db_reorder_queue(item_ids)
            if ok:
                self._queue_items = [id_map[item_id] for item_id in item_ids]
        return ok

    def start_queue(self, **kwargs: Any) -> list[str]:
        return self.start_queue_with_details(**kwargs)["job_ids"]

    def _raise_no_runnable_queue_items(self, prepared: Any) -> None:
        skipped_reasons = [
            f"{self._queue_item_label(item, idx)} ({reason})"
            for idx, (item, reason) in enumerate(prepared.skipped_items, start=1)
        ]
        detail = "Queue start failed: no runnable staged items"
        if skipped_reasons:
            detail = f"{detail}. " + "; ".join(skipped_reasons[:5])
            if len(skipped_reasons) > 5:
                detail = f"{detail}; and {len(skipped_reasons) - 5} more"
        log_info(f"[QUEUE-START] {detail}", "ERROR")
        raise ValueError(detail)

    def _remove_started_queue_items(self, runnable_ids: set[int], job_id: str) -> None:
        try:
            removed_count = database.db_remove_queue_items(sorted(runnable_ids))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[QUEUE-START] Job {job_id} started but staged-item cleanup failed: {exc}")
            return
        if removed_count != len(runnable_ids):
            logger.warning(
                f"[QUEUE-START] Expected to remove {len(runnable_ids)} staged item(s) after job creation, "
                f"but removed {removed_count}"
            )
        with self._lock:

            def _queue_item_id(value: Any) -> Optional[int]:
                text = str(value or "").strip()
                return int(text) if re.fullmatch(r"\d+", text) else None

            self._queue_items = [
                queue_item
                for queue_item in self._queue_items
                if _queue_item_id(queue_item.get("id")) not in runnable_ids
            ]

    def start_queue_with_details(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            if not self._queue_items:
                return {
                    "job_ids": [],
                    "jobs_created": 0,
                    "started_items": 0,
                    "skipped_items": 0,
                    "remaining_staged": 0,
                }
            items = list(self._queue_items)

        prepared = self._prepare_queue_start_items(items)
        logger.info(
            f"[QUEUE-START] staged_selected={len(items)} filtered_valid={len(prepared.runnable_items)} "
            f"excluded={len(prepared.skipped_items)}"
        )
        for index, (skipped_item, reason) in enumerate(prepared.skipped_items, start=1):
            log_info(
                f"[QUEUE-START] Skipping staged item '{self._queue_item_label(skipped_item, index)}': {reason}",
                "WARNING",
            )

        if not prepared.runnable_items:
            self._raise_no_runnable_queue_items(prepared)

        runnable_ids = {
            int(item["id"])
            for item in prepared.runnable_items
            if str(item.get("id") or "").strip() and re.fullmatch(r"\d+", str(item.get("id")))
        }
        categories = {
            str(item.get("category") or "").strip().lower()
            for item in prepared.runnable_items
            if str(item.get("category") or "").strip()
        }
        request_category = next(iter(categories)) if len(categories) == 1 else "mixed"
        job_id = self.start_processing_job_request(
            ProcessingJobRequest(
                category=request_category,
                test_mode=bool(kwargs.get("test_mode", False)),
                target_indexer_id=kwargs.get("indexer_id"),
                paths=tuple(
                    str(item.get("path") or "")
                    for item in prepared.runnable_items
                    if str(item.get("path") or "").strip()
                ),
                item_hints=tuple(dict(item) for item in prepared.runnable_items if isinstance(item, dict)),
                enable_duplicate_check=kwargs.get("enable_duplicate_check", True),
            ),
            reuse_running=False,
            source=str(kwargs.get("source") or "queue-start"),
        )
        if runnable_ids:
            self._remove_started_queue_items(runnable_ids, job_id)
        remaining_staged = len(self.get_queue_items())
        started_items = len(prepared.runnable_items)
        skipped_items = len(prepared.skipped_items)
        if skipped_items:
            log_info(
                f"[QUEUE-START] Created job {job_id} with final_queued={started_items}; "
                f"{skipped_items} staged item(s) remain in staging",
            )
        else:
            log_info(f"[QUEUE-START] Created job {job_id} with final_queued={started_items}")
        return {
            "job_ids": [job_id],
            "jobs_created": 1,
            "started_items": started_items,
            "skipped_items": skipped_items,
            "remaining_staged": remaining_staged,
        }

    def run_job_sync(
        self,
        category: str,
        limit: Optional[int] = None,
        skip_packs: bool = False,
        skip_episodes: bool = False,
        force: bool = False,
        test_mode: bool = False,
        indexer_id: Optional[str] = None,
        paths: Optional[list] = None,
    ) -> dict[str, Any]:
        job_id = f"cli-{uuid.uuid4().hex[:8]}"
        job: dict[str, Any] = JobState(
            job_id=job_id,
            category=category,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            progress="Starting...",
            current_stage="INITIALIZING",
            test_mode=test_mode,
        ).model_dump()

        with self._lock:
            self._jobs[job_id] = job

        token = set_thread_job(job)
        start_time = time.time()
        try:
            self._execute_processing_job(
                job,
                ProcessingJobRequest(
                    category=category,
                    limit=limit,
                    skip_packs=skip_packs,
                    skip_episodes=skip_episodes,
                    test_mode=test_mode,
                    target_indexer_id=indexer_id,
                    paths=tuple(self._normalize_paths(paths)),
                    force=force,
                ),
            )
        finally:
            reset_thread_job(token)

        duration_sec = time.time() - start_time

        with self._lock:
            self._finalize_job_locked(
                job,
                duration_sec=duration_sec,
                clear_active_fields=False,
                remove_from_active=True,
            )

        return job
