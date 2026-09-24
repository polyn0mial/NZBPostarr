# Auto-split from processing.py - verbatim symbol bodies, synthesized imports.

from logic.processing_base import (
    Any, Callable, Dict, Iterator, List, Optional, Path, dataclass, defaultdict, find_configured_root, get_thread_job, log_completed, log_info,
    log_success, logger, looks_like_tv_name, re, shutil, update_job_progress, uuid,
)
from logic.processing_g1 import (QueueItemValidation, _append_cleanup_path, _build_item_key, _has_tv_season_pack_name, _link_or_copy_filtered_file, _mediainfo_output_path, _normalize_processing_category, _normalize_processing_type, _normalize_runtime_path, _normalize_runtime_target_paths, _persist_runtime_job_checkpoint, _resolve_ambiguous_submission_category, _runtime_checkpoint_path_key, _safe_fs_component, _scan_release_media, _select_upload_server, _selected_indexers, _submission_category_label)  # noqa: F401

def _find_mediainfo_path(path: Path, conf: Any) -> Optional[Path]:
    """Return the generated mediainfo sidecar for an item when present."""
    candidate = _mediainfo_output_path(path, conf)
    return candidate if candidate.exists() else None

def _new_prepare_tmp_path(conf: Any, name: str, job: Optional[dict[str, Any]]) -> Path:
    """Create a unique temp path for this item and expose it to upload workers."""
    job_part = _safe_fs_component(str(job.get("job_id") or "job") if job else "job", max_length=40)
    item_part = _safe_fs_component(name)
    tmp = conf.tmp_sub / f"{job_part}-{item_part}-{uuid.uuid4().hex[:10]}"
    if job is not None:
        job["_current_prepare_tmp"] = str(tmp)
    return tmp

def _create_filtered_tv_pack_staging(
    source_dir: Path,
    allowed_paths: list[Path],
    conf: Any,
    job: Optional[dict[str, Any]],
) -> Optional[Path]:
    """Build a temporary season-pack folder containing only validated episode files."""
    allowed_files = [path for path in allowed_paths if path.is_file()]
    if not source_dir.is_dir() or not allowed_files:
        return None

    job_part = _safe_fs_component(str(job.get("job_id") or "job") if job else "job", max_length=40)
    staging_root = conf.tmp_sub / "_filtered_packs" / f"{job_part}-{uuid.uuid4().hex[:10]}"
    staged_pack = staging_root / source_dir.name

    try:
        for source in allowed_files:
            try:
                relative = source.relative_to(source_dir)
            except ValueError:
                relative = Path(source.name)
            _link_or_copy_filtered_file(source, staged_pack / relative)
    except OSError as exc:
        logger.error(f"Unable to stage filtered TV pack {source_dir.name}: {exc}")
        shutil.rmtree(staging_root, ignore_errors=True)
        return None

    _append_cleanup_path(job, staging_root)
    log_info(
        f"Staged filtered TV pack {source_dir.name}: "
        f"{len(allowed_files)} episode file(s), ignored extras excluded"
    )
    return staged_pack

def _has_direct_child_season_pack_dirs(path: Path) -> bool:
    """Return True when a selected TV folder is a parent that contains season-pack folders."""
    if not path.is_dir():
        return False
    try:
        for child in path.iterdir():
            if child.is_dir() and _has_tv_season_pack_name(child):
                return True
    except OSError:
        return False
    return False

def _season_pack_dir_for_file(source_dir: Path, file_path: Path) -> Optional[Path]:
    """Find the nearest season-pack folder between a selected folder and an episode file."""
    try:
        file_path.relative_to(source_dir)
    except ValueError:
        return None

    current = file_path.parent
    while True:
        if current == source_dir:
            return current if is_season_pack(current) else None
        if _has_tv_season_pack_name(current) and not _has_direct_child_season_pack_dirs(current):
            return current
        if current.parent == current:
            return None
        try:
            current.relative_to(source_dir)
        except ValueError:
            return None
        current = current.parent

def _looks_like_tv_season_pack_folder(path: Path, episode_paths: list[Path]) -> bool:
    """Return True for first-level season pack folders inferred from episode paths."""
    if not path.is_dir() or len(episode_paths) < 2:
        return False
    if _has_tv_season_pack_name(path):
        return True
    return False

def _inject_inferred_tv_pack_entries(
    raw_items: list[tuple[Path, str]],
    conf: Any,
    job: Optional[dict[str, Any]],
    already_staged_source_dirs: Optional[set[str]] = None,
) -> list[tuple[Path, str]]:
    """Infer selected season-pack folders when the UI sent only child episode paths."""
    existing_dirs = {
        _normalize_runtime_path(path)
        for path, cat in raw_items
        if cat in {"tv", "anime"} and path.is_dir()
    }
    # A staged or selected pack shares its season folder's name, so a parent
    # whose name is already queued must not be staged a second time.
    existing_dir_names = {
        path.name
        for path, cat in raw_items
        if cat in {"tv", "anime"} and path.is_dir()
    }
    episodes_by_parent: dict[Path, list[Path]] = defaultdict(list)
    for path, cat in raw_items:
        if cat in {"tv", "anime"} and path.is_file():
            episodes_by_parent[path.parent].append(path)

    pack_parents = {
        parent
        for parent, episode_paths in episodes_by_parent.items()
        if _normalize_runtime_path(parent) not in existing_dirs
        and (
            already_staged_source_dirs is None
            or _normalize_runtime_path(parent) not in already_staged_source_dirs
        )
        and parent.name not in existing_dir_names
        and _looks_like_tv_season_pack_folder(parent, episode_paths)
    }
    if not pack_parents:
        return raw_items

    staged_by_parent: dict[Path, Path] = {}
    for parent in sorted(pack_parents, key=lambda p: p.name.lower()):
        staged_pack = _create_filtered_tv_pack_staging(
            parent,
            sorted(episodes_by_parent[parent], key=lambda p: p.name.lower()),
            conf,
            job,
        )
        if staged_pack is not None:
            staged_by_parent[parent] = staged_pack

    if not staged_by_parent:
        return raw_items

    injected: list[tuple[Path, str]] = []
    inserted_parents: set[Path] = set()
    for path, cat in raw_items:
        parent = path.parent if cat in {"tv", "anime"} and path.is_file() else None
        staged_pack = staged_by_parent.get(parent) if parent is not None else None
        if staged_pack is not None and parent not in inserted_parents:
            injected.append((staged_pack, cat))
            inserted_parents.add(parent)
        injected.append((path, cat))

    logger.info(
        f"[QUEUE-RUN] inferred {len(inserted_parents)} TV season pack(s) from episode-only selection"
    )
    return injected

def _log_explicit_resolution(resolution: Any) -> None:
    """Emit user-facing logs for explicit-path classification and ignored extras."""
    type_label = _submission_category_label(str(getattr(resolution, "category", "")))
    source_name = getattr(getattr(resolution, "source_path", None), "name", "") or "item"
    flags = [str(flag).upper() for flag in getattr(resolution, "content_flags", ()) if str(flag).strip()]
    flag_suffix = f" [{' + '.join(flags)}]" if flags else ""
    log_info(f"Detected Type: {type_label}{flag_suffix} [{resolution.detection_method}] - {source_name}")
    override_note = str(getattr(resolution, "override_note", "") or "").strip()
    if override_note:
        log_info(f"↪ Override: {override_note}")
    for ignored in getattr(resolution, "ignored_paths", ()):
        log_info(f"⏩ Ignored '{ignored.path.name}' - {ignored.reason}")

def is_season_pack(path: Path) -> bool:
    """
    Check if a path represents a season pack (directory).
    Parent TV collection folders are not season packs; only leaf-ish folders
    with season naming and no direct child season-pack folders should upload as packs.
    """
    return path.is_dir() and _has_tv_season_pack_name(path) and not _has_direct_child_season_pack_dirs(path)

def _resolve_submission_category(path: Path, category: str, itype: str) -> str:
    """Validate and preserve the detected category used for indexer submission."""
    normalized_category = _normalize_processing_category(category)
    normalized_itype = _normalize_processing_type(itype)
    media_counts, _video_names = _scan_release_media(path)

    if normalized_category == "disc":
        if normalized_itype in {"anime", "tv", "tv_episode", "movie"}:
            normalized_category = {
                "anime": "anime",
                "tv": "tv",
                "tv_episode": "tv",
                "movie": "movies",
            }[normalized_itype]
        elif looks_like_tv_name(path.name) or any(
            re.search(r"S\d{1,2}[.\s_-]*E\d{1,3}", name, re.IGNORECASE) for name in _video_names
        ):
            normalized_category = "tv"
        else:
            normalized_category = "movies"

    allowed_categories = {"movies", "tv", "anime", "music", "audiobooks", "books", "apps", "misc"}
    if normalized_category not in allowed_categories:
        if normalized_itype in allowed_categories:
            raise ValueError(
                f"explicit category '{category}' is invalid; detected type '{itype}' cannot override the configured category"
            )
        raise ValueError(f"explicit category '{category}' is invalid")

    resolved_category = normalized_category
    if normalized_category in {"tv", "movies", "misc"}:
        resolved_category = _resolve_ambiguous_submission_category(path, normalized_category, normalized_itype)

    has_video = media_counts["video"] > 0
    if resolved_category == "misc" and has_video:
        raise ValueError("video content cannot be submitted as Misc")
    if resolved_category == "books" and media_counts["book"] == 0 and media_counts["audiobook"] == 0:
        raise ValueError("book category requires book metadata or book file types")
    if (
        resolved_category == "audiobooks"
        and media_counts["audiobook"] == 0
        and media_counts["music"] == 0
    ):
        raise ValueError("audiobook category requires audio file types")
    if resolved_category == "apps" and media_counts["app"] == 0:
        raise ValueError("apps category requires application/archive file types")
    if resolved_category == "music" and media_counts["music"] == 0:
        raise ValueError("music category requires audio file types")

    return resolved_category

def _build_duplicate_prefetch_state(
    conf: Any,
    sorted_items: list[tuple[Path, str]],
    configured_folders: list[Path],
    *,
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
) -> tuple[Dict[str, str], Dict[str, Dict[str, Optional[str]]], Callable[[Path], Optional[Path]]]:
    """Resolve item DB keys and batch-prefetch duplicate state for the job queue."""
    from core.database import get_duplicate_status_batch
    from core.registry import get_enabled_indexers

    source_root_cache: Dict[str, Optional[Path]] = {}

    def source_root_for(item_path: Path) -> Optional[Path]:
        item_key = _normalize_runtime_path(item_path)
        if item_key not in source_root_cache:
            source_root_cache[item_key] = find_configured_root(item_path, configured_folders)
        return source_root_cache[item_key]

    eligible_indexers = get_enabled_indexers(conf)
    if target_indexer_ids:
        allowed_ids = set(target_indexer_ids)
        eligible_indexers = [idx for idx in eligible_indexers if idx.id in allowed_ids]
    elif target_indexer_id:
        eligible_indexers = [idx for idx in eligible_indexers if idx.id == target_indexer_id]
    eligible_indexer_ids = [idx.id for idx in eligible_indexers]

    item_db_keys: Dict[str, str] = {}
    for item, _cat in sorted_items:
        item_db_keys[_normalize_runtime_path(item)] = _build_item_key(item, source_root_for(item))

    prefetched_dupes: Dict[str, Dict[str, Optional[str]]] = {}
    if eligible_indexer_ids:
        from logic.processing import _live_size_bytes

        item_keys = [item_db_keys[_normalize_runtime_path(item)] for item, _cat in sorted_items]
        # Current on-disk size per item key -- lets get_duplicate_status_batch tell
        # a genuine re-upload of the same name apart from a locally-replaced file
        # (same name, different size) instead of treating every name match as done.
        item_filesizes: Dict[str, int] = {
            item_db_keys[_normalize_runtime_path(item)]: _live_size_bytes(item)
            for item, _cat in sorted_items
        }
        prefetched_dupes = get_duplicate_status_batch(item_keys, eligible_indexer_ids, filesizes=item_filesizes)

    return item_db_keys, prefetched_dupes, source_root_for

def _iter_work_items(
    sorted_items: list[tuple[Path, str]],
    *,
    preserve_explicit_order: bool,
    runtime_job: Optional[dict[str, Any]],
    paths: Optional[List[str]],
) -> Iterator[tuple[int, tuple[Path, str]]]:
    """Yield the effective work order, respecting runtime reordering for targeted jobs."""
    if not preserve_explicit_order:
        yield from enumerate(sorted_items)
        return

    targeted_lookup = {_normalize_runtime_path(item): (item, cat) for item, cat in sorted_items}
    if runtime_job is None:
        yield from enumerate(sorted_items)
        return

    total = len(sorted_items)
    while targeted_lookup:
        # Items removed from the active job (QueueService.remove_active_job_item)
        # are dropped here, so a removal is honoured and not only hidden.
        for removed_path in list(runtime_job.get("_removed_item_paths") or []):
            targeted_lookup.pop(_normalize_runtime_path(Path(str(removed_path))), None)
        if not targeted_lookup:
            break
        runtime_paths = _normalize_runtime_target_paths(runtime_job, paths)
        next_key = None
        for raw_path in runtime_paths:
            resolved = _normalize_runtime_path(Path(raw_path))
            if resolved in targeted_lookup:
                next_key = resolved
                break

        if next_key is None:
            break

        runtime_job["target_paths"] = [
            raw_path for raw_path in runtime_paths if _normalize_runtime_path(Path(raw_path)) != next_key
        ]
        item, item_cat = targeted_lookup.pop(next_key)
        idx = total - len(targeted_lookup) - 1
        yield idx, (item, item_cat)

def _begin_runtime_item_checkpoint(
    job: Optional[dict[str, Any]],
    path: Path,
    *,
    make_current: bool,
) -> None:
    if job is None:
        return
    path_text = str(path)
    path_key = _runtime_checkpoint_path_key(path_text)
    inflight = [
        str(value)
        for value in (job.get("_inflight_item_paths") or [])
        if str(value).strip()
    ]
    if path_key and all(_runtime_checkpoint_path_key(value) != path_key for value in inflight):
        inflight.append(path_text)
    job["_inflight_item_paths"] = inflight
    if make_current:
        job["_current_item_path"] = path_text
    _persist_runtime_job_checkpoint(job)

def _complete_runtime_item_checkpoint(job: Optional[dict[str, Any]], path: Path) -> None:
    if job is None:
        return
    path_key = _runtime_checkpoint_path_key(path)
    job["_inflight_item_paths"] = [
        str(value)
        for value in (job.get("_inflight_item_paths") or [])
        if _runtime_checkpoint_path_key(value) != path_key
    ]
    if _runtime_checkpoint_path_key(job.get("_current_item_path")) == path_key:
        job.pop("_current_item_path", None)
    _persist_runtime_job_checkpoint(job)

def _classify_preview_source_item(
    item: Dict[str, Any],
    seen_paths: set[str],
) -> tuple[Optional[Path], str, str, str, Optional[str], str]:
    """Validate one raw preview item.

    Returns (path, display_path, name, category, reject_reason, outcome). reject_reason
    is None when the item is valid and should be queued. Extracted from
    preview_processing_items to keep its own branching down.
    """
    path_text = str(item.get("path") or "").strip()
    category = str(item.get("category") or item.get("detected_category") or "").strip().lower()
    name = str(item.get("name") or (Path(path_text).name if path_text else "") or "Unknown item")
    if not path_text:
        return None, "", name, category, "Missing source path", "invalid"
    path = Path(path_text)
    if not path.is_absolute():
        return path, path_text, name, category, "Source path must be absolute", "invalid"
    if not path.exists():
        return path, path_text, name, category, "Source path was not found", "invalid"
    if not category or category in {"all", "both", "mixed", "selected", "external"}:
        return path, str(path), name, category, "A concrete upload category is required", "invalid"
    normalized = _normalize_runtime_path(path)
    if normalized in seen_paths:
        return path, str(path), name, category, "Duplicate source path in selection", "excluded"
    return path, str(path), name, category, None, "valid"

def _plan_upload_runs(
    upload_sets: list[dict[str, Any]],
    *,
    all_servers: list[Any],
    dest_status: Dict[str, Optional[str]],
    indexer_map: Dict[str, Any],
    force: bool,
    is_new: bool,
    global_backfill: bool,
) -> list[tuple[dict[str, Any], Any]]:
    """Map logical upload sets to concrete server executions.

    Same-server destinations are merged into ONE physical upload regardless of
    priority, so an item is posted to Usenet once per server. The priority
    split is kept only as ``submission_groups``, so each indexer group still
    gets its own API submission (and its own "Priority " name prefix) without
    a second NNTP post. A set that includes priority destinations keeps the
    " (P)" suffix in its id so the uploader still labels the post as priority.
    """
    server_to_group: dict[str, dict[str, Any]] = {}
    selected_backbones: list[str] = []

    for upload_set in upload_sets:
        needed_dests: list[str] = []
        is_priority = upload_set["priority"]

        for dest_id in upload_set["dests"]:
            is_done = dest_status.get(dest_id) is not None
            if not force and is_done:
                logger.debug("[PLAN] dest=%s skipped (already done, force=False)", dest_id)
                continue
            indexer = indexer_map.get(dest_id)
            can_backfill = indexer.backfill if indexer else False
            if force or is_new or (global_backfill and can_backfill):
                needed_dests.append(dest_id)
                logger.debug(
                    "[PLAN] dest=%s ADDED (force=%r  is_new=%r  backfill=%r)",
                    dest_id,
                    force,
                    is_new,
                    can_backfill,
                )
            else:
                logger.debug(
                    "[PLAN] dest=%s skipped (force=%r  is_new=%r  backfill=%r/%r)",
                    dest_id,
                    force,
                    is_new,
                    global_backfill,
                    can_backfill,
                )

        if not needed_dests:
            continue

        server = _select_upload_server(upload_set["backbone"], all_servers, selected_backbones)
        selected_backbones.extend(backbone.lower() for backbone in server.backbone)

        bucket = server_to_group.setdefault(
            server.name,
            {"server": server, "priority_dests": [], "priority_ids": [], "normal_dests": [], "normal_ids": []},
        )
        if is_priority:
            bucket["priority_dests"].extend(needed_dests)
            bucket["priority_ids"].append(upload_set["id"])
        else:
            bucket["normal_dests"].extend(needed_dests)
            bucket["normal_ids"].append(upload_set["id"])

    raw_sets: list[tuple[dict[str, Any], Any]] = []
    for bucket in server_to_group.values():
        server = bucket["server"]
        submission_groups: list[dict[str, Any]] = []
        all_ids: list[str] = []
        all_dests: list[str] = []

        if bucket["priority_dests"]:
            submission_groups.append({"dests": list(dict.fromkeys(bucket["priority_dests"])), "priority": True})
            all_ids.extend(bucket["priority_ids"])
            all_dests.extend(bucket["priority_dests"])
        if bucket["normal_dests"]:
            submission_groups.append({"dests": list(dict.fromkeys(bucket["normal_dests"])), "priority": False})
            all_ids.extend(bucket["normal_ids"])
            all_dests.extend(bucket["normal_dests"])

        has_priority = bool(bucket["priority_dests"])
        raw_sets.append(
            (
                {
                    "id": "/".join(dict.fromkeys(all_ids)) + (" (P)" if has_priority else ""),
                    "dests": list(dict.fromkeys(all_dests)),
                    "backbone": server.backbone[0] if server.backbone else "Unknown",
                    "priority": has_priority,
                    "submission_groups": submission_groups,
                },
                server,
            )
        )

    raw_sets.sort(key=lambda value: value[0].get("priority", False), reverse=True)
    return raw_sets

def _item_exceeds_size_limit(path: Path, conf: Any, item_size_gb: float, name: str) -> bool:
    """Log + report whether `path` exceeds the configured folder/file size
    limit. Extracted from process_single to keep its own branching down;
    same behavior (including the log side effect) as before extraction."""
    if path.is_dir() and getattr(conf, "folder_size_limit_enabled", True):
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if folder_limit and item_size_gb > folder_limit:
            log_info(
                f"REJECTED: '{name}' ({item_size_gb:.1f} GB) exceeds folder size limit of {folder_limit} GB",
                "ERROR",
            )
            return True

    if path.is_file() and getattr(conf, "file_size_limit_enabled", True):
        file_limit = getattr(conf, "file_size_limit_gb", 0) or 0
        if file_limit and item_size_gb > file_limit:
            log_info(
                f"REJECTED: '{name}' ({item_size_gb:.1f} GB) exceeds file size limit of {file_limit} GB",
                "ERROR",
            )
            return True

    return False

def _resolve_target_indexers_for_single(
    conf: Any,
    target_indexer_id: Optional[str],
    target_indexer_ids: Optional[List[str]],
):
    """Resolve + log-on-empty the selected indexers for process_single.
    Extracted to keep process_single's own branching down; returns None
    (having already logged) where the caller previously returned 2."""
    all_indexers = _selected_indexers(conf, target_indexer_id, target_indexer_ids)
    if not all_indexers:
        if target_indexer_ids:
            log_info("Selected indexers were not found or are not enabled.")
        elif target_indexer_id:
            log_info(f"Indexer '{target_indexer_id}' not found or not enabled.")
        return None
    return all_indexers

def _resolve_job_categories(category: str) -> list[str]:
    from core.registry import get_available_categories

    category_lower = category.lower()
    active_categories = [item["id"] for item in get_available_categories()]
    if category_lower == "all":
        return active_categories or ["movies", "tv", "misc"]
    if category_lower == "both":
        return [item for item in active_categories if item in ("movies", "tv")] or ["movies", "tv"]
    if category_lower in {"mixed", "selected"}:
        return active_categories or ["movies", "tv", "anime", "misc"]
    return [category_lower]

def _build_item_hint_map(item_hints: Optional[List[Dict[str, Any]]]) -> dict[str, Dict[str, Any]]:
    hints: dict[str, Dict[str, Any]] = {}
    for item in item_hints or []:
        raw_path = item.get("path")
        if raw_path:
            hints[_normalize_runtime_path(Path(str(raw_path)))] = dict(item)
    return hints

@dataclass
class _JobRunState:
    """Mutable counters and the single in-flight upload for one processing run."""

    total: int
    effective_limit: Optional[int]
    test_mode: bool
    success_count: int = 0
    duplicate_count: int = 0
    failure_count: int = 0
    completed_count: int = 0
    active_upload_future: Any = None
    active_upload_validation: Optional[QueueItemValidation] = None
    stop_processing: bool = False
    limit_reached: bool = False
    consecutive_failures: int = 0
    max_consecutive_failures: int = 5

    def note_failure(self) -> None:
        """Count a failed item and warn on long failure runs; the job keeps going."""
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            log_info(f"{self.consecutive_failures} consecutive item failures - continuing job.", "WARN")

    def publish_counts(self) -> None:
        update_job_progress(
            processed=self.completed_count,
            total=self.total,
            percent=int((self.completed_count / self.total) * 100) if self.total else 0,
            skipped=self.duplicate_count,
        )

    def complete_active_upload(self, *, wait: bool) -> None:
        if self.active_upload_future is None or self.active_upload_validation is None:
            return
        if not wait and not self.active_upload_future.done():
            return

        result = self.active_upload_future.result()
        current_job = get_thread_job()
        if current_job is not None and result != 2:
            _complete_runtime_item_checkpoint(current_job, self.active_upload_validation.path)

        if result == 0:
            self.consecutive_failures = 0
            self.success_count += 1
            self.completed_count += 1
            prefix = "[TEST] " if self.test_mode else ""
            log_completed(
                f"{prefix}COMPLETED: {self.active_upload_validation.path.name} "
                f"({self.success_count}/{self.effective_limit or 'All'})"
            )
            self.publish_counts()
            if self.effective_limit and self.success_count >= self.effective_limit:
                log_success(f"Target limit reached: {self.success_count} uploads.")
                self.limit_reached = True
        elif result == 1:
            self.duplicate_count += 1
            self.completed_count += 1
            self.publish_counts()
        elif result == 2:
            self.stop_processing = True
        elif result in (3, 4):
            self.failure_count += 1
            self.completed_count += 1
            self.publish_counts()
            self.note_failure()

        self.active_upload_future = None
        self.active_upload_validation = None

@dataclass(frozen=True)
class _JobExecutionContext:
    force: bool
    test_mode: bool
    target_indexer_id: Optional[str]
    target_indexer_ids: Optional[List[str]]
    configured_folders: List[Any]
    skip_enabled: bool
    skip_config: Optional[Dict[Any, Any]]
    prefetched_item_db_keys: Dict[str, str]
    prefetched_dupes: Dict[str, Dict[str, Optional[str]]]
    prefetched_source_root_for: Optional[Callable[[Path], Optional[Path]]]

def _prefetched_validation_state(
    context: _JobExecutionContext,
    item: Path,
) -> tuple[Optional[Dict[str, Optional[str]]], Optional[Path]]:
    destination_status = None
    normalized_item = _normalize_runtime_path(item)
    if context.prefetched_item_db_keys:
        item_db_key = context.prefetched_item_db_keys.get(normalized_item)
        if item_db_key:
            destination_status = context.prefetched_dupes.get(item_db_key)

    base_folder = context.prefetched_source_root_for(item) if context.prefetched_source_root_for else None
    return destination_status, base_folder

def _guard_no_raw_items(category: str, paths: Optional[List[str]]) -> bool:
    """Handle the empty-raw_items case for run_job.

    Raises ValueError when a targeted (paths-based) run found nothing valid.
    Returns True to signal the caller should return early for an untargeted
    scan that simply found no items. Extracted from run_job to keep its own
    branching down.
    """
    if paths:
        selected_count = len(paths)
        reason = f"No valid items remained after filtering: selected={selected_count} filtered=0 final_queued=0"
        logger.error(f"[QUEUE-RUN] {reason}")
        update_job_progress(msg=reason, status="failed", total=selected_count, processed=0, percent=0)
        raise ValueError(reason)
    log_info(f"No items found to process for category: {category}")
    return True

def kill_child_processes(include_running: bool = False) -> None:
    """Kill orphaned / hung tool processes (nyuu, rar, parpar).

    Args:
        include_running: If True, also kills processes that belong to
                         active upload jobs (nuclear option for restarts).
    """
    from logic.process_reaper import reap_all_tools, reap_orphans

    if include_running:
        reap_all_tools(include_active=True)
    else:
        reap_orphans(force=True)

