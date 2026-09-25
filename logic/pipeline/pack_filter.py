"""Filtered TV-pack staging: link or copy the selected episodes of a season pack into a temp folder."""

from __future__ import annotations

import os
import shutil
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from core.utils import VIDEO_EXTENSIONS, log_info
from logic.classify.tv_packs import (
    _has_direct_child_season_pack_dirs,
    _tv_pack_episode_rejection_reason,
    has_season_pack_name,
    is_season_pack,
    is_season_pack_folder,
)
from logic.pipeline.checkpoints import _append_cleanup_path, _normalize_runtime_path
from logic.pipeline.prepare import _ONE_GIB, _safe_fs_component
from logic.pipeline.record import _live_size_bytes


def _link_or_copy_filtered_file(source: Path, target: Path) -> None:
    """Stage a filtered pack file without duplicating data when possible."""
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        try:
            shutil.copy2(source, target)
        except shutil.SameFileError:
            pass  # already staged from a prior attempt; treat as success


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
        if has_season_pack_name(current.name) and not _has_direct_child_season_pack_dirs(current):
            return current
        if current.parent == current:
            return None
        try:
            current.relative_to(source_dir)
        except ValueError:
            return None
        current = current.parent


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
        and is_season_pack_folder(parent, episode_paths, require_source_token=False)
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


def _create_filtered_tv_pack_entries_for_selection(
    source_dir: Path,
    allowed_paths: list[Path],
    conf: Any,
    job: Optional[dict[str, Any]],
) -> tuple[list[tuple[Path, list[Path]]], set[str]]:
    """Stage filtered TV packs and return each pack with the episodes it contains."""
    allowed_files: list[Path] = []
    rejected_count = 0
    for path in allowed_paths:
        if not path.is_file():
            continue
        if _tv_pack_episode_rejection_reason(path, VIDEO_EXTENSIONS):
            rejected_count += 1
            continue
        allowed_files.append(path)
    if not source_dir.is_dir() or not allowed_files:
        if rejected_count:
            log_info(
                f"TV/anime pack selection {source_dir.name}: "
                f"{rejected_count} non-S##E##/invalid file(s) ignored"
            )
        return [], set()
    if rejected_count:
        log_info(
            f"TV/anime pack selection {source_dir.name}: "
            f"{rejected_count} non-S##E##/invalid file(s) ignored"
        )

    def pack_is_over_limit(pack_dir: Path) -> bool:
        folder_limit = getattr(conf, "folder_size_limit_gb", 99) or 0
        if not folder_limit or not getattr(conf, "folder_size_limit_enabled", True):
            return False
        size_gb = _live_size_bytes(pack_dir) / _ONE_GIB
        if size_gb <= folder_limit:
            return False
        log_info(
            f"TV/anime pack {pack_dir.name} is {size_gb:.1f} GB and exceeds "
            f"{folder_limit} GB; pack upload skipped, episodes still queued"
        )
        return True

    child_pack_files: dict[Path, list[Path]] = defaultdict(list)
    loose_files: list[Path] = []
    for path in allowed_files:
        pack_dir = _season_pack_dir_for_file(source_dir, path)
        if pack_dir is not None:
            child_pack_files[pack_dir].append(path)
        else:
            loose_files.append(path)

    if not child_pack_files:
        if _has_direct_child_season_pack_dirs(source_dir):
            log_info(
                f"TV parent selection {source_dir.name}: child season folders exist; "
                "parent folder itself will not be uploaded as a pack"
            )
            return [], set()
        if not has_season_pack_name(source_dir.name):
            log_info(
                f"TV parent selection {source_dir.name}: "
                f"{len(loose_files) or len(allowed_files)} loose episode file(s) queued as singles"
            )
            return [], set()
        if pack_is_over_limit(source_dir):
            return [], set()
        staged_pack = _create_filtered_tv_pack_staging(source_dir, allowed_paths, conf, job)
        if staged_pack is None:
            return [], set()
        episode_keys = {_normalize_runtime_path(path) for path in allowed_files}
        return [(staged_pack, sorted(allowed_files, key=lambda path: path.name.lower()))], episode_keys

    entries: list[tuple[Path, list[Path]]] = []
    episode_keys: set[str] = set()
    for child_dir in sorted(child_pack_files, key=lambda p: p.name.lower()):
        child_files = sorted(child_pack_files[child_dir], key=lambda path: path.name.lower())
        if pack_is_over_limit(child_dir):
            continue
        staged_pack = _create_filtered_tv_pack_staging(child_dir, child_files, conf, job)
        if staged_pack is not None:
            entries.append((staged_pack, child_files))
            episode_keys.update(_normalize_runtime_path(path) for path in child_files)

    if loose_files:
        log_info(f"TV parent selection {source_dir.name}: {len(loose_files)} loose episode file(s) queued as singles")

    return entries, episode_keys
