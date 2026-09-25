"""Schema setup: additive, idempotent init over the live schema and the cached uploads columns."""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from core.db.engine import get_engine
from core.db.models import Base
from core.release_name import parse_release_name

# Tables the live DB carries from older builds. They are never created, dropped or written here, and the
# trigger ``update_avg_on_insert`` on them is left alone.
TOLERATED_TABLES = frozenset(
    {"upload_stats", "upload_stats_avg", "stage_timings", "server_speeds", "server_failures", "system_stats_history"}
)

# (engine, columns): a new engine (another DB file) probes again.
_UPLOADS_COLUMNS: Optional[Tuple[Engine, FrozenSet[str]]] = None


def _probe_uploads_columns(conn: Connection | Session) -> FrozenSet[str]:
    return frozenset(row[1] for row in conn.execute(text("PRAGMA table_info(uploads)")).fetchall())


def uploads_columns(conn: Connection | Session) -> FrozenSet[str]:
    """The ``uploads`` column set, probed once per engine (at init) and cached."""
    global _UPLOADS_COLUMNS
    engine = get_engine()
    if _UPLOADS_COLUMNS is None or _UPLOADS_COLUMNS[0] is not engine:
        _UPLOADS_COLUMNS = (engine, _probe_uploads_columns(conn))
    return _UPLOADS_COLUMNS[1]


def _backfill_parsed_metadata() -> None:
    """One-time backfill of parsed_title / media_type / season / episode for all uploads."""
    engine = get_engine()
    logger.info("Backfilling parsed metadata for uploads...")
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, item_name FROM uploads WHERE parsed_title IS NULL")).fetchall()
        if not rows:
            return
        logger.info(f"  Parsing {len(rows)} item names...")
        stmt = text(
            "UPDATE uploads SET parsed_title=:pt, media_type=:mt, "
            "season_number=:sn, episode_number=:en, episode_end_number=:een WHERE id=:id"
        )
        batch: List[Dict[str, Any]] = []
        for row_id, item_name in rows:
            meta = parse_release_name(item_name or "")
            batch.append(
                {
                    "id": row_id,
                    "pt": meta["parsed_title"],
                    "mt": meta["media_type"],
                    "sn": meta["season_number"],
                    "en": meta["episode_number"],
                    "een": meta.get("episode_end_number"),
                }
            )
            if len(batch) >= 1000:
                conn.execute(stmt, batch)
                batch.clear()
        if batch:
            conn.execute(stmt, batch)
        conn.commit()
        logger.info(f"  Backfill complete: {len(rows)} uploads updated.")

def init_database() -> bool:
    """Initialize the database and create tables (additive and idempotent over the live schema)."""
    global _UPLOADS_COLUMNS
    try:
        engine = get_engine()
        Base.metadata.create_all(
            engine, tables=[table for table in Base.metadata.sorted_tables if table.name not in TOLERATED_TABLES]
        )

        # Simple manual "migration" for test_mode column if it doesn't exist
        with engine.connect() as conn:
            # Check for test_mode in job_history
            result = conn.execute(text("PRAGMA table_info(job_history)"))
            columns = [row[1] for row in result.fetchall()]
            if columns and "test_mode" not in columns:
                logger.info("Adding 'test_mode' column to job_history table...")
                conn.execute(text("ALTER TABLE job_history ADD COLUMN test_mode BOOLEAN DEFAULT 0"))
                conn.commit()

            # Check for status/error in upload_results
            result = conn.execute(text("PRAGMA table_info(upload_results)"))
            columns = [row[1] for row in result.fetchall()]
            if columns and "status" not in columns:
                logger.info("Adding 'status' and 'error' columns to upload_results table...")
                conn.execute(text("ALTER TABLE upload_results ADD COLUMN status VARCHAR DEFAULT 'success'"))
                conn.execute(text("ALTER TABLE upload_results ADD COLUMN error VARCHAR"))
                conn.commit()

            # ── Parsed metadata columns on uploads ──
            result = conn.execute(text("PRAGMA table_info(uploads)"))
            upload_cols = [row[1] for row in result.fetchall()]
            for col_name, col_type in [
                ("parsed_title", "VARCHAR"),
                ("media_type", "VARCHAR"),
                ("season_number", "INTEGER"),
                ("episode_number", "INTEGER"),
                ("episode_end_number", "INTEGER"),
            ]:
                if col_name not in upload_cols:
                    logger.info(f"Adding '{col_name}' column to uploads table...")
                    conn.execute(text(f"ALTER TABLE uploads ADD COLUMN {col_name} {col_type}"))
                    conn.commit()

            # Probe the final column set once; update_db_destination reads the cache.
            _UPLOADS_COLUMNS = (engine, _probe_uploads_columns(conn))

            # Ensure indexes exist
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uploads_parsed_title ON uploads(parsed_title)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uploads_media_type ON uploads(media_type)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uploads_updated_at ON uploads(updated_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_uploads_created_at ON uploads(created_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_upload_results_upload_id ON upload_results(upload_id)"))
            conn.execute(
                text("CREATE INDEX IF NOT EXISTS ix_upload_results_uploaded_at ON upload_results(uploaded_at)")
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_upload_results_upload_id_indexer_id "
                    "ON upload_results(upload_id, indexer_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_upload_results_indexer_status_upload "
                    "ON upload_results(indexer_id, status, upload_id)"
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_upload_results_status_uploaded_at "
                    "ON upload_results(status, uploaded_at)"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_job_history_started_at ON job_history(started_at)"))
            conn.commit()

            # Backfill parsed metadata for any uploads that lack it
            needs_backfill = conn.execute(text("SELECT COUNT(*) FROM uploads WHERE parsed_title IS NULL")).scalar()
            if needs_backfill:
                # Close this connection before starting backfill to avoid locks
                conn.commit()

        # Run backfill outside the first connection block
        if needs_backfill:
            _backfill_parsed_metadata()

        return True
    except (SQLAlchemyError, OSError) as e:
        logger.error(f"Database initialization failed: {e}")
        return False
