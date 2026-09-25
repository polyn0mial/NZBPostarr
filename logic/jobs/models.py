"""Job schema: the job state shape, normalized job requests, and job field normalizers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

_FINISHED_JOB_RETENTION_COUNT = 100
_FINISHED_JOB_RETENTION_DAYS = 30
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
INVALID_CATEGORY_VALUES = frozenset({"", "external", "null", "none", "unknown", "undefined"})


class JobState(BaseModel):
    """Canonical job state shape for in-memory tracking and API serialization."""

    model_config = ConfigDict(extra="allow")

    job_id: str
    category: str
    status: str
    started_at: str
    created_at: Optional[str] = None

    items_processed: int = 0
    items_total: int = 0
    items_skipped: int = 0
    total_bytes: int = 0

    progress: str = "Starting..."
    progress_percent: int = 0

    current_item: Optional[str] = None
    item_percent: int = 0
    speed: Optional[str] = None
    eta: Optional[str] = None
    current_stage: Optional[str] = "INITIALIZING"
    test_mode: bool = False
    display_name: Optional[str] = None
    run_after: Optional[str] = None
    priority: int = 0
    attempt_count: int = 0
    retry_of: Optional[str] = None
    retried_as: Optional[str] = None
    retry_eligible: bool = False
    last_error: Optional[str] = None
    events: list[dict[str, str]] = Field(default_factory=list)


@dataclass(frozen=True)
class ProcessingJobRequest:
    """Normalized execution request for a processing job."""

    category: str
    limit: Optional[int] = None
    skip_packs: bool = False
    skip_episodes: bool = False
    test_mode: bool = False
    target_indexer_id: Optional[str] = None
    target_indexer_ids: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    item_hints: tuple[dict[str, Any], ...] = ()
    enable_duplicate_check: Optional[bool] = None
    force: Optional[bool] = None
    skip_pack_expansion: bool = False


@dataclass(frozen=True)
class StreamJobRequest:
    """Normalized execution request for a streamed NZB repost job."""

    category: str
    source_path: Optional[str]
    release_name: Optional[str] = None
    test_mode: bool = False
    target_indexer_id: Optional[str] = None
    posting_server_name: Optional[str] = None
    submit_mode: str = "post_and_submit"
    enable_duplicate_check: Optional[bool] = None


@dataclass(frozen=True)
class QueueStartSummary:
    """Prepared queue-start state after staged-item validation."""

    runnable_items: tuple[dict[str, Any], ...]
    skipped_items: tuple[tuple[dict[str, Any], str], ...]


def normalize_job_source(source: Any) -> str:
    cleaned = " ".join(str(source or "").strip().split())
    return cleaned[:80] if cleaned else "unknown"


def normalize_job_name(name: Any) -> Optional[str]:
    if name is None:
        return None
    cleaned = " ".join(str(name).split()).strip()
    if not cleaned:
        return None
    return cleaned[:80]


def parse_iso_datetime_utc(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_run_after(raw: Any) -> Optional[str]:
    dt = parse_iso_datetime_utc(raw)
    return dt.isoformat() if dt else None


def normalize_paths(paths: Any) -> list[str]:
    if not paths:
        return []
    if isinstance(paths, (str, Path)):
        return [str(paths)]
    normalized: list[str] = []
    for value in paths:
        text = str(value).strip()
        if text:
            normalized.append(text)
    return normalized


def normalize_job_path_identity(path: Any) -> Optional[str]:
    # Not core.paths.path_key: job identity stays non-resolving (a symlink and its target are distinct jobs).
    text = str(path or "").strip()
    if not text:
        return None
    normalized = os.path.normpath(text)
    if not os.path.isabs(normalized):
        normalized = os.path.abspath(normalized)
    normalized = normalized.replace('\\', '/')
    return normalized.casefold() if os.name == "nt" else normalized


def job_target_paths(job: dict[str, Any]) -> list[str]:
    raw_paths = job.get("_paths")
    if raw_paths is None:
        raw_paths = job.get("target_paths")
    return normalize_paths(raw_paths)


def set_job_target_paths(job: dict[str, Any], paths: Any) -> None:
    normalized = normalize_paths(paths)
    job["_paths"] = normalized
    job["has_explicit_paths"] = bool(normalized)
    if normalized:
        job["target_paths"] = normalized
    else:
        job.pop("target_paths", None)
