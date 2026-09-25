# Auto-split mixin from queueing.py - verbatim method bodies.

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
import psutil
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from core import database
from core.config import get_config
from core.utils import (
    atomic_write_text,
    log_info,
    log_success,
    normalize_submission_category,
    reset_thread_job,
    set_thread_job,
)
from logic.pending_scan import looks_like_generic_tv_season_folder
from logic import usenet_stream
_QUEUE_SOURCE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:WEB(?:[.\s_-]?DL|[.\s_-]?Rip|[.\s_-]?HD)?|WEBDL|WEBRip|WEBHD|BluRay|BDRip|BRRip|REMUX|HDRip|PDRip|HDTV|PDTV|"
    r"SDTV|TVRip|SATRip|DSR|DVB|DVDRip|DVD|VHS(?:Rip)?|DV|UHD|AMZN|NF|NFLX|DSNP|PCOK|HMAX|MAX|HULU|ATVP|AUBC|iT|iP|STAN|CR|"
    r"PMTP|PMNT|CTV|CBC|BBC|PBS|TBS|TNT|NBC|ABC|CBS|FOX|HBO|SHOWTIME|SHO)(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_FINISHED_JOB_RETENTION_COUNT = 100
_FINISHED_JOB_RETENTION_DAYS = 30
_TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "cancelled"})
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

__all__ = [
    'Any', 'BaseModel', 'ConfigDict', 'Field', 'JobState', 'Optional', 'Path', 'ProcessingJobRequest',
    'QueueStartSummary', 'StreamJobRequest', '_FINISHED_JOB_RETENTION_COUNT', '_FINISHED_JOB_RETENTION_DAYS',
    '_QUEUE_SOURCE_TOKEN_RE', '_TERMINAL_JOB_STATUSES', 'atomic_write_text', 'database', 'dataclass', 'datetime',
    'get_config', 'json', 'log_info', 'log_success', 'logger', 'looks_like_generic_tv_season_folder',
    'normalize_submission_category', 'os', 'psutil', 're', 'reset_thread_job', 'set_thread_job', 'shutil',
    'threading', 'time', 'timedelta', 'timezone', 'usenet_stream', 'uuid',
]
