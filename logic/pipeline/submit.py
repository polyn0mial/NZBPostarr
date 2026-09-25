"""Indexer submission: submit an NZB per indexer and record each destination's result."""

from __future__ import annotations

import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from core.config import Config
from core.db.uploads import update_db_destination
from core.indexers.http_submit import submit_to_indexer
from core.indexers.models import SubmitResult
from core.indexers.registry import get_indexer
from core.logging import log_info
from logic.jobs.context import get_thread_job
from logic.pipeline.record import _record_folder_hierarchy_rows, refresh_pending_after_upload


# Error statuses that should NOT be retried (permanent failures)
_PERMANENT_FAILURE_STATUSES = frozenset({"duplicate", "misconfigured"})


def submit_api(
    name: str,
    dest: str,
    config: Config,
    nzb_path: Optional[Path] = None,
    cat: str = "tv",
    nfo_path: Optional[Path] = None,
    mediainfo_path: Optional[Path] = None,
) -> SubmitResult:
    """Submit NZB to indexer API via dynamic plugin system.

    Retries transient failures (network errors, 5xx, timeouts) up to
    ``upload_max_retries`` times with exponential backoff.  Permanent
    failures (invalid API key, banned) are returned immediately.
    """
    nzb = nzb_path or config.get_nzb_path(name)
    if not nzb.exists():
        reason = f"NZB not found at {nzb}"
        logger.error(f"Submission failed: {reason}")
        return SubmitResult(False, "error", reason)

    # Guard against empty/truncated NZB files (e.g. Nyuu partial failure)
    nzb_size = nzb.stat().st_size
    if nzb_size == 0:
        reason = f"NZB is 0 bytes (empty file) at {nzb}"
        logger.error(f"Submission blocked: {reason}")
        return SubmitResult(False, "error", reason)
    if nzb_size < 100:
        reason = f"NZB is suspiciously small ({nzb_size} bytes) at {nzb}"
        logger.error(f"Submission blocked: {reason}")
        return SubmitResult(False, "error", reason)

    # Get the indexer definition
    indexer = get_indexer(dest)
    if not indexer:
        reason = f"Unknown indexer identifier: {dest}"
        logger.error(reason)
        return SubmitResult(False, "error", reason)

    try:
        job = get_thread_job()
        if config.test_run or (job and job.get("test_mode")):
            log_info(f"[TEST MODE] Skipping indexer submission to {indexer.log_name}")
            return SubmitResult(True, "success", "Test mode: indexer submission skipped")

        # Clean "Priority " from release name for submission
        clean_name = name.replace("Priority ", "")

        # Sanitize release name
        rls_name = re.sub(r"\.(nzb|mkv|mp4|avi|ts|m4v|wmv)$", "", clean_name, flags=re.I)
        # Transliterate accents ("Pokémon" -> "Pokemon") instead of turning them into dots.
        rls_name = unicodedata.normalize("NFKD", rls_name).encode("ascii", "ignore").decode("ascii")
        rls_name = rls_name.replace(" & ", ".and.").replace("&", ".and.")
        rls_name = rls_name.replace("'", "")
        rls_name = re.sub(r"[^a-zA-Z0-9.\-_]", ".", rls_name)
        rls_name = re.sub(r"\.{2,}", ".", rls_name).strip(".")

        max_retries = getattr(config, "upload_max_retries", 3)
        retry_delay = getattr(config, "upload_retry_delay_seconds", 5)
        last_reason = ""

        for attempt in range(1, max_retries + 1):
            if attempt > 1:
                # Check if job was stopped between retries
                if job and job.get("stop_requested"):
                    return SubmitResult(False, "error", last_reason or "Job stopped during retry")
                wait = retry_delay * attempt  # linear backoff: 10s, 15s, 20s, ...
                logger.info(f"{indexer.log_name} Retry {attempt}/{max_retries} in {wait}s...")
                time.sleep(wait)

            result = submit_to_indexer(
                indexer=indexer,
                rls_name=rls_name,
                nzb_path=nzb,
                config=config,
                cat=cat,
                nfo_path=nfo_path,
                mediainfo_path=mediainfo_path,
            )
            success, status, reason = result.success, result.status, result.reason
            last_reason = reason

            if success:
                return result

            # Permanent failure - no point retrying
            if status in _PERMANENT_FAILURE_STATUSES:
                logger.warning(f"{indexer.log_name} Permanent failure ({status}): {reason}")
                return SubmitResult(False, status, reason)

            # Retryable failure - try again unless this was the last attempt
            if attempt < max_retries:
                logger.warning(f"{indexer.log_name} Attempt {attempt}/{max_retries} failed ({status}): {reason}")
            else:
                logger.error(f"{indexer.log_name} All {max_retries} attempts failed: {reason}")

        return SubmitResult(False, "error", last_reason)
    except Exception as e:
        reason = str(e)
        logger.error(f"{indexer.log_name} Submission Error: {reason}")
        return SubmitResult(False, "error", reason)


def _submit_api_batch(
    upload_set: dict[str, Any],
    *,
    conf: Any,
    name: str,
    priority_label: str,
    unique_nzb: Path,
    submission_category: str,
    nfo_path: Optional[Path],
    mediainfo_path: Optional[Path],
) -> list[tuple[str, bool, str, str]]:
    """Submit an uploaded NZB to one or more indexers."""

    def submit_one(dest_id: str) -> tuple[str, bool, str, str]:
        result = submit_api(
            f"{priority_label}{name}",
            dest_id,
            conf,
            nzb_path=unique_nzb,
            cat=submission_category,
            nfo_path=nfo_path,
            mediainfo_path=mediainfo_path,
        )
        return dest_id, result.success, result.reason, result.status

    dests = upload_set["dests"]
    if len(dests) == 1:
        try:
            return [submit_one(dests[0])]
        except Exception as exc:
            reason = f"Unhandled submission exception for indexer '{dests[0]}': {exc}"
            logger.exception(reason)
            return [(dests[0], False, reason, "error")]

    results: dict[str, tuple[str, bool, str, str]] = {}
    with ThreadPoolExecutor(max_workers=len(dests)) as api_pool:
        futures = {api_pool.submit(submit_one, dest_id): dest_id for dest_id in dests}
        for future in as_completed(futures):
            dest_id = futures[future]
            try:
                results[dest_id] = future.result()
            except Exception as exc:
                reason = f"Unhandled submission exception for indexer '{dest_id}': {exc}"
                logger.exception(reason)
                results[dest_id] = (dest_id, False, reason, "error")
    return [results[dest_id] for dest_id in dests]


def _persist_submission_results(
    api_results: list[tuple[str, bool, str, str]],
    *,
    name: str,
    item_size: int,
    key: str,
    itype: str,
    item_path: Path,
    base_folder: Optional[Path],
    category: str,
    test_mode: bool,
    upload_result: dict[str, Any],
) -> bool:
    """Persist per-indexer submission results and return whether any succeeded."""
    from logic.queue_metrics import request_live_queue_refresh

    any_success = False
    for dest_id, ok, reason, sub_status in api_results:
        if ok:
            logger.info(f"[INDEXER] {dest_id} accepted '{name}'")
            any_success = True
            if not test_mode and item_size > 0:
                if update_db_destination(dest_id, name, item_size, key, itype=itype, **upload_result):
                    request_live_queue_refresh(reason="upload-success")
                _record_folder_hierarchy_rows(
                    item_path,
                    base_folder=base_folder,
                    category=category,
                    dest_id=dest_id,
                    upload_result=upload_result,
                )
            continue

        logger.error(f"[INDEXER] {dest_id} failed '{name}': {reason or 'unknown error'}")
        if sub_status == "duplicate":
            logger.info(f"[INDEXER] {dest_id} duplicate treated as already-posted for '{name}'")
            any_success = True
            if not test_mode and item_size > 0:
                if update_db_destination(dest_id, name, item_size, key, itype=itype, **upload_result):
                    request_live_queue_refresh(reason="upload-success")
            continue
        if not test_mode and item_size > 0:
            update_db_destination(
                dest_id,
                name,
                item_size,
                key,
                itype=itype,
                status="failed",
                error=reason or "Indexer submission rejected or unreachable",
            )
    if any_success and not test_mode:
        refresh_pending_after_upload()
    return any_success


def submit_and_record(
    submission_groups: list[dict[str, Any]],
    *,
    conf: Any,
    name: str,
    nzb_path: Path,
    submission_category: str,
    item_size: int,
    key: str,
    itype: str,
    item_path: Path,
    base_folder: Optional[Path],
    category: str,
    test_mode: bool,
    upload_result: dict[str, Any],
    nfo_path: Optional[Path] = None,
    mediainfo_path: Optional[Path] = None,
) -> tuple[list[tuple[str, bool, str, str]], bool]:
    """Submit one posted NZB to every destination group and record each destination's outcome.

    Each group is ``{"dests": [...], "priority": bool}``; priority groups submit under the
    "Priority " name prefix. One indexer failing never stops the others. A "duplicate" answer
    counts as already posted. Returns the per-indexer results (dest, ok, reason, status) and
    whether any destination now holds the release.
    """
    api_results: list[tuple[str, bool, str, str]] = []
    for group in submission_groups:
        priority = bool(group.get("priority"))
        group_set = {"id": "/".join(group["dests"]), "dests": group["dests"], "priority": priority}
        api_results.extend(
            _submit_api_batch(
                group_set,
                conf=conf,
                name=name,
                priority_label="Priority " if priority else "",
                unique_nzb=nzb_path,
                submission_category=submission_category,
                nfo_path=nfo_path,
                mediainfo_path=mediainfo_path,
            )
        )
    any_success = _persist_submission_results(
        api_results,
        name=name,
        item_size=item_size,
        key=key,
        itype=itype,
        item_path=item_path,
        base_folder=base_folder,
        category=category,
        test_mode=test_mode,
        upload_result=upload_result,
    )
    return api_results, any_success
