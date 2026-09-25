"""Lazy pending children: resolve a /api/pending/children request and build that one level."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from core.utils import log_backend_timing
from logic.classify.anime import cached_lookup
from logic.classify.explicit import resolve_explicit_path
from logic.pending.completion import (
    _clear_non_target_ignored_flags,
    _get_pending_indexer_context,
    _mark_ignored_tree_nodes_completed,
)
from logic.pending.rules import _enforce_child_source_requirement
from logic.pending.selection import _selection_path_identity, _stamp_lazy_children_selection, stamp_upload_itype
from logic.pending.tree import _build_external_tree_item, _has_filepart_path, _load_external_metadata
from logic.pending.view import _strip_external_helper_fields


def build_external_children_snapshot(
    external_folder_name: str,
    parent_path: Path,
    parent_rel_path: str,
    *,
    folder_category_hint: str = "",
) -> Dict[str, Any]:
    started = time.perf_counter()
    (
        _active_indexers,
        active_ids,
        indexer_status_available,
        upload_map,
        completed_lookup,
        _db_error,
        _failed_map,
        filesize_by_indexer,
    ) = _get_pending_indexer_context()

    children: List[Dict[str, Any]] = []
    try:
        entries = sorted(parent_path.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        entries = []

    for child in entries:
        if child.name.startswith("."):
            continue
        child_rel = f"{parent_rel_path}/{child.name}" if parent_rel_path else child.name
        child_item, _child_size = _build_external_tree_item(
            external_folder_name,
            child,
            child_rel,
            upload_map,
            completed_lookup,
            active_ids if indexer_status_available else [],
            folder_category_hint=folder_category_hint,
            top_level=False,
            include_children=False,
            filesize_by_indexer=filesize_by_indexer,
        )
        child_item["has_filepart"] = child.name.endswith(".filepart") or _has_filepart_path(str(child))
        children.append(child_item)

    if children:
        # One resolution of the expanded folder gives this level its ignore
        # state (extras, samples, sidecars) without building deeper levels.
        try:
            resolution = resolve_explicit_path(
                parent_path,
                category_hint=folder_category_hint,
                anime_lookup=cached_lookup,
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.debug(f"Lazy child resolution failed for {parent_path}: {exc}")
            resolution = None
        if resolution is not None:
            _stamp_lazy_children_selection(children, resolution)
        for child_item in children:
            _clear_non_target_ignored_flags(child_item)
    # Episode files inside a pack named "Show.S01.WEB-DL" must carry their own
    # source token; the parent folder name does not count for individual upload.
    _enforce_child_source_requirement({"children": children})
    for child_item in children:
        _mark_ignored_tree_nodes_completed(child_item, active_ids if indexer_status_available else [])
        _strip_external_helper_fields(child_item)
        stamp_upload_itype(child_item)

    log_backend_timing(
        "scan_pending_children",
        started,
        context=f"path={parent_path} children={len(children)}",
        warn_threshold_s=0.5,
    )
    return {"children": children, "child_count": len(children)}

def _resolve_external_child_request(
    data: Dict[str, Any],
    key: str,
    path: str,
) -> Optional[tuple[str, Path, str, str]]:
    """Validate a lazy-child request against the configured snapshot roots."""
    target_key = str(key or "").strip()
    target_path_text = str(path or "").strip()
    if not target_key.startswith("ext:") or not target_path_text:
        return None

    key_parts = target_key.split(":", 2)
    if len(key_parts) != 3:
        return None
    _prefix, external_folder_name, parent_rel_path = key_parts

    items = data.get("items")
    groups = items.get("external") if isinstance(items, dict) else None
    if not isinstance(groups, list):
        return None

    target_path = Path(target_path_text)
    try:
        resolved_target = target_path.resolve(strict=True)
    except OSError:
        return None
    if not resolved_target.is_dir():
        return None

    for group in groups:
        if not isinstance(group, dict) or str(group.get("folder_name") or "") != external_folder_name:
            continue
        root_text = str(group.get("folder_path") or "").strip()
        if not root_text:
            continue
        try:
            root_path = Path(root_text).resolve(strict=True)
            actual_rel_path = resolved_target.relative_to(root_path).as_posix()
        except (OSError, ValueError):
            continue
        if actual_rel_path != parent_rel_path.replace("\\", "/").strip("/"):
            continue

        folder_category_hint = ""
        for top_item in group.get("items", []) or []:
            if not isinstance(top_item, dict):
                continue
            top_path_text = str(top_item.get("path") or "").strip()
            if not top_path_text:
                continue
            try:
                resolved_target.relative_to(Path(top_path_text).resolve(strict=False))
            except (OSError, ValueError):
                continue
            folder_category_hint = str(
                top_item.get("detected_category") or top_item.get("category") or ""
            ).strip().lower()
            break

        return external_folder_name, resolved_target, actual_rel_path, folder_category_hint
    return None

def build_external_children_for_request(data: Dict[str, Any], key: str, path: str) -> Dict[str, Any]:
    """Resolve, authorize, and build one lazy external-directory response."""
    resolved = _resolve_external_child_request(data, key, path)
    if resolved is None:
        return {"children": [], "child_count": 0}
    external_folder_name, parent_path, parent_rel_path, folder_category_hint = resolved
    compact_metadata = _load_external_metadata(data)
    if compact_metadata:
        children: List[Dict[str, Any]] = []
        try:
            entries = sorted(parent_path.iterdir(), key=lambda entry: entry.name.lower())
        except OSError:
            entries = []
        missing_metadata = False
        for entry in entries:
            if entry.name.startswith("."):
                continue
            cached = compact_metadata.get(_selection_path_identity(entry))
            if cached is None:
                missing_metadata = True
                break
            child = dict(cached)
            child["children"] = []
            child["files"] = []
            children.append(child)
        if not missing_metadata:
            return {"children": children, "child_count": len(children)}

    return build_external_children_snapshot(
        external_folder_name,
        parent_path,
        parent_rel_path,
        folder_category_hint=folder_category_hint,
    )
