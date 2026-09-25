"""Upload history reads: recent, grouped and per-job uploads.

Each read turns any failure, not only SQLAlchemyError, into DatabaseOperationalError: the API renders
its degraded state from that one type.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import selectinload

from core.db import engine as db_engine
from core.db.engine import DatabaseOperationalError
from core.db.models import JobHistory, Upload, UploadResult
from core.db.timefmt import _isoformat_utc


def get_recent_uploads(
    limit: int = 20,
    offset: int = 0,
    search: Optional[str] = None,
    itype: Optional[str] = None,
    destination: Optional[str] = "all",
    sort_by: str = "when",
    order: str = "desc",
    literal: bool = False,
    **_kwargs: Any,
) -> Dict[str, Any]:
    """Get list of most recent individual item uploads with pagination and search."""
    try:
        with db_engine.session_scope() as session:
            # Eager load results using selectinload (more efficient for collections)
            stmt = select(Upload).options(selectinload(Upload.results))
            total_stmt = select(func.count(Upload.id))

            if search:
                search_filter: Any = None
                if literal:
                    like_pattern = f"%{search}%"
                    search_filter = Upload.item_name.like(like_pattern)
                else:
                    # Smart: treat separators as wildcards or spaces
                    q_clean = search
                    for c in "._-[]()":
                        q_clean = q_clean.replace(c, " ")
                    words = [w for w in q_clean.split() if w]
                    like_pattern = f"%{'%'.join(words)}%" if words else "%%"

                    search_filter = or_(
                        Upload.item_name.like(like_pattern),
                        Upload.parsed_title.like(like_pattern),
                    )
                stmt = stmt.where(search_filter)
                total_stmt = total_stmt.where(search_filter)

            if itype:
                stmt = stmt.where(Upload.itype == itype)
                total_stmt = total_stmt.where(Upload.itype == itype)

            if destination and destination not in ("all", "incomplete"):
                # Use ANY for filtering by result destination to avoid duplicating rows
                stmt = stmt.where(Upload.results.any(UploadResult.indexer_id == destination))
                total_stmt = total_stmt.where(Upload.results.any(UploadResult.indexer_id == destination))

            # Count total for pagination
            total = session.execute(total_stmt).scalar() or 0

            # Sorting
            sort_attr: Any = Upload.updated_at
            if sort_by == "name":
                sort_attr = Upload.item_name
            elif sort_by == "size":
                sort_attr = Upload.filesize

            if order == "desc":
                stmt = stmt.order_by(desc(sort_attr))
            else:
                stmt = stmt.order_by(sort_attr)

            # execute and fetch
            uploads = session.execute(stmt.limit(limit).offset(offset)).scalars().all()

            return {
                "items": [_serialize_upload(u) for u in uploads],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
    except Exception as e:
        logger.error(f"Recent uploads fetch failed: {e}")
        raise DatabaseOperationalError("Recent uploads fetch failed") from e

def _build_grouped_upload_filters(
    search: Optional[str],
    literal: bool,
    destination: Optional[str],
) -> List[Any]:
    """Build the WHERE-clause filters for get_grouped_uploads.

    Extracted from get_grouped_uploads to keep its own branching down.
    """
    filters: List[Any] = []
    if search:
        if literal:
            like_pattern = f"%{search}%"
            filters.append(Upload.item_name.like(like_pattern))
        else:
            # Smart matching logic
            q_clean = search
            for c in "._-[]()":
                q_clean = q_clean.replace(c, " ")
            words = [w for w in q_clean.split() if w]
            like_pattern = f"%{'%'.join(words)}%" if words else "%%"

            filters.append(
                or_(
                    Upload.item_name.like(like_pattern),
                    Upload.parsed_title.like(like_pattern),
                )
            )
    if destination and destination not in ("all", "incomplete"):
        filters.append(
            Upload.results.any(
                and_(
                    UploadResult.indexer_id == destination,
                    UploadResult.status == "success",
                )
            )
        )
    return filters

def get_grouped_uploads(
    page: int = 1,
    per_page: int = 50,
    search: Optional[str] = None,
    destination: Optional[str] = "all",
    sort_by: str = "when",
    order: str = "desc",
    literal: bool = False,
    summary_only: bool = False,
) -> Dict[str, Any]:
    """Return uploads grouped by parsed_title, paginated by group count.

    Uses indexed parsed_title column for efficient SQL GROUP BY rather than
    loading every row into Python.
    """
    # pylint: disable=assignment-from-no-return
    try:
        with db_engine.session_scope() as session:
            # ── WHERE filters ──
            filters: List[Any] = _build_grouped_upload_filters(search, literal, destination)

            title_key = func.lower(func.coalesce(Upload.parsed_title, Upload.item_name))

            # ── Count total distinct groups ──
            count_q = select(title_key).group_by(title_key)
            if filters:
                count_q = count_q.where(*filters)
            total_groups = session.execute(select(func.count()).select_from(count_q.subquery())).scalar() or 0

            if total_groups == 0:
                return {
                    "groups": [],
                    "total_groups": 0,
                    "page": page,
                    "per_page": per_page,
                }

            # ── Paginated group titles ──
            latest_expr = func.max(func.coalesce(Upload.updated_at, Upload.created_at))
            size_expr = func.sum(func.coalesce(Upload.filesize, 0))
            count_expr = func.count(Upload.id)
            display_title = func.min(func.coalesce(Upload.parsed_title, Upload.item_name))
            media_type_expr = func.min(func.coalesce(Upload.media_type, "other"))

            group_q = select(
                title_key.label("tk"),
                display_title.label("display"),
                latest_expr.label("latest"),
                size_expr.label("total_size"),
                count_expr.label("item_count"),
                media_type_expr.label("media_type"),
            ).group_by(title_key)
            if filters:
                group_q = group_q.where(*filters)

            if sort_by == "name":
                sort_expr = title_key
            elif sort_by == "size":
                sort_expr = size_expr
            else:
                sort_expr = latest_expr

            group_q = group_q.order_by(desc(sort_expr) if order == "desc" else sort_expr)
            group_q = group_q.limit(per_page).offset((page - 1) * per_page)

            page_rows = session.execute(group_q).fetchall()
            title_keys = [r.tk for r in page_rows]
            display_map = {r.tk: r.display for r in page_rows}

            if summary_only:
                return {
                    "groups": [
                        {
                            "title_key": r.tk,
                            "show_name": r.display or r.tk,
                            "media_type": r.media_type or "other",
                            "item_count": int(r.item_count or 0),
                            "total_size": int(r.total_size or 0),
                            "latest_date": _isoformat_utc(r.latest),
                            "summary_only": True,
                        }
                        for r in page_rows
                    ],
                    "total_groups": total_groups,
                    "page": page,
                    "per_page": per_page,
                }

            if not title_keys:
                return {
                    "groups": [],
                    "total_groups": total_groups,
                    "page": page,
                    "per_page": per_page,
                }

            # ── Fetch items for the page's groups ──
            items_q = (
                select(Upload)
                .options(selectinload(Upload.results))
                .where(title_key.in_(title_keys))
                .order_by(Upload.season_number, Upload.episode_number, Upload.item_name)
            )
            all_items = session.execute(items_q).scalars().all()

            bucket: Dict[str, list] = {k: [] for k in title_keys}
            for u in all_items:
                key = (u.parsed_title or u.item_name or "").lower()
                if key in bucket:
                    bucket[key].append(_serialize_upload(u))

            result_groups = []
            for tk in title_keys:
                items = bucket.get(tk, [])
                if items:
                    # Determine media_type by majority vote across all items in
                    # the group to avoid a single mis-parsed item skewing the
                    # classification.
                    type_counts: Dict[str, int] = {}
                    for it in items:
                        mt = it.get("media_type", "other")
                        type_counts[mt] = type_counts.get(mt, 0) + 1
                    dominant_type = max(type_counts, key=type_counts.__getitem__)

                    result_groups.append(
                        {
                            "show_name": display_map.get(tk, tk),
                            "title_key": tk,
                            "media_type": dominant_type,
                            "items": items,
                        }
                    )

            return {
                "groups": result_groups,
                "total_groups": total_groups,
                "page": page,
                "per_page": per_page,
            }

    except Exception as e:
        logger.error(f"Grouped uploads fetch failed: {e}")
        raise DatabaseOperationalError("Grouped uploads fetch failed") from e

def get_group_upload_items(
    title_key_value: str,
    destination: Optional[str] = "all",
) -> Dict[str, Any]:
    """Return full upload rows for one grouped-history title key."""
    try:
        with db_engine.session_scope() as session:
            title_key = func.lower(func.coalesce(Upload.parsed_title, Upload.item_name))
            stmt = (
                select(Upload)
                .options(selectinload(Upload.results))
                .where(title_key == str(title_key_value or "").lower())
                .order_by(Upload.season_number, Upload.episode_number, Upload.item_name)
            )
            if destination and destination not in ("all", "incomplete"):
                stmt = stmt.where(
                    Upload.results.any(
                        and_(
                            UploadResult.indexer_id == destination,
                            UploadResult.status == "success",
                        )
                    )
                )

            uploads = session.execute(stmt).scalars().all()
            return {
                "title_key": title_key_value,
                "items": [_serialize_upload(u) for u in uploads],
            }
    except Exception as e:
        logger.error(f"Grouped upload item fetch failed for {title_key_value!r}: {e}")
        raise DatabaseOperationalError(f"Grouped upload item fetch failed for {title_key_value!r}") from e

def get_uploads_for_job(job_id: str) -> List[Dict[str, Any]]:
    """Fetch individual items updated within a job's timeframe."""
    try:
        with db_engine.session_scope() as session:
            job = session.execute(select(JobHistory).filter_by(job_id=job_id)).scalar_one_or_none()
            if not job:
                return []

            end = job.completed_at or datetime.now(timezone.utc)
            stmt = (
                select(Upload)
                .options(selectinload(Upload.results))
                .where(
                    Upload.updated_at >= job.started_at - timedelta(seconds=5),
                    Upload.updated_at <= end + timedelta(seconds=5),
                )
                .order_by(desc(Upload.updated_at))
            )

            uploads = session.execute(stmt).scalars().all()
            return [_serialize_upload(u) for u in uploads]
    except Exception as e:
        logger.error(f"Job uploads fetch failed: {e}")
        raise DatabaseOperationalError("Job uploads fetch failed") from e

def _serialize_upload(upload: Upload) -> Dict[str, Any]:
    """Convert an Upload ORM model into a flat dictionary for the API."""
    from core.indexers.registry import get_registry

    registry = get_registry()
    _ua = upload.updated_at or upload.created_at
    _ca = upload.created_at
    data = {
        "item_name": upload.item_name,
        "itype": upload.itype,
        "filesize": upload.filesize,
        "updated_at": _isoformat_utc(_ua),
        "created_at": _isoformat_utc(_ca),
        "parsed_title": upload.parsed_title,
        "media_type": upload.media_type,
        "season_number": upload.season_number,
        "episode_number": upload.episode_number,
        "episode_end_number": getattr(upload, "episode_end_number", None),
    }

    # Enhanced destinations list for the jobs modal and other UI components
    destinations = []

    for res in upload.results:
        # Structured format for UI components
        idx_def = registry.get(res.indexer_id)
        destinations.append(
            {
                "id": res.indexer_id,
                "name": idx_def.name if idx_def else res.indexer_id.upper(),
                "color": idx_def.color if idx_def else "gray",
                "uploaded_at": _isoformat_utc(res.uploaded_at),
                "duration": res.duration,
                "speed": res.speed_bps,
                "server_name": res.server_name,
                "status": res.status,
                "error": res.error,
            }
        )

    data["destinations"] = destinations
    return data
