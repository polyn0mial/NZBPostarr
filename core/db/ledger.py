"""Duplicate and completion lookups over the upload ledger, all through one size predicate."""

from __future__ import annotations

import time
from typing import Any, Dict, Generator, List, NamedTuple, Optional, Set

from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload, Session

from core.db import engine as db_engine
from core.db.engine import _log_db_timing, DatabaseOperationalError
from core.db.models import Upload, UploadResult


def size_matches(stored: Any, expected: Any) -> Optional[bool]:
    """The one size rule: True/False when both sizes are known, None when either is unknown.

    The ``filesize`` column has legacy mixed-type storage (some rows hold text), so both sides are
    coerced to int; a value that does not coerce counts as unknown.
    """
    if stored is None or expected is None:
        return None
    try:
        return int(stored) == int(expected)
    except (TypeError, ValueError):
        return None


def _counts_as_upload_of(key: str, stored_name: str, stored_size: Any, expected_size: Any) -> bool:
    """Whether a ledger row found for ``key`` (exact, basename or suffix match) is an upload of it.

    Without an expected size every row counts. With one, a row of a different size never counts, and a
    row of unknown size counts only on an exact key match (bare-name rows are matched by size).
    """
    if expected_size is None:
        return True
    if stored_size is None:
        return stored_name == key
    return size_matches(stored_size, expected_size) is not False


class CompletionIndex(NamedTuple):
    """Per-indexer completion over the active indexers (unpacks like the old 4-tuple)."""

    fully_done: Set[str]
    success_map: Dict[str, Set[str]]
    failed_map: Dict[str, Dict[str, str]]
    filesize_by_indexer: Dict[str, Dict[str, int]]


def destinations_for(item_key: str, _itype: str, indexer_ids: List[str], filesize: Optional[int] = None) -> Dict[str, Optional[str]]:
    """Check which indexers already have this item."""
    results: Dict[str, Optional[str]] = {idx: None for idx in indexer_ids}
    try:
        with db_engine.session_scope() as session:
            basename = item_key.rsplit("/", 1)[-1]
            uploads = _load_duplicate_upload_payloads(
                session,
                exact_names=[item_key, basename],
                suffix_names=[basename],
            )

            for upload in uploads:
                if not _counts_as_upload_of(item_key, upload.get("item_name", ""), upload.get("filesize"), filesize):
                    continue
                for indexer_id, uploaded_at in upload.get("results", {}).items():
                    if indexer_id in results and results[indexer_id] is None:
                        results[indexer_id] = uploaded_at
    except Exception as e:
        logger.error(f"Duplicate check failed for {item_key}: {e}")
        raise DatabaseOperationalError(f"Duplicate check failed for {item_key}") from e
    return results

_DUPLICATE_EXACT_BATCH_SIZE = 900

_DUPLICATE_SUFFIX_BATCH_SIZE = 250

def _iter_chunks(values: List[str], chunk_size: int) -> Generator[List[str], None, None]:
    for start in range(0, len(values), chunk_size):
        yield values[start : start + chunk_size]

def _serialize_duplicate_upload(upload: Upload) -> Dict[str, Any]:
    return {
        "item_name": upload.item_name,
        "filesize": upload.filesize,
        "results": {res.indexer_id: res.uploaded_at.isoformat() for res in upload.results if res.status == "success"},
    }

def _load_duplicate_upload_payloads(
    session: Session,
    *,
    exact_names: List[str],
    suffix_names: List[str],
) -> List[Dict[str, Any]]:
    payloads_by_name: Dict[str, Dict[str, Any]] = {}

    deduped_exact = list(dict.fromkeys(name for name in exact_names if name))
    deduped_suffix = list(dict.fromkeys(name for name in suffix_names if name))

    for chunk in _iter_chunks(deduped_exact, _DUPLICATE_EXACT_BATCH_SIZE):
        stmt = select(Upload).where(Upload.item_name.in_(chunk)).options(selectinload(Upload.results))
        for upload in session.execute(stmt).scalars().all():
            payloads_by_name[upload.item_name] = _serialize_duplicate_upload(upload)

    for chunk in _iter_chunks(deduped_suffix, _DUPLICATE_SUFFIX_BATCH_SIZE):
        suffix_filters = [Upload.item_name.like(f"%/{name}") for name in chunk]
        stmt = select(Upload).where(or_(*suffix_filters)).options(selectinload(Upload.results))
        for upload in session.execute(stmt).scalars().all():
            payloads_by_name[upload.item_name] = _serialize_duplicate_upload(upload)

    return list(payloads_by_name.values())

def destinations_for_batch(
    item_keys: List[str],
    indexer_ids: List[str],
    filesizes: Optional[Dict[str, int]] = None,
) -> Dict[str, Dict[str, Optional[str]]]:
    """Batch duplicate lookup for many item keys.

    ``filesizes`` (optional) maps each item key to its CURRENT on-disk size.
    When provided, a stored upload record whose recorded filesize differs
    from the current file's size is not counted as a duplicate for that
    key -- this is what lets a locally-replaced file (same name, different/
    newer size) be recognized as needing a fresh upload instead of being
    silently skipped as "already done". Mirrors the same size-aware logic
    already used by destinations_for() for single-item lookups.

    Returns:
        {
            "item/key": {"idx_a": "2026-...", "idx_b": None},
            ...
        }
    """
    filesizes = filesizes or {}
    unique_keys = list(dict.fromkeys(k for k in item_keys if k))
    results: Dict[str, Dict[str, Optional[str]]] = {key: {idx: None for idx in indexer_ids} for key in unique_keys}

    if not unique_keys or not indexer_ids:
        return results

    basenames: Dict[str, str] = {key: key.rsplit("/", 1)[-1] for key in unique_keys}

    try:
        with db_engine.session_scope() as session:
            lookup_names = list(dict.fromkeys([*unique_keys, *basenames.values()]))
            upload_payloads = _load_duplicate_upload_payloads(
                session,
                exact_names=lookup_names,
                suffix_names=list(basenames.values()),
            )

        by_name = {payload["item_name"]: payload for payload in upload_payloads}
        by_basename: Dict[str, List[Dict[str, Any]]] = {}
        for payload in upload_payloads:
            basename = str(payload["item_name"]).rsplit("/", 1)[-1]
            by_basename.setdefault(basename, []).append(payload)
        id_set = set(indexer_ids)

        for key in unique_keys:
            row = results[key]
            candidates: List[Dict[str, Any]] = []
            seen_names: Set[str] = set()

            exact = by_name.get(key)
            if exact is not None:
                candidates.append(exact)
                seen_names.add(str(exact["item_name"]))

            basename = basenames[key]
            if basename != key:
                basename_exact = by_name.get(basename)
                if basename_exact is not None and str(basename_exact["item_name"]) not in seen_names:
                    candidates.append(basename_exact)
                    seen_names.add(str(basename_exact["item_name"]))

            for payload in by_basename.get(basename, []):
                payload_name = str(payload["item_name"])
                if payload_name in seen_names:
                    continue
                candidates.append(payload)
                seen_names.add(payload_name)

            current_size = filesizes.get(key)

            for payload in candidates:
                if not _counts_as_upload_of(key, str(payload.get("item_name", "")), payload.get("filesize"), current_size):
                    continue

                payload_results = payload.get("results", {}) if isinstance(payload, dict) else {}
                for indexer_id, uploaded_at in payload_results.items():
                    if indexer_id in id_set and row[indexer_id] is None:
                        row[indexer_id] = uploaded_at
    except Exception as e:
        logger.error(f"Batch duplicate check failed for {len(unique_keys)} items: {e}")
        raise DatabaseOperationalError(f"Batch duplicate check failed for {len(unique_keys)} items") from e

    return results

def completion_index(
    active_ids: List[str],
) -> CompletionIndex:
    """Fetch the 'all-done' set, per-indexer success map, per-indexer failed map,
    and the stored filesize behind each (item_name, indexer_id) success.

    Returns:
        (fully_done_names, success_map, failed_map, filesize_by_indexer)
        - success_map: {item_name: {indexer_ids that succeeded}}
        - failed_map:  {item_name: {indexer_id: error_message}} for items
          that have a 'failed' result but no 'success' result for that indexer.
        - filesize_by_indexer: {item_name: {indexer_id: filesize}} for the
          Upload row behind that success, used by callers to confirm a
          "completed" name still refers to the same file/folder size that
          was actually uploaded, not just the same name.
    """
    if not active_ids:
        return CompletionIndex(set(), {}, {}, {})

    started = time.perf_counter()
    try:
        with db_engine.session_scope() as session:
            # Fetch distinct (item_name, indexer_id, filesize) rows for active indexers (SUCCESS).
            stmt = (
                select(Upload.item_name, UploadResult.indexer_id, Upload.filesize)
                .join(UploadResult)
                .filter(UploadResult.indexer_id.in_(active_ids))
                .where(UploadResult.status == "success")
                .distinct()
            )

            mapping: Dict[str, Set[str]] = {}
            filesize_by_indexer: Dict[str, Dict[str, int]] = {}
            for name, idx_id, filesize in session.execute(stmt):
                if name not in mapping:
                    mapping[name] = set()
                mapping[name].add(idx_id)
                if filesize is not None:
                    # The filesize column has legacy mixed-type storage (some
                    # historical rows stored it as text) - coerce to int so
                    # downstream size comparisons never fail on a str/int
                    # mismatch between otherwise-equal values.
                    try:
                        filesize_by_indexer.setdefault(name, {})[idx_id] = int(filesize)
                    except (TypeError, ValueError):
                        pass

            # Fetch failed results (only where there is NO success for that indexer)
            failed_stmt = (
                select(Upload.item_name, UploadResult.indexer_id, UploadResult.error)
                .join(UploadResult)
                .filter(UploadResult.indexer_id.in_(active_ids))
                .where(UploadResult.status == "failed")
                .distinct()
            )

            failed_map: Dict[str, Dict[str, str]] = {}
            for name, idx_id, error in session.execute(failed_stmt):
                # Only include in failed_map if NOT already succeeded
                if name in mapping and idx_id in mapping[name]:
                    continue
                if name not in failed_map:
                    failed_map[name] = {}
                failed_map[name][idx_id] = error or "Unknown error"

            # Determine which items are fully done across all active indexers
            required_count = len(active_ids)
            active_set = set(active_ids)
            fully_done = {name for name, idxs in mapping.items() if len(idxs & active_set) >= required_count}

            _log_db_timing(
                "completion_index",
                started,
                context=f"active_ids={len(active_ids)} mapped_items={len(mapping)} fully_done={len(fully_done)} failed={len(failed_map)}",
                warn_threshold_s=0.75,
            )
            return CompletionIndex(fully_done, mapping, failed_map, filesize_by_indexer)
    except Exception as e:
        logger.error(f"Failed to fetch dashboard data: {e}")
        raise DatabaseOperationalError("Failed to fetch dashboard data") from e
