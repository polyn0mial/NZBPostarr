# Auto-split mixin from queueing.py - verbatim method bodies.
# W11-B10: request shaping lives in logic/jobs/requests.py and job fields in logic/jobs/models.py;
# the one-line delegations below are removed with the mixins in W12-B12.

from core.media import VIDEO_EXTENSIONS
from logic.classify.tv_packs import has_season_pack_name, is_season_pack_folder
from logic.jobs import models as job_models
from logic.jobs import requests as job_requests
from logic.queueing_base import (
    Any, Optional, Path, ProcessingJobRequest, QueueStartSummary, StreamJobRequest, datetime, logger,
    normalize_category, os, timezone,
)

class _QueueServiceMixinPart1:
    @staticmethod
    def _normalize_job_source(source: Any) -> str:
        return job_models.normalize_job_source(source)


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
        manual_category = normalize_category(item.get("manual_category"))
        path_text = str(item.get("path") or "").strip()
        if path_text:
            try:
                from logic.classify.explicit import resolve_explicit_path

                resolved = resolve_explicit_path(
                    Path(path_text),
                    category_hint=manual_category,
                    itype_hint=str(item.get("itype") or "") if manual_category else "",
                    respect_explicit_hint=bool(manual_category),
                )
                resolved_category = normalize_category(resolved.category)
                if resolved_category and resolved_category not in cls._QUEUE_INVALID_CATEGORY_VALUES:
                    source = "manual_category" if manual_category else f"path:{resolved.detection_method}"
                    return resolved_category, source
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.debug(f"Queue item category recovery failed for {path_text}: {exc}")
            return "", ""

        detected_category = normalize_category(item.get("detected_category"))
        itype_category = normalize_category(item.get("itype"))
        explicit_category = normalize_category(item.get("category"))
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
        from logic.classify.walk import begin_scan_cache, end_scan_cache

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

        runnable_items = cls._collapse_overlapping_queue_start_items(runnable_items)

        return QueueStartSummary(
            runnable_items=tuple(cls._inject_inferred_tv_pack_queue_items(runnable_items)),
            skipped_items=tuple(skipped_items),
        )

    @staticmethod
    def _queue_item_path_text(item: dict[str, Any]) -> str:
        return str(item.get("path") or "").strip()

    @staticmethod
    def _normalize_queue_overlap_path(path: Any) -> str:
        text = str(path or "").strip()
        if not text:
            return ""
        try:
            normalized = Path(text).resolve().as_posix()
        except OSError:
            normalized = Path(text).as_posix()
        normalized = normalized.rstrip("/")
        return normalized.casefold() if os.name == "nt" else normalized

    @classmethod
    def _should_preserve_queue_start_dir(cls, item: dict[str, Any], category: str, raw_path: Path) -> bool:
        if category not in {"tv", "anime"} or not raw_path.exists() or not raw_path.is_dir():
            return False

        item_type = str(item.get("itype") or "").strip().lower()
        if item_type in {"tv show", "anime"}:
            return True

        try:
            direct_video_count = sum(
                1 for child in raw_path.iterdir() if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS
            )
        except OSError:
            direct_video_count = 0
        if direct_video_count >= 1:
            return True

        try:
            recursive_video_count = sum(
                1 for child in raw_path.rglob("*") if child.is_file() and child.suffix.lower() in VIDEO_EXTENSIONS
            )
        except OSError:
            recursive_video_count = 0

        if recursive_video_count < 2:
            return False

        return has_season_pack_name(raw_path.name)

    @classmethod
    def _collapse_overlapping_queue_start_items(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep a staged TV/anime pack folder and drop its separately staged descendants."""
        grouped: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
        for idx, item in enumerate(items):
            category = str(item.get("category") or "").strip().lower()
            normalized_path = cls._normalize_queue_overlap_path(item.get("path"))
            if not category or not normalized_path:
                continue
            grouped.setdefault(category, []).append((idx, item, normalized_path))

        dropped_indices: set[int] = set()
        for category, entries in grouped.items():
            kept_descendants: list[str] = []
            dropped_here = 0
            for idx, item, normalized_path in sorted(entries, key=lambda entry: len(entry[2]), reverse=True):
                raw_path = Path(str(item.get("path") or "").strip())
                if raw_path.exists() and not raw_path.is_dir():
                    kept_descendants.append(normalized_path)
                    continue

                if any(
                    child_path.startswith(f"{normalized_path}/") for child_path in kept_descendants
                ) and cls._should_preserve_queue_start_dir(item, category, raw_path):
                    kept_descendants = [
                        child_path for child_path in kept_descendants if not child_path.startswith(f"{normalized_path}/")
                    ]
                    kept_descendants.append(normalized_path)
                    dropped_here += 1
                    dropped_indices.update(
                        child_idx
                        for child_idx, _child_item, child_path in entries
                        if child_idx != idx and child_path.startswith(f"{normalized_path}/")
                    )
                    continue

                kept_descendants.append(normalized_path)

            if dropped_here:
                logger.info(
                    f"[QUEUE-START] Category '{category}' collapsed {dropped_here} selected parent pack overlap(s)"
                )

        return [item for idx, item in enumerate(items) if idx not in dropped_indices]

    @classmethod
    def _inject_inferred_tv_pack_queue_items(cls, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert season-folder pack rows when staging contains only child episode files.

        Selected pack folders are expanded once, at job creation
        (_expand_explicit_pack_request_paths), so no child episodes are added here.
        """
        existing_dirs = {
            os.path.normpath(cls._queue_item_path_text(item))
            for item in items
            if str(item.get("category") or "").strip().lower() == "tv"
            and cls._queue_item_path_text(item)
            and Path(cls._queue_item_path_text(item)).is_dir()
        }
        episodes_by_parent: dict[Path, list[Path]] = {}
        for item in items:
            if str(item.get("category") or "").strip().lower() != "tv":
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
            and is_season_pack_folder(parent, episode_paths, require_source_token=True)
        }
        if not pack_parents:
            return items

        injected: list[dict[str, Any]] = []
        inserted: set[Path] = set()
        for item in items:
            path = Path(cls._queue_item_path_text(item))
            parent = path.parent if path.is_file() else None
            if parent in pack_parents and parent not in inserted:
                injected.append(job_requests.inferred_pack_row(parent))
                inserted.add(parent)
            injected.append(item)

        logger.info(f"[QUEUE-START] inferred {len(inserted)} TV season pack row(s) from staged episodes")
        return injected

    @staticmethod
    def _build_processing_request(category: str, kwargs: dict[str, Any], paths: list[str]) -> ProcessingJobRequest:
        return job_requests.build_processing_request(category, kwargs, paths)

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
            request = job_requests.build_stream_request(str(job.get("category") or "misc"), kwargs)
        else:
            request = job_requests.build_processing_request(str(job.get("category") or "misc"), kwargs, paths)

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

    @staticmethod
    def _normalize_paths(paths: Any) -> list[str]:
        return job_models.normalize_paths(paths)

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
        return job_models.job_target_paths(job)

    def _set_job_target_paths(self, job: dict[str, Any], paths: Any) -> None:
        job_models.set_job_target_paths(job, paths)

    @staticmethod
    def _normalize_job_path_identity(path: Any) -> Optional[str]:
        return job_models.normalize_job_path_identity(path)

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
        # Force upload passes skip_pack_expansion: it starts at once with exactly
        # the rows the user picked, without a pack walk.
        if not request.skip_pack_expansion:
            request = self._expand_explicit_pack_request_paths(request)
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
    def _with_inferred_tv_pack_request_paths(cls, request: ProcessingJobRequest) -> ProcessingJobRequest:
        return job_requests.with_inferred_tv_pack_request_paths(request)

    @classmethod
    def _expand_explicit_pack_request_paths(cls, request: ProcessingJobRequest) -> ProcessingJobRequest:
        return job_requests.expand_explicit_pack_request_paths(request)
