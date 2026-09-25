"""Upload history rows: folder hierarchy rows recorded after a successful post."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.db.uploads import pin_folder_ts_to_children, record_nntp_success, update_db_destination
from core.fs import compute_size_uncached
from core.paths import path_key
from logic.jobs.context import get_thread_job
from logic.jobs.metrics import request_live_queue_refresh


def _folder_log_itype(category: str) -> str:
    normalized = category.lower()
    if normalized == "tv":
        return "TV Folder"
    if normalized == "movies":
        return "Movie Folder"
    if normalized == "anime":
        return "Anime Folder"
    return "Folder"


def _iter_folder_ancestor_entries(path: Path, base_folder: Optional[Path]) -> list[tuple[str, Path]]:
    if base_folder is None:
        return []
    try:
        rel_path = path.relative_to(base_folder)
    except ValueError:
        return []

    rel_parent = rel_path.parent
    if str(rel_parent) in {"", "."}:
        return []

    entries: list[tuple[str, Path]] = []
    parts = rel_parent.parts
    for index in range(1, len(parts) + 1):
        rel_key = "/".join(parts[:index])
        folder_path = base_folder.joinpath(*parts[:index])
        entries.append((rel_key, folder_path))
    return entries


def _live_size_bytes(path: Path) -> int:
    """Use an uncached size for upload-time guards; pending scans may cache stale growth."""
    return compute_size_uncached(path)


def _folder_size_cached(folder_path: Path) -> int:
    cache_key = path_key(folder_path)
    job = get_thread_job()
    if job is not None:
        cache = job.setdefault("_folder_size_cache", {})
        if cache_key in cache:
            return int(cache[cache_key])
    # This cache is scoped to the active job.  The former process-wide LRU could
    # return stale sizes for mutable folders and retained path objects forever.
    size = compute_size_uncached(folder_path)
    if job is not None:
        cache = job.setdefault("_folder_size_cache", {})
        cache[cache_key] = int(size)
    return int(size)


def _record_folder_hierarchy_rows(
    path: Path,
    *,
    base_folder: Optional[Path],
    category: str,
    dest_id: Optional[str] = None,
    upload_result: Optional[dict[str, Any]] = None,
) -> None:
    folder_itype = _folder_log_itype(category)
    for folder_key, folder_path in _iter_folder_ancestor_entries(path, base_folder):
        folder_size = _folder_size_cached(folder_path)
        if folder_size <= 0:
            continue
        # Folder rows never jump to "now" on each child upload; they are pinned
        # just above their newest child instead.
        record_nntp_success(folder_key, folder_size, folder_itype, bump_timestamp=False)
        if dest_id and upload_result is not None:
            update_db_destination(
                dest_id,
                folder_path.name,
                folder_size,
                folder_key,
                itype=folder_itype,
                _bump_timestamp=False,
                **upload_result,
            )
        pin_folder_ts_to_children(folder_key)


def refresh_queue_after_destination_write() -> None:
    """Refresh the live queue view and statistics after an upload wrote a destination row.

    The DB layer no longer triggers this refresh when it records a destination; the pipeline does.
    """
    request_live_queue_refresh(reason="upload-success")


def refresh_pending_after_upload() -> None:
    """Drop the pending view's cached indexer ticks so a new upload shows at once, not after the cache TTL.

    The DB layer no longer triggers this refresh when it records a destination; the pipeline does.
    """
    from logic.pending.completion import invalidate_pending_indexer_context

    invalidate_pending_indexer_context()
