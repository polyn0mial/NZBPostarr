"""Finished-job history rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast, Dict, List

from loguru import logger
from sqlalchemy import delete, desc, select

from core.db.engine import DatabaseOperationalError, session_scope
from core.db.models import JobHistory
from core.db.timefmt import _isoformat_utc


def save_job_history(job_id: str, **kwargs: Any) -> None:
    """Create or update a job history record."""
    try:
        with session_scope() as session:
            job = session.execute(select(JobHistory).filter_by(job_id=job_id)).scalar_one_or_none()
            if not job:
                # Ensure category is present for new record
                category = kwargs.get("category", "misc")
                job = JobHistory(
                    job_id=job_id,
                    started_at=datetime.now(timezone.utc),
                    status="running",
                    category=category,
                )
                session.add(job)

            for k, v in kwargs.items():
                if k in ["started_at", "completed_at"] and isinstance(v, str):
                    try:
                        # Clean up common ISO formats for SQLite compatibility
                        clean_v = v.replace("Z", "+00:00")
                        v = datetime.fromisoformat(clean_v)
                    except ValueError:
                        logger.warning(f"Failed to parse datetime '{v}' for {k}")
                        continue  # Keep existing or default instead of setting invalid string

                if k == "error_message" and v is not None:
                    v = str(v)[:500]  # Truncate long error messages

                if hasattr(job, k):
                    setattr(job, k, v)
    except Exception as e:
        logger.error(f"Failed to save job history for {job_id}: {e}")

def get_job_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Retrieve historical upload jobs."""
    try:
        with session_scope() as session:
            jobs = session.query(JobHistory).order_by(desc(JobHistory.started_at)).limit(limit).all()
            serialized: List[Dict[str, Any]] = []
            for job in jobs:
                row = {c.name: getattr(job, c.name) for c in JobHistory.__table__.columns}
                row["started_at"] = _isoformat_utc(job.started_at)
                row["completed_at"] = _isoformat_utc(job.completed_at)
                row["created_at"] = _isoformat_utc(job.created_at)
                serialized.append(row)
            return serialized
    except Exception as e:
        logger.error(f"Job history fetch failed: {e}")
        raise DatabaseOperationalError("Job history fetch failed") from e

def delete_job_history(job_ids: List[str]) -> int:
    """Delete one or more job history records by job_id."""
    if not job_ids:
        return 0
    try:
        with session_scope() as session:
            stmt = delete(JobHistory).where(JobHistory.job_id.in_(job_ids))
            result = session.execute(stmt)
            return cast(Any, result).rowcount
    except Exception as e:
        logger.error(f"Failed to delete job history: {e}")
        raise DatabaseOperationalError("Failed to delete job history") from e
