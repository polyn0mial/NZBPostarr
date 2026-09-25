"""The persisted upload queue rows."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Set

from loguru import logger
from sqlalchemy import delete, func, select

from core.db.engine import _retry_on_lock, session_scope
from core.db.models import QueueItem
from core.paths import path_key, resolve_path


def _normalize_queue_item_path(path: Any) -> str:
    return str(resolve_path(path)) if str(path or "").strip() else ""

@_retry_on_lock()
def db_load_queue() -> List[Dict[str, Any]]:
    """Return all queued items ordered by position then id."""
    with session_scope() as session:
        items = session.execute(select(QueueItem).order_by(QueueItem.position, QueueItem.id)).scalars().all()
        valid: List[QueueItem] = []
        seen_identities: Set[str] = set()
        stale = 0
        duplicate = 0

        for qi in items:
            normalized_path = _normalize_queue_item_path(qi.path)
            identity = path_key(normalized_path)
            if not normalized_path or not Path(normalized_path).exists():
                session.delete(qi)
                stale += 1
                continue
            if not identity or identity in seen_identities:
                session.delete(qi)
                duplicate += 1
                continue
            seen_identities.add(identity)
            if qi.path != normalized_path:
                qi.path = normalized_path
            valid.append(qi)

        if stale:
            logger.info(f"Queue restore: dropped {stale} stale item(s) (path no longer exists)")
        if duplicate:
            logger.info(f"Queue restore: dropped {duplicate} duplicate item(s) with the same path identity")
        if valid:
            logger.info(f"Queue restore: recovered {len(valid)} item(s) from previous session")
        return [
            {
                "id": qi.id,
                "path": qi.path,
                "category": qi.category,
                "itype": qi.itype,
                "name": qi.name,
                "added_at": qi.added_at.isoformat() if qi.added_at else None,
            }
            for qi in valid
        ]

@_retry_on_lock()
def db_add_queue_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add items to the queue; skip duplicate paths. Returns added items as dicts."""
    added: List[Dict[str, Any]] = []
    with session_scope() as session:
        existing_paths: Set[str] = {
            path_key(row[0]) for row in session.execute(select(QueueItem.path)).all()
        }
        max_pos = session.execute(select(func.max(QueueItem.position))).scalar() or 0
        position = max_pos + 1
        for item in items:
            path = _normalize_queue_item_path(item.get("path", ""))
            path_identity = path_key(path)
            if not path or not path_identity or path_identity in existing_paths:
                continue
            qi = QueueItem(
                path=path,
                category=item.get("category", "misc"),
                itype=item.get("itype", "") or "",
                name=item.get("name", "") or Path(path).name,
                position=position,
            )
            session.add(qi)
            session.flush()  # populate qi.id
            existing_paths.add(path_identity)
            added.append(
                {
                    "id": qi.id,
                    "path": qi.path,
                    "category": qi.category,
                    "itype": qi.itype,
                    "name": qi.name,
                    "added_at": qi.added_at.isoformat() if qi.added_at else None,
                }
            )
            position += 1
    return added

@_retry_on_lock()
def db_remove_queue_item(item_id: int) -> bool:
    """Remove a single item from the queue by id."""
    with session_scope() as session:
        qi = session.get(QueueItem, item_id)
        if qi is None:
            return False
        session.delete(qi)
    return True

@_retry_on_lock()
def db_remove_queue_items(item_ids: List[int]) -> int:
    """Remove multiple queued items by id and return the deleted count."""
    normalized_ids = [int(item_id) for item_id in item_ids if item_id is not None]
    if not normalized_ids:
        return 0

    with session_scope() as session:
        count = (
            session.execute(
                select(func.count()).select_from(QueueItem).where(QueueItem.id.in_(normalized_ids))
            ).scalar()
            or 0
        )
        if count:
            session.execute(delete(QueueItem).where(QueueItem.id.in_(normalized_ids)))
    return int(count)

@_retry_on_lock()
def db_clear_queue() -> int:
    """Remove all items from the queue. Returns count removed."""
    with session_scope() as session:
        count = session.execute(select(func.count()).select_from(QueueItem)).scalar() or 0
        session.execute(delete(QueueItem))
    return int(count)

@_retry_on_lock()
def db_reorder_queue(item_ids: List[int]) -> bool:
    """Update position of each item per the provided ordered list of IDs."""
    with session_scope() as session:
        rows = session.execute(select(QueueItem)).scalars().all()
        if len(rows) != len(item_ids):
            return False
        id_to_obj = {qi.id: qi for qi in rows}
        if set(item_ids) != set(id_to_obj.keys()):
            return False
        for pos, iid in enumerate(item_ids):
            id_to_obj[iid].position = pos
    return True
