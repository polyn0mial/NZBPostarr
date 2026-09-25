"""Pure request shaping: build, expand, infer and collapse processing job requests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from core.config import get_config
from core.utils import normalize_submission_category
from logic.classify.tv_packs import is_season_pack_folder
from logic.jobs.models import (
    INVALID_CATEGORY_VALUES,
    ProcessingJobRequest,
    StreamJobRequest,
    normalize_job_path_identity,
)


def resolve_force_flag(
    *,
    enable_duplicate_check: Optional[bool] = None,
    force: Optional[bool] = None,
    test_mode: bool = False,
) -> bool:
    """Single source of truth for duplicate-check/force resolution."""
    conf = get_config()

    # Explicit `force` wins (used by CLI/headless).
    if force is not None:
        force_v = bool(force)
    else:
        # WebUI uses the inverse of "enable duplicate check".
        force_v = not bool(True if enable_duplicate_check is None else enable_duplicate_check)

    # Global setting and test mode always bypass duplicate checks.
    if not getattr(conf, "enable_duplicate_checking", True):
        force_v = True
    if test_mode:
        force_v = True

    logger.info(
        "[FORCE-FLAG] enable_duplicate_check={!r}  force={!r}  test_mode={!r}  => skip_dupes={!r}",
        enable_duplicate_check,
        force,
        test_mode,
        force_v,
    )
    return force_v


def build_processing_request(category: str, kwargs: dict[str, Any], paths: list[str]) -> ProcessingJobRequest:
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


def build_stream_request(category: str, kwargs: dict[str, Any]) -> StreamJobRequest:
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


def build_retry_request(kwargs: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    retry_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key not in {"cleanup_paths", "source_monitor_id", "manifest_path"}
    }
    # The job-state file is JSON, so normalize Path/tuple/model-like values
    # at creation instead of discovering an unserializable retry later.
    normalized_kwargs = json.loads(json.dumps(retry_kwargs, default=str))
    return {"kwargs": normalized_kwargs, "paths": list(paths)}


def clone_request_with_paths(
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
        skip_pack_expansion=request.skip_pack_expansion,
    )


def _dedup_result_or_original(
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
    return clone_request_with_paths(request, deduped_paths, deduped_hints)


def _hints_by_path(request: ProcessingJobRequest) -> dict[str, dict[str, Any]]:
    hints_by_path: dict[str, dict[str, Any]] = {}
    for hint in request.item_hints:
        path_text = str(hint.get("path") or "").strip()
        if path_text:
            hints_by_path[path_text] = dict(hint)
    return hints_by_path


def request_path_category(path: Path, hint: dict[str, Any]) -> str:
    """Resolve rewrite eligibility from the path, allowing a manual override only."""
    manual_category = normalize_submission_category(hint.get("manual_category"))
    if manual_category and manual_category not in INVALID_CATEGORY_VALUES:
        return str(manual_category)

    try:
        from logic.classify.explicit import resolve_explicit_path

        resolved = resolve_explicit_path(path)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug(f"Queue path category resolution failed for {path}: {exc}")
        return ""
    return str(normalize_submission_category(resolved.category))


def inferred_pack_row(parent: Path) -> dict[str, Any]:
    """The staged/request row for a TV season folder inferred from its selected episodes."""
    return {
        "path": str(parent),
        "name": parent.name,
        "category": "tv",
        "detected_category": "tv",
        "itype": "TV Show",
        "is_dir": True,
        "queue_category_source": "inferred-season-pack",
    }


def with_inferred_tv_pack_request_paths(request: ProcessingJobRequest) -> ProcessingJobRequest:
    """Insert season-folder paths into explicit TV episode-only job requests."""
    paths = list(request.paths)
    if not paths:
        return request

    hints_by_path = _hints_by_path(request)
    existing = {os.path.normpath(path) for path in paths if str(path).strip()}
    episodes_by_parent: dict[Path, list[Path]] = {}
    for path_text in paths:
        path = Path(str(path_text))
        hint = hints_by_path.get(str(path_text), {})
        if request_path_category(path, hint) != "tv":
            continue
        if path.is_file():
            episodes_by_parent.setdefault(path.parent, []).append(path)

    pack_parents = {
        parent
        for parent, episode_paths in episodes_by_parent.items()
        if os.path.normpath(str(parent)) not in existing
        and is_season_pack_folder(parent, episode_paths, require_source_token=True)
    }
    if not pack_parents:
        return request

    expanded_paths: list[str] = []
    expanded_hints: list[dict[str, Any]] = []
    inserted: set[Path] = set()
    for path_text in paths:
        path = Path(str(path_text))
        parent = path.parent if path.is_file() else None
        if parent is not None and parent in pack_parents and parent not in inserted:
            expanded_paths.append(str(parent))
            expanded_hints.append(inferred_pack_row(parent))
            inserted.add(parent)
        expanded_paths.append(str(path_text))
        hint = hints_by_path.get(str(path_text))
        if hint is not None:
            expanded_hints.append(hint)

    if not inserted:
        return request

    logger.info(f"[QUEUE-CREATE] inferred {len(inserted)} TV season pack path(s) before job start")
    return clone_request_with_paths(request, expanded_paths, expanded_hints)


def expand_explicit_pack_request_paths(request: ProcessingJobRequest) -> ProcessingJobRequest:
    """Expand selected TV/anime folders into pack rows plus nested item rows before job creation."""
    paths = list(request.paths)
    if not paths:
        return request

    try:
        from logic.classify.explicit import resolve_explicit_path
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug(f"Queue pack expansion unavailable: {exc}")
        return request

    hints_by_path = _hints_by_path(request)

    def build_hint(
        path: Path,
        category: str,
        *,
        is_dir: bool,
        source: str,
        base_hint: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        hint = dict(base_hint or {})
        hint.update(
            {
                "path": str(path),
                "name": path.name,
                "category": category,
                "detected_category": category,
                "itype": "Anime" if category == "anime" else ("TV Show" if is_dir else "TV Episode"),
                "queue_category_source": source,
            }
        )
        if is_dir:
            hint["is_dir"] = True
        else:
            hint.pop("is_dir", None)
        return hint

    expanded_paths: list[str] = []
    expanded_hints: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    expanded_groups = 0

    def push_path(path_text: str, hint: Optional[dict[str, Any]] = None) -> None:
        identity = normalize_job_path_identity(path_text)
        if not identity or identity in seen_identities:
            return
        seen_identities.add(identity)
        expanded_paths.append(path_text)
        if hint is not None:
            expanded_hints.append(dict(hint))

    def expand_pack_dir(
        source_dir: Path,
        category: str,
        episode_paths: list[Path],
        *,
        parent_hint: Optional[dict[str, Any]],
        source: str,
    ) -> int:
        if not episode_paths:
            return 0

        child_groups: dict[Path, list[Path]] = {}
        direct_files: list[Path] = []
        for episode_path in episode_paths:
            try:
                rel = episode_path.relative_to(source_dir)
            except ValueError:
                direct_files.append(episode_path)
                continue
            if len(rel.parts) > 1:
                pack_dir = source_dir / rel.parts[0]
                if pack_dir.is_dir():
                    child_groups.setdefault(pack_dir, []).append(episode_path)
                    continue
            direct_files.append(episode_path)

        inserted = 0
        for pack_dir in sorted(child_groups, key=lambda path: path.name.lower()):
            push_path(
                str(pack_dir),
                build_hint(pack_dir, category, is_dir=True, source=source, base_hint=parent_hint),
            )
            inserted += 1
            for child_file in sorted(child_groups[pack_dir], key=lambda path: path.name.lower()):
                push_path(
                    str(child_file),
                    hints_by_path.get(str(child_file))
                    or build_hint(child_file, category, is_dir=False, source=f"{source}-child"),
                )
                inserted += 1

        if direct_files:
            push_path(
                str(source_dir),
                build_hint(source_dir, category, is_dir=True, source=source, base_hint=parent_hint),
            )
            inserted += 1
            for direct_file in sorted(direct_files, key=lambda path: path.name.lower()):
                push_path(
                    str(direct_file),
                    hints_by_path.get(str(direct_file))
                    or build_hint(direct_file, category, is_dir=False, source=f"{source}-child"),
                )
                inserted += 1
        return inserted

    for path_text in paths:
        original_hint = hints_by_path.get(path_text)
        path = Path(str(path_text))
        if not path.exists() or not path.is_dir():
            push_path(path_text, original_hint)
            continue

        category_hint = normalize_submission_category(
            (original_hint or {}).get("category") or (original_hint or {}).get("detected_category") or request.category
        )
        itype_hint = str((original_hint or {}).get("itype") or "")
        try:
            resolution = resolve_explicit_path(path, category_hint=category_hint, itype_hint=itype_hint)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Queue pack expansion failed for {path}: {exc}")
            push_path(path_text, original_hint)
            continue

        resolved_category = normalize_submission_category(getattr(resolution, "category", ""))
        if resolved_category not in {"tv", "anime"}:
            corrected_hint = dict(original_hint or {})
            if resolved_category and resolved_category not in INVALID_CATEGORY_VALUES:
                corrected_hint = build_hint(
                    path,
                    resolved_category,
                    is_dir=True,
                    source="resolved-directory",
                    base_hint=corrected_hint,
                )
            push_path(path_text, corrected_hint or None)
            continue

        queue_files = [
            candidate
            for candidate in getattr(resolution, "queue_paths", ())
            if candidate.exists() and candidate.is_file()
        ]
        inserted = expand_pack_dir(
            path,
            resolved_category,
            queue_files,
            parent_hint=original_hint,
            source="resolved-pack-selection",
        )

        if inserted == 0:
            child_inserted = 0
            try:
                child_dirs = sorted(
                    (candidate for candidate in path.iterdir() if candidate.is_dir()),
                    key=lambda item: item.name.lower(),
                )
            except OSError:
                child_dirs = []
            for child in child_dirs:
                try:
                    child_resolution = resolve_explicit_path(
                        child,
                        category_hint=resolved_category,
                        itype_hint="Anime" if resolved_category == "anime" else "TV Show",
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    continue
                child_category = normalize_submission_category(getattr(child_resolution, "category", "")) or resolved_category
                child_files = [
                    candidate
                    for candidate in getattr(child_resolution, "queue_paths", ())
                    if candidate.exists() and candidate.is_file()
                ]
                child_inserted += expand_pack_dir(
                    child,
                    child_category,
                    child_files,
                    parent_hint=build_hint(child, child_category, is_dir=True, source="resolved-child-pack"),
                    source="resolved-child-pack",
                )
            inserted = child_inserted

        if inserted == 0:
            push_path(
                path_text,
                build_hint(path, resolved_category, is_dir=True, source="resolved-directory", base_hint=original_hint),
            )
            continue

        expanded_groups += 1

    if not expanded_groups:
        if tuple(expanded_paths) == request.paths:
            return request
        return clone_request_with_paths(request, expanded_paths, expanded_hints)

    logger.info(
        f"[QUEUE-CREATE] expanded {expanded_groups} selected TV/anime folder(s) into pack + nested queue items"
    )
    return clone_request_with_paths(request, expanded_paths, expanded_hints)


def collapse_overlapping_tv_request_paths(request: ProcessingJobRequest) -> ProcessingJobRequest:
    """Drop ancestor TV directories when explicit child season folders are also selected."""
    paths = list(request.paths)
    if len(paths) < 2:
        return request

    hints_by_path = _hints_by_path(request)

    deduped_paths: list[str] = []
    deduped_hints: list[dict[str, Any]] = []
    seen_identities: set[str] = set()
    for path_text in paths:
        identity = normalize_job_path_identity(path_text)
        if not identity or identity in seen_identities:
            continue
        seen_identities.add(identity)
        deduped_paths.append(path_text)
        hint = hints_by_path.get(path_text)
        if hint is not None:
            deduped_hints.append(hint)

    if len(deduped_paths) < 2:
        return _dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

    tv_dir_entries: list[tuple[int, Path]] = []
    for idx, path_text in enumerate(deduped_paths):
        try:
            path = Path(path_text)
        except TypeError:
            continue
        if not path.is_dir():
            continue

        hint = hints_by_path.get(path_text, {})
        if request_path_category(path, hint) != "tv":
            continue
        tv_dir_entries.append((idx, path))

    if len(tv_dir_entries) < 2:
        return _dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

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
        return _dedup_result_or_original(request, paths, deduped_paths, deduped_hints)

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
    return clone_request_with_paths(request, new_paths, new_hints)
