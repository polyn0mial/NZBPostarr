# Auto-split mixin from queueing.py - verbatim method bodies.

from logic.queueing_base import (
    Any, Optional, Path, ProcessingJobRequest, QueueStartSummary, StreamJobRequest, _QUEUE_SOURCE_TOKEN_RE, datetime, json, logger,
    looks_like_generic_tv_season_folder, normalize_submission_category, os, re, timezone,
)

class _QueueServiceMixinPart1:
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

