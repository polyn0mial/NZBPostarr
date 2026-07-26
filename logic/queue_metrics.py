"""Helpers for pending-task counting and live queue refreshes."""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, Optional, Set

from loguru import logger

_QUEUE_UPDATE_LOCK = threading.Lock()
_LAST_QUEUE_UPDATE_SIG: Optional[tuple[int, int]] = None


def required_pending_indexer_ids(indexers: Iterable[Any]) -> list[str]:
    """Return the indexers that should count toward pending work."""
    active_ids: list[str] = []
    backfill_ids: list[str] = []

    for indexer in indexers or []:
        if isinstance(indexer, dict):
            idx_id = str(indexer.get("id") or "").strip()
            backfill = bool(indexer.get("backfill"))
        else:
            idx_id = str(getattr(indexer, "id", "") or "").strip()
            backfill = bool(getattr(indexer, "backfill", False))

        if not idx_id:
            continue
        active_ids.append(idx_id)
        if backfill:
            backfill_ids.append(idx_id)

    return backfill_ids or active_ids


def incomplete_pending_indexer_ids(
    indexer_status: Optional[Dict[str, Any]], required_indexer_ids: Iterable[str]
) -> Set[str]:
    """Return the required indexers still pending for one item."""
    status_map = indexer_status if isinstance(indexer_status, dict) else {}
    return {idx_id for idx_id in required_indexer_ids if status_map.get(idx_id) is not True}


def count_pending_indexer_slots(indexer_status: Optional[Dict[str, Any]], required_indexer_ids: Iterable[str]) -> int:
    """Count red/incomplete indexer slots for one pending item."""
    return len(incomplete_pending_indexer_ids(indexer_status, required_indexer_ids))


def log_queue_update(*, red_indexers: int, total_tasks: int) -> None:
    """Emit a deduplicated queue-update log line."""
    global _LAST_QUEUE_UPDATE_SIG

    signature = (int(red_indexers), int(total_tasks))
    with _QUEUE_UPDATE_LOCK:
        if _LAST_QUEUE_UPDATE_SIG == signature:
            return
        _LAST_QUEUE_UPDATE_SIG = signature

    logger.info(f"[Queue Update] Found {signature[0]} Red Indexers. Total pending tasks set to {signature[1]}.")


def request_live_queue_refresh(reason: str = "manual") -> None:
    """Refresh pending snapshot and live statistics after DB-backed status changes."""
    try:
        from logic.pending_index import get_pending_index_manager

        get_pending_index_manager().request_refresh(reason=f"db-{reason}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug(f"Pending queue refresh request failed ({reason}): {exc}")

    try:
        from logic.services import get_upload_service

        get_upload_service().invalidate_statistics_cache()
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.debug(f"Live statistics invalidation failed ({reason}): {exc}")
