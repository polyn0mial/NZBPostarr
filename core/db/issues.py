"""Upload error issues: fingerprinted groups and mute state."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from loguru import logger
from sqlalchemy import select

from core.db import engine as db_engine
from core.db.engine import DatabaseOperationalError
from core.db.models import MutedIssue, Upload, UploadResult


# WHY: with 6+ indexers each free to fail in their own way (auth, category mapping,
# rate limits, transient network errors), the raw upload_results table is a flat
# per-attempt log. Finding a recurring pattern means scrolling history by eye. This
# adapts PostHog's error_tracking idea (products/error_tracking/, MIT, PostHog Inc.):
# collapse the dynamic parts of an error message (paths, numbers, quoted values) into
# a stable signature, then group failures by (indexer, signature) into a "known
# issues" list, counted, with first/last-seen timestamps. Adapted for NZBPostarr's
# plain-text UploadResult.error column - no vendored code, written fresh for this
# schema.
_ERROR_SIGNATURE_PATTERNS: Tuple[Tuple["re.Pattern[str]", str], ...] = (
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"(?:[A-Za-z]:\\|/)[^\s\"']+"), "<path>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<value>"),
    (re.compile(r"\b\d+\b"), "<n>"),
)

def _fingerprint_upload_error(message: str) -> str:
    """Collapse a raw indexer error message into a stable grouping signature.

    Two failures with the same underlying cause rarely share literal text (they
    carry a different filename, timestamp, or byte count) but do share shape, so
    normalizing the variable parts is what makes grouping possible at all.
    """
    text = (message or "").strip()
    if not text:
        return "<empty>"
    for pattern, placeholder in _ERROR_SIGNATURE_PATTERNS:
        text = pattern.sub(placeholder, text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text[:200] if text else "<empty>"

def get_grouped_upload_errors(
    indexer_id: Optional[str] = None,
    limit: int = 50,
    since_days: Optional[int] = None,
    include_muted: bool = True,
) -> Dict[str, Any]:
    """Group failed indexer submissions into a "known issues" view.

    One row per (indexer, error signature) with an occurrence count and
    first/last-seen timestamps, sorted by most recently seen. Mirrors PostHog's
    error_tracking model of grouping exception occurrences into issues rather
    than presenting a raw event stream. Each issue carries a `muted` flag
    (from MutedIssue); pass `include_muted=False` to drop muted issues from
    the result entirely instead of just flagging them.
    """
    try:
        with db_engine.session_scope() as session:
            stmt = (
                select(
                    UploadResult.indexer_id,
                    UploadResult.error,
                    UploadResult.uploaded_at,
                    Upload.item_name,
                )
                .join(Upload, Upload.id == UploadResult.upload_id)
                .where(UploadResult.status == "failed", UploadResult.error.isnot(None))
            )
            if indexer_id and indexer_id != "all":
                stmt = stmt.where(UploadResult.indexer_id == indexer_id)
            if since_days is not None:
                cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
                stmt = stmt.where(UploadResult.uploaded_at >= cutoff)

            rows = session.execute(stmt).all()

            groups: Dict[Tuple[str, str], Dict[str, Any]] = {}
            for row in rows:
                signature = _fingerprint_upload_error(row.error)
                key = (row.indexer_id, signature)
                group = groups.get(key)
                if group is None:
                    group = {
                        "indexer_id": row.indexer_id,
                        "signature": signature,
                        "sample_error": row.error,
                        "count": 0,
                        "first_seen": row.uploaded_at,
                        "last_seen": row.uploaded_at,
                        "affected_items": set(),
                    }
                    groups[key] = group
                group["count"] += 1
                if row.uploaded_at and (not group["first_seen"] or row.uploaded_at < group["first_seen"]):
                    group["first_seen"] = row.uploaded_at
                if row.uploaded_at and (not group["last_seen"] or row.uploaded_at > group["last_seen"]):
                    group["last_seen"] = row.uploaded_at
                group["affected_items"].add(row.item_name)

            muted_keys = {(row.indexer_id, row.signature) for row in session.execute(select(MutedIssue.indexer_id, MutedIssue.signature)).all()}

            ordered = sorted(
                groups.values(),
                key=lambda g: g["last_seen"] or datetime.min,
                reverse=True,
            )
            issues = []
            for issue in ordered:
                is_muted = (issue["indexer_id"], issue["signature"]) in muted_keys
                if is_muted and not include_muted:
                    continue
                affected = sorted(issue["affected_items"])
                issues.append(
                    {
                        "indexer_id": issue["indexer_id"],
                        "signature": issue["signature"],
                        "sample_error": issue["sample_error"],
                        "count": issue["count"],
                        "affected_item_count": len(affected),
                        "affected_items": affected[:5],
                        "first_seen": issue["first_seen"].isoformat() if issue["first_seen"] else None,
                        "last_seen": issue["last_seen"].isoformat() if issue["last_seen"] else None,
                        "muted": is_muted,
                    }
                )
                if len(issues) >= max(0, limit):
                    break

            return {"issues": issues, "total_issues": len(groups)}
    except Exception as e:
        logger.error(f"Grouped upload error fetch failed: {e}")
        raise DatabaseOperationalError("Grouped upload error fetch failed") from e

def mute_upload_issue(indexer_id: str, signature: str) -> bool:
    """Silence a known-issue group so it stops standing out in the default view.

    Idempotent: muting an already-muted (indexer_id, signature) pair is a no-op.
    """
    try:
        with db_engine.session_scope() as session:
            existing = session.execute(
                select(MutedIssue).filter_by(indexer_id=indexer_id, signature=signature)
            ).scalar_one_or_none()
            if existing is None:
                session.add(MutedIssue(indexer_id=indexer_id, signature=signature))
            return True
    except Exception as e:
        logger.error(f"Failed to mute issue ({indexer_id}, {signature}): {e}")
        raise DatabaseOperationalError("Failed to mute issue") from e

def unmute_upload_issue(indexer_id: str, signature: str) -> bool:
    """Restore a previously muted known-issue group to the default view.

    Idempotent: unmuting an already-unmuted pair is a no-op.
    """
    try:
        with db_engine.session_scope() as session:
            existing = session.execute(
                select(MutedIssue).filter_by(indexer_id=indexer_id, signature=signature)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)
            return True
    except Exception as e:
        logger.error(f"Failed to unmute issue ({indexer_id}, {signature}): {e}")
        raise DatabaseOperationalError("Failed to unmute issue") from e
