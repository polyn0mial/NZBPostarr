"""The staging queue: items the user staged for a later queue start, and their start-time preparation."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from core.db import queue_items as db_queue_items
from core.logging import log_info
from core.media import VIDEO_EXTENSIONS, normalize_category
from logic.classify.tv_packs import has_season_pack_name, is_season_pack_folder
from logic.jobs import requests as job_requests
from logic.jobs.models import INVALID_CATEGORY_VALUES, QueueStartSummary, normalize_job_path_identity


class StagingQueue:
    """Staged items, mirrored to the database; guarded by the engine's lock."""

    def __init__(self, lock: threading.Lock, items: list[dict[str, Any]]) -> None:
        self._lock = lock
        self.items: list[dict[str, Any]] = list(items)

    def list_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.items)

    def add(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            added = db_queue_items.db_add_queue_items(items)
            if not added:
                return []

            original_by_identity: dict[str, dict[str, Any]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                identity = normalize_job_path_identity(item.get("path"))
                if identity:
                    original_by_identity[identity] = dict(item)

            merged_added: list[dict[str, Any]] = []
            for row in added:
                merged = dict(row)
                identity = normalize_job_path_identity(row.get("path"))
                extras = dict(original_by_identity.get(identity or "", {}))
                extras.pop("id", None)
                extras.pop("added_at", None)
                merged.update(extras)
                merged_added.append(merged)

            self.items.extend(merged_added)
        return merged_added

    def remove(self, item_id: int) -> bool:
        with self._lock:
            removed = db_queue_items.db_remove_queue_item(item_id)
            if removed:
                self.items = [queue_item for queue_item in self.items if queue_item["id"] != item_id]
        return removed

    def clear(self) -> int:
        with self._lock:
            count = db_queue_items.db_clear_queue()
            self.items.clear()
        return count

    def reorder(self, item_ids: list[int]) -> bool:
        with self._lock:
            id_map = {queue_item["id"]: queue_item for queue_item in self.items}
            if set(item_ids) != set(id_map.keys()):
                return False
            ok = db_queue_items.db_reorder_queue(item_ids)
            if ok:
                self.items = [id_map[item_id] for item_id in item_ids]
        return ok

    def remove_started(self, runnable_ids: set[int], job_id: str) -> None:
        """Drop the staged rows a queue start turned into job ``job_id``."""
        try:
            removed_count = db_queue_items.db_remove_queue_items(sorted(runnable_ids))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning(f"[QUEUE-START] Job {job_id} started but staged-item cleanup failed: {exc}")
            return
        if removed_count != len(runnable_ids):
            logger.warning(
                f"[QUEUE-START] Expected to remove {len(runnable_ids)} staged item(s) after job creation, "
                f"but removed {removed_count}"
            )
        with self._lock:
            self.items = [
                queue_item for queue_item in self.items if _queue_item_id(queue_item.get("id")) not in runnable_ids
            ]


def _queue_item_id(value: Any) -> Optional[int]:
    text = str(value or "").strip()
    return int(text) if re.fullmatch(r"\d+", text) else None


def queue_item_label(item: dict[str, Any], fallback_index: int) -> str:
    name = str(item.get("name") or "").strip()
    path_text = str(item.get("path") or "").strip()
    if name:
        return name
    if path_text:
        return Path(path_text).name or path_text
    return f"item #{fallback_index}"


def derive_queue_item_category(item: dict[str, Any]) -> tuple[str, str]:
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
            if resolved_category and resolved_category not in INVALID_CATEGORY_VALUES:
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
        if candidate and candidate not in INVALID_CATEGORY_VALUES:
            return candidate, source

    return "", ""


def prepare_start_items(items: list[dict[str, Any]]) -> QueueStartSummary:
    """Validate staged items for a queue start: runnable rows with a category, and skipped rows with a reason."""
    from logic.classify.walk import begin_scan_cache, end_scan_cache

    # Many staged items in the same root share filesystem walks; reuse a
    # per-call cache so resolve_explicit_path doesn't re-walk every item.
    cache_token = begin_scan_cache()
    try:
        return _prepare_start_items(items)
    finally:
        end_scan_cache(cache_token)


def _prepare_start_items(items: list[dict[str, Any]]) -> QueueStartSummary:
    runnable_items: list[dict[str, Any]] = []
    skipped_items: list[tuple[dict[str, Any], str]] = []
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

        category, source = derive_queue_item_category(queue_item)
        if not category or category in INVALID_CATEGORY_VALUES:
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
            f"Queue start prepared '{queue_item_label(queue_item, index)}' as {category} via {source or 'unknown'}"
        )
        runnable_items.append(queue_item)

    runnable_items = collapse_overlapping_start_items(runnable_items)

    return QueueStartSummary(
        runnable_items=tuple(inject_inferred_tv_pack_items(runnable_items)),
        skipped_items=tuple(skipped_items),
    )


def raise_no_runnable(prepared: QueueStartSummary) -> None:
    skipped_reasons = [
        f"{queue_item_label(item, idx)} ({reason})" for idx, (item, reason) in enumerate(prepared.skipped_items, start=1)
    ]
    detail = "Queue start failed: no runnable staged items"
    if skipped_reasons:
        detail = f"{detail}. " + "; ".join(skipped_reasons[:5])
        if len(skipped_reasons) > 5:
            detail = f"{detail}; and {len(skipped_reasons) - 5} more"
    log_info(f"[QUEUE-START] {detail}", "ERROR")
    raise ValueError(detail)


def _item_path_text(item: dict[str, Any]) -> str:
    return str(item.get("path") or "").strip()


def _overlap_path(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        normalized = Path(text).resolve().as_posix()
    except OSError:
        normalized = Path(text).as_posix()
    normalized = normalized.rstrip("/")
    return normalized.casefold() if os.name == "nt" else normalized


def _should_preserve_start_dir(item: dict[str, Any], category: str, raw_path: Path) -> bool:
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


def collapse_overlapping_start_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep a staged TV/anime pack folder and drop its separately staged descendants."""
    grouped: dict[str, list[tuple[int, dict[str, Any], str]]] = {}
    for idx, item in enumerate(items):
        category = str(item.get("category") or "").strip().lower()
        normalized_path = _overlap_path(item.get("path"))
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
            ) and _should_preserve_start_dir(item, category, raw_path):
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
            logger.info(f"[QUEUE-START] Category '{category}' collapsed {dropped_here} selected parent pack overlap(s)")

    return [item for idx, item in enumerate(items) if idx not in dropped_indices]


def inject_inferred_tv_pack_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert season-folder pack rows when staging contains only child episode files.

    Selected pack folders are expanded once, at job creation
    (requests.expand_explicit_pack_request_paths), so no child episodes are added here.
    """
    existing_dirs = {
        os.path.normpath(_item_path_text(item))
        for item in items
        if str(item.get("category") or "").strip().lower() == "tv"
        and _item_path_text(item)
        and Path(_item_path_text(item)).is_dir()
    }
    episodes_by_parent: dict[Path, list[Path]] = {}
    for item in items:
        if str(item.get("category") or "").strip().lower() != "tv":
            continue
        path_text = _item_path_text(item)
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
        path = Path(_item_path_text(item))
        parent = path.parent if path.is_file() else None
        if parent in pack_parents and parent not in inserted:
            injected.append(job_requests.inferred_pack_row(parent))
            inserted.add(parent)
        injected.append(item)

    logger.info(f"[QUEUE-START] inferred {len(inserted)} TV season pack row(s) from staged episodes")
    return injected
