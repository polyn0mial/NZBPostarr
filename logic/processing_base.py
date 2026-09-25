# Auto-split from processing.py - verbatim symbol bodies, synthesized imports.

import copy
import multiprocessing as mp
import os
import queue as stdlib_queue
import re
import shutil
import subprocess
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Iterator, List, Optional, cast
import humanfriendly  # type: ignore[import-untyped]
from loguru import logger
from core.config import get_config
from core.database import (
    pin_folder_ts_to_children,
    record_nntp_success,
    update_db_destination,
)
from core.fs import compute_size_uncached, should_skip_file
from core.logging import log_completed, log_info, log_success, log_verbose
from core.media import (
    AUDIOBOOK_EXTENSIONS,
    EBOOK_EXTENSIONS,
    MUSIC_EXTENSIONS,
    normalize_category,
    VIDEO_EXTENSIONS,
)
from core.proc import extract_percentage, extract_speed, run_command
from logic.classify.patterns import has_multi_file_episode_pattern
from logic.jobs.context import get_thread_job, set_thread_job, update_job_progress, wait_for_job_resume
from logic.pipeline.cleanup import purge_item_data
from logic.classify.explicit import resolve_explicit_path
from logic.classify.names import has_clear_movie_year, looks_like_tv_name
from logic.classify.tv_packs import _tv_pack_episode_rejection_reason
from logic.pending.roots import find_configured_root, get_configured_folders, scan_configured_items
from logic.uploaders import submit_api, upload_item
_ONE_GIB: int = humanfriendly.parse_size("1 GiB")
_APP_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bat",
    ".bin",
    ".deb",
    ".dmg",
    ".exe",
    ".img",
    ".ipa",
    ".iso",
    ".msi",
    ".pkg",
    ".rar",
    ".rpm",
    ".tar",
    ".tbz2",
    ".tgz",
    ".xz",
    ".zip",
}
_ITEM_VALIDATION_TIMEOUT_SECONDS = 30.0

__all__ = [
    'AUDIOBOOK_EXTENSIONS', 'Any', 'Callable', 'Dict', 'EBOOK_EXTENSIONS', 'FutureTimeoutError', 'Iterator',
    'List', 'Lock', 'MUSIC_EXTENSIONS', 'Optional', 'Path', 'ThreadPoolExecutor', 'VIDEO_EXTENSIONS',
    '_APP_EXTENSIONS', '_ITEM_VALIDATION_TIMEOUT_SECONDS', '_ONE_GIB', '_tv_pack_episode_rejection_reason',
    'as_completed', 'cast', 'compute_size_uncached', 'copy', 'dataclass', 'defaultdict', 'extract_percentage',
    'extract_speed', 'field', 'find_configured_root', 'get_config', 'get_configured_folders', 'get_thread_job',
    'has_clear_movie_year', 'has_multi_file_episode_pattern', 'humanfriendly', 'log_completed', 'log_info',
    'log_success', 'log_verbose', 'logger', 'looks_like_tv_name', 'mp', 'normalize_category', 'os',
    'pin_folder_ts_to_children', 'purge_item_data', 're', 'record_nntp_success', 'resolve_explicit_path', 'run_command',
    'scan_configured_items', 'set_thread_job', 'should_skip_file', 'shutil', 'stdlib_queue', 'submit_api',
    'subprocess', 'update_db_destination', 'update_job_progress', 'upload_item', 'uuid', 'wait_for_job_resume',
]
