"""Upload ledger writes: NNTP success, per-destination results, manual marks and deletes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast, List, Optional

from loguru import logger
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from core.db import engine as db_engine
from core.db.engine import DatabaseOperationalError
from core.db.models import Upload, UploadResult
from core.db.schema import uploads_columns
from core.db.timefmt import sql_timestamp
from core.release_name import parse_release_name


def _get_or_create_upload_row(
    session: Session,
    item_key: str,
    *,
    filesize: Optional[int] = None,
    itype: Optional[str] = None,
) -> Upload:
    """Fetch an Upload row or create it safely under concurrent writers.

    Dual uploads can record the same `item_name` from multiple threads at nearly
    the same time. The `uploads.item_name` column is unique, so a naive
    SELECT-then-INSERT can race and raise IntegrityError.
    """
    upload = session.execute(select(Upload).filter_by(item_name=item_key)).scalar_one_or_none()
    if upload:
        return upload

    meta = parse_release_name(item_key)
    upload = Upload(
        item_name=item_key,
        filesize=filesize,
        itype=itype,
        parsed_title=meta["parsed_title"],
        media_type=meta["media_type"],
        season_number=meta["season_number"],
        episode_number=meta["episode_number"],
        episode_end_number=meta.get("episode_end_number"),
    )
    session.add(upload)
    try:
        # Flush early so we can handle unique violations without crashing the job.
        session.flush()
        return upload
    except IntegrityError:
        session.rollback()
        # Another thread likely inserted it first; load and continue.
        upload = session.execute(select(Upload).filter_by(item_name=item_key)).scalar_one_or_none()
        if upload:
            return upload
        raise

def _restore_updated_at(upload: Upload, previous: datetime) -> None:
    """Pin updated_at back to ``previous`` even when other columns change.

    Re-assigning an unchanged value is not a net change, so SQLAlchemy would leave
    the column out of the UPDATE and onupdate=_utc_now would still fire.
    """
    upload.updated_at = previous
    flag_modified(upload, "updated_at")

def record_nntp_success(item_key: str, size: int, itype: str, *, bump_timestamp: bool = True) -> None:
    """Ensure a record exists in the uploads table after successful NNTP upload."""
    try:
        with db_engine.session_scope() as session:
            upload = _get_or_create_upload_row(session, item_key, filesize=size, itype=itype)
            _prev_ts = upload.updated_at
            upload.filesize = size
            upload.itype = itype
            if bump_timestamp:
                # Only bump on first upload - resuming a partial job keeps the original
                # timestamp so history ordering stays stable.
                has_prior = session.execute(
                    select(UploadResult.id)
                    .where(UploadResult.upload_id == upload.id)
                    .limit(1)
                ).scalar_one_or_none() is not None
                if not has_prior:
                    upload.updated_at = datetime.now(timezone.utc)
                elif _prev_ts is not None:
                    _restore_updated_at(upload, _prev_ts)
            elif _prev_ts is not None:
                # Restore original timestamp: onupdate=_utc_now fires for any dirty row,
                # so we must explicitly pin it back when we only want to update other fields.
                _restore_updated_at(upload, _prev_ts)
            if upload.parsed_title is None:
                meta = parse_release_name(item_key)
                upload.parsed_title = meta["parsed_title"]
                upload.media_type = meta["media_type"]
                upload.season_number = meta["season_number"]
                upload.episode_number = meta["episode_number"]
                upload.episode_end_number = meta.get("episode_end_number")
    except IntegrityError:
        # Best-effort: don't let NNTP success recording crash the job.
        logger.warning(f"record_nntp_success skipped due to duplicate item_name: {item_key}")
    except Exception as e:
        logger.error(f"record_nntp_success failed for {item_key}: {e}")

def update_db_destination(
    dest: str,
    _name: str,
    size: int,
    key: str,
    itype: Optional[str] = None,
    _bump_timestamp: bool = True,
    **stats: Any,
) -> bool:
    """Update the database with upload results for a specific destination.

    Returns whether the write landed. The DB layer does not refresh live views: a caller that wrote a
    success calls ``logic.queue_metrics.request_live_queue_refresh``.
    """
    try:
        with db_engine.session_scope() as session:
            upload = _get_or_create_upload_row(session, key, filesize=size, itype=itype)
            _prev_ts = upload.updated_at
            if upload.parsed_title is None:
                meta = parse_release_name(key)
                upload.parsed_title = meta["parsed_title"]
                upload.media_type = meta["media_type"]
                upload.season_number = meta["season_number"]
                upload.episode_number = meta["episode_number"]
                upload.episode_end_number = meta.get("episode_end_number")

            # Check if result for this indexer already exists
            result = session.execute(
                select(UploadResult).filter_by(upload_id=upload.id, indexer_id=dest)
            ).scalar_one_or_none()

            if not result:
                result = UploadResult(upload_id=upload.id, indexer_id=dest)
                session.add(result)

            # Update stats
            new_status = stats.get("status", "success")
            prior_success = result.status == "success"
            # Don't overwrite a prior success with a failure -- dupe check relies on
            # success records to skip already-uploaded items.  A failed re-upload attempt
            # should not erase the fact that the item was previously delivered.
            if new_status == "success" or not prior_success:
                result.uploaded_at = datetime.now(timezone.utc)
                result.duration = stats.get("duration")
                result.speed_bps = stats.get("speed_bps")
                result.server_name = stats.get("server_name")
                result.status = new_status
                result.error = stats.get("error")

            legacy_columns = {"geek": "uploaded_at_geek", "in": "uploaded_at_in", "omg": "uploaded_at_omg"}
            legacy_column = legacy_columns.get(dest)
            if legacy_column and result.status == "success":
                if legacy_column in uploads_columns(session):
                    session.execute(
                        text(f"UPDATE uploads SET {legacy_column} = CURRENT_TIMESTAMP WHERE id = :upload_id"),
                        {"upload_id": upload.id},
                    )

            if size is not None and size > 0:
                upload.filesize = size
            if itype:
                upload.itype = itype

            # Bump updated_at only on the first successful result - so resuming a partial
            # upload does not push the item to the top of history.
            if _bump_timestamp:
                _prior_success = session.execute(
                    select(UploadResult.id)
                    .where(UploadResult.upload_id == upload.id, UploadResult.status == "success")
                    .limit(1)
                ).scalar_one_or_none() is not None
                if not _prior_success:
                    upload.updated_at = datetime.now(timezone.utc)
                elif _prev_ts is not None:
                    _restore_updated_at(upload, _prev_ts)
            elif _prev_ts is not None:
                # Restore original timestamp: onupdate=_utc_now fires for any dirty row.
                _restore_updated_at(upload, _prev_ts)

        return True
    except Exception as e:
        logger.error(f"DB Update failed for {key} -> {dest}: {e}")
        return False

def pin_folder_ts_to_children(folder_key: str) -> None:
    """Keep the pack/folder just above its newest child in history.

    Writes the timestamp via raw SQL in the same 'YYYY-MM-DD HH:MM:SS.ffffff'
    format the ORM's SQLite DateTime type stores, so SQLite string ordering
    stays consistent with ORM-written rows and the onupdate hook is bypassed.
    """
    try:
        with db_engine.session_scope() as session:
            folder = session.execute(
                select(Upload).filter_by(item_name=folder_key)
            ).scalar_one_or_none()
            if folder is None:
                return
            child_max_raw = session.execute(
                select(func.max(Upload.updated_at))
                .where(Upload.item_name.like(folder_key + "/%"))
            ).scalar_one_or_none()
            if child_max_raw is None:
                return
            # Normalise to a naive datetime regardless of whether SQLAlchemy
            # returned a datetime object or a raw string from SQLite.
            if isinstance(child_max_raw, str):
                child_max_dt = datetime.fromisoformat(child_max_raw.replace("T", " ").split("+")[0])
            else:
                child_max_dt = child_max_raw.replace(tzinfo=None) if child_max_raw.tzinfo else child_max_raw
            target = child_max_dt + timedelta(microseconds=1)
            target_str = sql_timestamp(target)
            # Use raw SQL so ORM datetime serialization cannot change the format
            session.execute(
                text("UPDATE uploads SET updated_at = :ts WHERE id = :id AND updated_at != :ts"),
                {"ts": target_str, "id": folder.id},
            )
    except Exception as e:
        logger.debug(f"pin_folder_ts_to_children non-fatal for {folder_key!r}: {e}")

def delete_upload_item(name: str) -> bool:
    """Delete an item from history."""
    try:
        with db_engine.session_scope() as session:
            upload = session.execute(select(Upload).filter_by(item_name=name)).scalar_one_or_none()
            if upload:
                session.delete(upload)
                return True
            return False
    except Exception as e:
        logger.error(f"Failed to delete {name}: {e}")
        raise DatabaseOperationalError(f"Failed to delete {name}") from e

def bulk_delete_upload_items(names: List[str]) -> int:
    """Bulk delete items."""
    try:
        with db_engine.session_scope() as session:
            if not names:
                return 0

            # NOTE: SQLAlchemy bulk deletes bypass ORM cascades. Since upload_results
            # has a FK to uploads and we enable `PRAGMA foreign_keys=ON`, we must
            # delete dependent rows explicitly first.
            id_subq = select(Upload.id).where(Upload.item_name.in_(names)).scalar_subquery()
            session.execute(delete(UploadResult).where(UploadResult.upload_id.in_(id_subq)))

            stmt = delete(Upload).where(Upload.item_name.in_(names))
            result = session.execute(stmt)
            return cast(Any, result).rowcount or 0
    except Exception as e:
        logger.error(f"Bulk delete failed: {e}")
        raise DatabaseOperationalError("Bulk delete failed") from e

def mark_as_uploaded(
    item_keys: List[str],
    indexer_ids: List[str],
    itype: str = "Misc",
) -> int:
    """Mark specific items as uploaded to specific indexers (manual override).

    Creates Upload + UploadResult records so duplicate checkers skip them.
    Returns the number of new result records created; when it is above zero the caller requests a live
    queue refresh.
    """
    created = 0
    try:
        with db_engine.session_scope() as session:
            for key in item_keys:
                upload = session.execute(select(Upload).filter_by(item_name=key)).scalar_one_or_none()
                if not upload:
                    upload = Upload(item_name=key, filesize=0, itype=itype)
                    session.add(upload)
                    session.flush()

                for idx_id in indexer_ids:
                    existing = session.execute(
                        select(UploadResult).filter_by(upload_id=upload.id, indexer_id=idx_id)
                    ).scalar_one_or_none()
                    if not existing:
                        result = UploadResult(
                            upload_id=upload.id,
                            indexer_id=idx_id,
                            status="manual",
                            duration=0,
                            speed_bps=0,
                            server_name="manual",
                        )
                        session.add(result)
                        created += 1

                upload.updated_at = datetime.now(timezone.utc)
    except Exception as e:
        logger.error(f"mark_as_uploaded failed: {e}")
    return created
