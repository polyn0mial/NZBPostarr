"""Job planning: collect, order and size-limit the items a job will upload, and pick its indexers."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from loguru import logger

from core.logging import log_info, log_verbose
from core.paths import path_key
from logic.classify.explicit import resolve_explicit_path
from logic.classify.tv_packs import get_tv_sort_key, is_season_pack
from logic.jobs.context import update_job_progress, wait_for_job_resume
from logic.pending.roots import scan_configured_items
from logic.pipeline.checkpoints import _normalize_runtime_target_paths
from logic.pipeline.pack_filter import _create_filtered_tv_pack_entries_for_selection, _inject_inferred_tv_pack_entries
from logic.pipeline.prepare import _ONE_GIB, _descendant_files, _safe_mtime
from logic.pipeline.record import _live_size_bytes
from logic.pipeline.submission_category import _submission_category_label


def _resolve_targeted_path(raw_path: str) -> Optional[Path]:
    """Targeted jobs only accept explicit absolute paths."""
    if not raw_path:
        return None
    path = Path(str(raw_path))
    return path if path.is_absolute() else None


def _selected_indexers(
    conf: Any, target_indexer_id: Optional[str], target_indexer_ids: Optional[List[str]]
) -> list[Any]:
    """Return the enabled indexers selected for this item run."""
    from core.indexers.registry import get_enabled_indexers

    indexers = get_enabled_indexers(conf)
    if target_indexer_ids:
        allowed_ids = set(target_indexer_ids)
        indexers = [indexer for indexer in indexers if indexer.id in allowed_ids]
    elif target_indexer_id:
        indexers = [indexer for indexer in indexers if indexer.id == target_indexer_id]
    return indexers


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

    targeted_lookup = {path_key(item): (item, cat) for item, cat in sorted_items}
    if runtime_job is None:
        yield from enumerate(sorted_items)
        return

    total = len(sorted_items)
    while targeted_lookup:
        # Items removed from the active job (QueueService.remove_active_job_item)
        # are dropped here, so a removal is honoured and not only hidden.
        for removed_path in list(runtime_job.get("_removed_item_paths") or []):
            targeted_lookup.pop(path_key(Path(str(removed_path))), None)
        if not targeted_lookup:
            break
        runtime_paths = _normalize_runtime_target_paths(runtime_job, paths)
        next_key = None
        for raw_path in runtime_paths:
            resolved = path_key(Path(raw_path))
            if resolved in targeted_lookup:
                next_key = resolved
                break

        if next_key is None:
            break

        runtime_job["target_paths"] = [
            raw_path for raw_path in runtime_paths if path_key(Path(raw_path)) != next_key
        ]
        item, item_cat = targeted_lookup.pop(next_key)
        idx = total - len(targeted_lookup) - 1
        yield idx, (item, item_cat)


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
    from core.indexers.categories import get_available_categories

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
            hints[path_key(Path(str(raw_path)))] = dict(item)
    return hints


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


def _plan_explicit_items(
    raw_items: list[tuple[Path, str]],
    *,
    skip_packs: bool,
    folder_limit: int,
    folder_enabled: bool,
    file_limit: int,
    file_enabled: bool,
) -> tuple[list[tuple[Path, str]], set[str], set[str]]:
    sorted_items: list[tuple[Path, str]] = []
    seen_paths: set[str] = set()
    oversized_folders: set[str] = set()
    oversized_files: set[str] = set()

    def append_item(path: Path, category: str) -> None:
        normalized = path_key(path)
        if normalized in seen_paths:
            return
        seen_paths.add(normalized)
        sorted_items.append((path, category))
        if file_limit and file_enabled and path.is_file():
            if _live_size_bytes(path) / _ONE_GIB > file_limit:
                oversized_files.add(normalized)

    for path, category in raw_items:
        normalized = path_key(path)
        if normalized in seen_paths:
            continue

        if category in {"tv", "anime"}:
            season_pack = is_season_pack(path)
            if skip_packs and season_pack:
                continue
            if folder_limit and folder_enabled and season_pack:
                if _live_size_bytes(path) / _ONE_GIB > folder_limit:
                    oversized_folders.add(normalized)
                    for child in _descendant_files(path, video_only=True, sorted_names=True):
                        append_item(child, category)
                    continue
            append_item(path, category)
            continue

        if folder_limit and folder_enabled and path.is_dir():
            if _live_size_bytes(path) / _ONE_GIB > folder_limit:
                oversized_folders.add(normalized)
                for child in _descendant_files(path, video_only=False, sorted_names=True):
                    append_item(child, category)
                continue

        append_item(path, category)

    return sorted_items, oversized_folders, oversized_files


def _expand_oversized_category_items(
    cat_items: list[Path],
    *,
    folder_limit: int,
    oversized_folders: set[str],
    season_packs_only: bool,
) -> list[Path]:
    expanded: list[Path] = []
    seen = {path_key(path) for path in cat_items}
    for path in cat_items:
        if season_packs_only:
            if not is_season_pack(path):
                continue
        elif not path.is_dir():
            continue
        if _live_size_bytes(path) / _ONE_GIB <= folder_limit:
            continue

        oversized_folders.add(path_key(path))
        for child in _descendant_files(
            path,
            video_only=season_packs_only,
            sorted_names=season_packs_only,
        ):
            child_key = path_key(child)
            if child_key in seen:
                continue
            expanded.append(child)
            seen.add(child_key)

    if not expanded:
        return cat_items
    retained = [
        path for path in cat_items if path_key(path) not in oversized_folders
    ]
    return [*retained, *expanded]


def _plan_sorted_items(
    raw_items: list[tuple[Path, str]],
    cats: list[str],
    *,
    preserve_explicit_order: bool,
    skip_packs: bool,
    folder_limit: int,
    folder_enabled: bool,
    file_limit: int,
    file_enabled: bool,
) -> tuple[list[tuple[Path, str]], set[str], set[str]]:
    """Deduplicate, sort, and expand raw items into the concrete processing queue."""
    if preserve_explicit_order:
        return _plan_explicit_items(
            raw_items,
            skip_packs=skip_packs,
            folder_limit=folder_limit,
            folder_enabled=folder_enabled,
            file_limit=file_limit,
            file_enabled=file_enabled,
        )

    by_cat: dict[str, list[Path]] = defaultdict(list)
    seen_paths: set[str] = set()
    for path, cat in raw_items:
        normalized = path_key(path)
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        by_cat[cat].append(path)

    sorted_items: list[tuple[Path, str]] = []
    oversized_folders: set[str] = set()
    oversized_files: set[str] = set()

    for cat in cats:
        if cat not in by_cat:
            continue

        cat_items = list(by_cat[cat])
        if cat in {"tv", "anime"}:
            cat_items.sort(key=get_tv_sort_key)
            if skip_packs:
                cat_items = [path for path in cat_items if not is_season_pack(path)]
            elif folder_limit and folder_enabled:
                cat_items = _expand_oversized_category_items(
                    cat_items,
                    folder_limit=folder_limit,
                    oversized_folders=oversized_folders,
                    season_packs_only=True,
                )
                cat_items.sort(key=get_tv_sort_key)
            log_verbose(f"Sorted TV items: {[path.name for path in cat_items]}")
        else:
            if folder_limit and folder_enabled:
                cat_items = _expand_oversized_category_items(
                    cat_items,
                    folder_limit=folder_limit,
                    oversized_folders=oversized_folders,
                    season_packs_only=False,
                )
            cat_items.sort(key=_safe_mtime, reverse=True)

        if file_limit and file_enabled:
            for path in cat_items:
                if not path.is_file():
                    continue
                file_size = _live_size_bytes(path) / _ONE_GIB
                if file_size > file_limit:
                    oversized_files.add(path_key(path))

        sorted_items.extend((path, cat) for path in cat_items)

    return sorted_items, oversized_folders, oversized_files


def _append_targeted_resolution_items(
    raw_items: list[tuple[Path, str]],
    *,
    selected_path: Path,
    resolution: Any,
    conf: Any,
    runtime_job: Optional[dict[str, Any]],
    process_tv_episodes: bool,
    staged_pack_source_dirs: set[str],
) -> None:
    resolved_paths = list(resolution.queue_paths)
    episode_paths_added_with_packs: set[str] = set()
    skip_parent_series_folder = False

    if (
        resolution.category in {"tv", "anime"}
        and selected_path.is_dir()
        and resolved_paths
        and any(resolved_path.is_file() for resolved_path in resolved_paths)
    ):
        pack_entries, episode_paths_added_with_packs = _create_filtered_tv_pack_entries_for_selection(
            selected_path,
            resolved_paths,
            conf,
            runtime_job,
        )
        skip_parent_series_folder = bool(pack_entries)
        if pack_entries:
            staged_pack_source_dirs.add(path_key(selected_path))
        for staged_pack, pack_episode_paths in pack_entries:
            raw_items.append((staged_pack, resolution.category))
            if process_tv_episodes:
                raw_items.extend((episode_path, resolution.category) for episode_path in pack_episode_paths)

    selected_key = path_key(selected_path)
    for resolved_path in resolved_paths:
        if (
            skip_parent_series_folder
            and resolved_path.is_dir()
            and path_key(resolved_path) == selected_key
        ):
            continue
        if resolution.category in {"tv", "anime"} and not process_tv_episodes and resolved_path.is_file():
            continue
        if path_key(resolved_path) in episode_paths_added_with_packs:
            continue
        raw_items.append((resolved_path, resolution.category))


def _collect_targeted_job_items(
    *,
    paths: List[str],
    item_hints: Optional[List[Dict[str, Any]]],
    category: str,
    conf: Any,
    runtime_job: Optional[dict[str, Any]],
    process_tv_episodes: bool,
) -> Optional[list[tuple[Path, str]]]:
    """Resolve the selected paths; None means the user stopped the job meanwhile."""
    log_info(f"Targeted upload: {len(paths)} item(s)")
    raw_items: list[tuple[Path, str]] = []
    staged_pack_source_dirs: set[str] = set()
    item_hint_map = _build_item_hint_map(item_hints)

    for raw_target in paths:
        if runtime_job and not wait_for_job_resume(runtime_job):
            log_info("Job stopped by user during path resolution.")
            update_job_progress(status="stopped")
            return None
        selected_path = _resolve_targeted_path(raw_target)
        if not selected_path:
            log_info(f"⏩ Non-absolute targeted path rejected: {raw_target}", "WARN")
            continue
        if not selected_path.exists():
            log_info(f"⏩ Missing path: {selected_path} (skipped)", "WARN")
            continue
        if selected_path.name.startswith("."):
            continue

        hint = item_hint_map.get(path_key(selected_path), {})
        category_hint = str(hint.get("manual_category") or "")
        itype_hint = str(hint.get("itype") or "") if category_hint else ""
        resolution = resolve_explicit_path(
            selected_path,
            category_hint=category_hint,
            itype_hint=itype_hint,
            respect_explicit_hint=bool(category_hint),
        )
        _log_explicit_resolution(resolution)
        if "disc" in resolution.content_flags:
            raw_items.append((selected_path, "disc"))
            continue
        _append_targeted_resolution_items(
            raw_items,
            selected_path=selected_path,
            resolution=resolution,
            conf=conf,
            runtime_job=runtime_job,
            process_tv_episodes=process_tv_episodes,
            staged_pack_source_dirs=staged_pack_source_dirs,
        )

    return _inject_inferred_tv_pack_entries(raw_items, conf, runtime_job, staged_pack_source_dirs)


def _collect_scanned_job_items(
    conf: Any,
    categories: list[str],
    *,
    process_tv_episodes: bool,
) -> list[tuple[Path, str]]:
    raw_items = []
    for scan_item in scan_configured_items(conf, must_exist=True):
        if scan_item.category not in categories:
            continue
        if scan_item.category in {"tv", "anime"} and not process_tv_episodes and scan_item.path.is_file():
            continue
        raw_items.append((scan_item.path, scan_item.category))
    return raw_items
