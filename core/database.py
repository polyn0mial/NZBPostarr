"""
NZBPostarr - Database Module (SQLAlchemy Edition)
"""

# pylint: disable=not-callable

import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Generator,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
    cast,
)

from loguru import logger
from sqlalchemy import (
    Boolean,
    DateTime,
    Engine,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    desc,
    event,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    selectinload,
    sessionmaker,
)
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.pool import QueuePool

from core.paths import path_key, resolve_path
from core.release_name import parse_release_name
from core.logging import log_backend_timing

_F = TypeVar("_F", bound=Callable[..., Any])


class DatabaseOperationalError(RuntimeError):
    """Raised when database reads fail and callers must handle degraded state explicitly."""


def _retry_on_lock(max_retries: int = 3, base_delay: float = 0.25) -> Callable[[_F], _F]:
    """Retry a function when SQLite raises 'database is locked'.

    Uses exponential back-off: 0.25 s → 0.5 s → 1.0 s (default).
    """

    def decorator(func: _F) -> _F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (OperationalError, SQLAlchemyError) as exc:
                    if "database is locked" not in str(exc):
                        raise
                    last_exc = exc
                    if attempt < max_retries - 1:
                        delay = base_delay * (2**attempt)
                        logger.debug(f"DB locked on {func.__name__}, retry {attempt + 1}/{max_retries} in {delay:.2f}s")
                        time.sleep(delay)
            # All retries exhausted - raise last exception
            raise last_exc  # type: ignore[misc]

        return wrapper  # type: ignore[return-value]

    return decorator


def _log_db_timing(name: str, start_time: float, *, context: str = "", warn_threshold_s: float = 0.5) -> None:
    """Emit lightweight timing logs for database-heavy operations."""
    log_backend_timing(
        f"db.{name}",
        start_time,
        context=context,
        warn_threshold_s=warn_threshold_s,
    )


def _utc_now():
    return datetime.now(timezone.utc)


def _isoformat_utc(value: Optional[datetime]) -> Optional[str]:
    """Serialize datetimes with an explicit UTC offset for API consumers."""
    if value is None:
        return None

    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Base(DeclarativeBase):
    """Modern SQLAlchemy Declarative Base."""


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(primary_key=True)
    item_name: Mapped[str] = mapped_column(unique=True, nullable=False, index=True)
    itype: Mapped[Optional[str]] = mapped_column(String)
    filesize: Mapped[Optional[int]] = mapped_column(Integer)
    # Parsed metadata - populated automatically on insert, indexed for grouped queries
    parsed_title: Mapped[Optional[str]] = mapped_column(String, index=True)
    media_type: Mapped[Optional[str]] = mapped_column(String, index=True)  # tv, movie, other
    season_number: Mapped[Optional[int]] = mapped_column(Integer)
    episode_number: Mapped[Optional[int]] = mapped_column(Integer)
    episode_end_number: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=_utc_now,
        onupdate=_utc_now,
        index=True,
    )
    results: Mapped[List["UploadResult"]] = relationship(
        "UploadResult", back_populates="upload", cascade="all, delete-orphan"
    )


class UploadResult(Base):
    __tablename__ = "upload_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id"), nullable=False, index=True)
    indexer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)
    duration: Mapped[Optional[float]] = mapped_column(Float)
    speed_bps: Mapped[Optional[float]] = mapped_column(Float)
    server_name: Mapped[Optional[str]] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="success", index=True)  # success, failed
    error: Mapped[Optional[str]] = mapped_column(String)
    upload: Mapped["Upload"] = relationship("Upload", back_populates="results")


class JobHistory(Base):
    __tablename__ = "job_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items_processed: Mapped[int] = mapped_column(Integer, default=0)
    items_total: Mapped[int] = mapped_column(Integer, default=0)
    items_skipped: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_message: Mapped[Optional[str]] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class SystemStat(Base):
    """Historical system metrics for the dashboard."""

    __tablename__ = "system_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    cpu_percent: Mapped[Optional[float]] = mapped_column(Float)
    memory_percent: Mapped[Optional[float]] = mapped_column(Float)
    upload_mbps: Mapped[Optional[float]] = mapped_column(Float)
    download_mbps: Mapped[Optional[float]] = mapped_column(Float)
    total_sent_mb: Mapped[float] = mapped_column(Float, default=0.0)
    total_recv_mb: Mapped[float] = mapped_column(Float, default=0.0)
    connections: Mapped[int] = mapped_column(Integer, default=0)
    disk_percent: Mapped[float] = mapped_column(Float, default=0.0)
    disk_free_gb: Mapped[float] = mapped_column(Float, default=0.0)
    disk_read_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    disk_write_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    errors_in: Mapped[int] = mapped_column(Integer, default=0)
    errors_out: Mapped[int] = mapped_column(Integer, default=0)
    drops_in: Mapped[int] = mapped_column(Integer, default=0)
    drops_out: Mapped[int] = mapped_column(Integer, default=0)
    swap_percent: Mapped[float] = mapped_column(Float, default=0.0)
    load_avg: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)


class InterfaceStat(Base):
    """Per-interface network statistics."""

    __tablename__ = "interface_stats"
    id: Mapped[int] = mapped_column(primary_key=True)
    interface_name: Mapped[str] = mapped_column(String, nullable=False)
    upload_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    download_mbps: Mapped[float] = mapped_column(Float, default=0.0)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, index=True)


class QueueItem(Base):
    """Item queued for upload, persisted across restarts."""

    __tablename__ = "queue_items"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False, default="misc")
    itype: Mapped[str] = mapped_column(String, default="")
    name: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


class MutedIssue(Base):
    """A known-issue group the operator has silenced from the default view.

    WHY: the known-issues grouping (get_grouped_upload_errors) has no primary key
    of its own - a group is identified only by (indexer_id, signature), the same
    pair it is grouped by - so muting is keyed on that pair rather than any single
    UploadResult row. A signature stays muted across every future occurrence of
    the same recurring failure until explicitly unmuted.
    """

    __tablename__ = "muted_issues"
    __table_args__ = (UniqueConstraint("indexer_id", "signature", name="uq_muted_issue_indexer_signature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    indexer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    signature: Mapped[str] = mapped_column(String, nullable=False)
    muted_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)


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


# ==============================================================================
#  DATABASE ENGINE & SESSION
# ==============================================================================

_ENGINE = None
_SESSION_FACTORY = None
_lock = threading.Lock()


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        with _lock:
            if _ENGINE is None:
                from core.config import get_config

                conf = get_config()
                db_path = conf.log_db
                db_path.parent.mkdir(parents=True, exist_ok=True)

                # Database URL
                conn_str = f"sqlite:///{db_path}"
                engine = create_engine(
                    conn_str,
                    poolclass=QueuePool,
                    pool_size=10,
                    max_overflow=5,
                    pool_timeout=60,
                    pool_pre_ping=True,
                    connect_args={
                        "timeout": 60,  # Wait up to 60s for locks
                        "check_same_thread": False,  # FastAPI/background threads
                    },
                )

                # PRAGMAs are applied per-connection via the event listener
                # below.  Verify WAL mode is active on the first connection.
                try:
                    with engine.connect() as conn:
                        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
                        if str(mode).lower() != "wal":
                            conn.execute(text("PRAGMA journal_mode=WAL"))
                            conn.commit()
                except Exception:
                    # Don't cache a broken engine instance.
                    engine.dispose()
                    raise

                _ENGINE = engine
    return _ENGINE


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Apply critical PRAGMAs to every new physical SQLite connection.

    * journal_mode=WAL   - allows concurrent readers + one writer
    * busy_timeout=60000  - wait up to 60 s for a write lock (ms)
    * synchronous=NORMAL  - safe for WAL; avoids full fsync per commit
    * foreign_keys=ON     - enforce FK constraints
    * wal_autocheckpoint=100 - checkpoint every 100 pages (≈400 KB)
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA wal_autocheckpoint=1000")  # passive checkpoint every ~4 MB
    cursor.close()


def checkpoint_wal() -> None:
    """Run a TRUNCATE checkpoint to flush and zero-out the WAL file.

    Uses TRUNCATE mode which waits for all active readers to finish,
    writes all WAL pages to the main DB, then truncates the WAL to 0 bytes.
    Safe to call at any time; will log a warning if the WAL cannot be fully
    flushed (e.g. a reader is still active), but will not raise.
    """
    try:
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
            # result = (busy, log, checkpointed)
            if result:
                busy, log, checkpointed = result
                if busy:
                    logger.debug(f"[db] WAL checkpoint: blocked by active reader - {checkpointed}/{log} pages flushed")
                else:
                    logger.debug(f"[db] WAL checkpoint: {checkpointed}/{log} pages flushed, WAL truncated")
    except Exception as exc:
        logger.warning(f"[db] WAL checkpoint failed: {exc}")


def get_session_factory() -> Any:
    global _SESSION_FACTORY
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(bind=get_engine())
    return _SESSION_FACTORY


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ==============================================================================
#  INITIALIZATION & MIGRATION
# ==============================================================================


def init_database() -> bool:
    """Initialize the database and create tables."""
    try:
        engine = get_engine()
        Base.metadata.create_all(engine)

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

            # Refresh columns list for backfill check
            result = conn.execute(text("PRAGMA table_info(uploads)"))
            upload_cols = [row[1] for row in result.fetchall()]

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


# ==============================================================================
#  QUEUE (ITEM-LEVEL UPLOAD QUEUE)
# ==============================================================================


def _normalize_queue_item_path(path: Any) -> str:
    return str(resolve_path(path)) if str(path or "").strip() else ""


@_retry_on_lock()
def db_load_queue() -> List[Dict[str, Any]]:
    """Return all queued items ordered by position then id."""
    with session_scope() as session:
        items = session.execute(select(QueueItem).order_by(QueueItem.position, QueueItem.id)).scalars().all()
        valid: List[QueueItem] = []
        seen_identities: Set[str] = set()
        stale = 0
        duplicate = 0

        for qi in items:
            normalized_path = _normalize_queue_item_path(qi.path)
            identity = path_key(normalized_path)
            if not normalized_path or not Path(normalized_path).exists():
                session.delete(qi)
                stale += 1
                continue
            if not identity or identity in seen_identities:
                session.delete(qi)
                duplicate += 1
                continue
            seen_identities.add(identity)
            if qi.path != normalized_path:
                qi.path = normalized_path
            valid.append(qi)

        if stale:
            logger.info(f"Queue restore: dropped {stale} stale item(s) (path no longer exists)")
        if duplicate:
            logger.info(f"Queue restore: dropped {duplicate} duplicate item(s) with the same path identity")
        if valid:
            logger.info(f"Queue restore: recovered {len(valid)} item(s) from previous session")
        return [
            {
                "id": qi.id,
                "path": qi.path,
                "category": qi.category,
                "itype": qi.itype,
                "name": qi.name,
                "added_at": qi.added_at.isoformat() if qi.added_at else None,
            }
            for qi in valid
        ]


@_retry_on_lock()
def db_add_queue_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add items to the queue; skip duplicate paths. Returns added items as dicts."""
    added: List[Dict[str, Any]] = []
    with session_scope() as session:
        existing_paths: Set[str] = {
            path_key(row[0]) for row in session.execute(select(QueueItem.path)).all()
        }
        max_pos = session.execute(select(func.max(QueueItem.position))).scalar() or 0
        position = max_pos + 1
        for item in items:
            path = _normalize_queue_item_path(item.get("path", ""))
            path_identity = path_key(path)
            if not path or not path_identity or path_identity in existing_paths:
                continue
            qi = QueueItem(
                path=path,
                category=item.get("category", "misc"),
                itype=item.get("itype", "") or "",
                name=item.get("name", "") or Path(path).name,
                position=position,
            )
            session.add(qi)
            session.flush()  # populate qi.id
            existing_paths.add(path_identity)
            added.append(
                {
                    "id": qi.id,
                    "path": qi.path,
                    "category": qi.category,
                    "itype": qi.itype,
                    "name": qi.name,
                    "added_at": qi.added_at.isoformat() if qi.added_at else None,
                }
            )
            position += 1
    return added


@_retry_on_lock()
def db_remove_queue_item(item_id: int) -> bool:
    """Remove a single item from the queue by id."""
    with session_scope() as session:
        qi = session.get(QueueItem, item_id)
        if qi is None:
            return False
        session.delete(qi)
    return True


@_retry_on_lock()
def db_remove_queue_items(item_ids: List[int]) -> int:
    """Remove multiple queued items by id and return the deleted count."""
    normalized_ids = [int(item_id) for item_id in item_ids if item_id is not None]
    if not normalized_ids:
        return 0

    with session_scope() as session:
        count = (
            session.execute(
                select(func.count()).select_from(QueueItem).where(QueueItem.id.in_(normalized_ids))
            ).scalar()
            or 0
        )
        if count:
            session.execute(delete(QueueItem).where(QueueItem.id.in_(normalized_ids)))
    return int(count)


@_retry_on_lock()
def db_clear_queue() -> int:
    """Remove all items from the queue. Returns count removed."""
    with session_scope() as session:
        count = session.execute(select(func.count()).select_from(QueueItem)).scalar() or 0
        session.execute(delete(QueueItem))
    return int(count)


@_retry_on_lock()
def db_reorder_queue(item_ids: List[int]) -> bool:
    """Update position of each item per the provided ordered list of IDs."""
    with session_scope() as session:
        rows = session.execute(select(QueueItem)).scalars().all()
        if len(rows) != len(item_ids):
            return False
        id_to_obj = {qi.id: qi for qi in rows}
        if set(item_ids) != set(id_to_obj.keys()):
            return False
        for pos, iid in enumerate(item_ids):
            id_to_obj[iid].position = pos
    return True


def get_database_health() -> Dict[str, Any]:
    """Get comprehensive database health and statistics."""
    from core.config import get_config

    conf = get_config()
    db_path = conf.log_db
    result: Dict[str, Any] = {
        "status": "error",
        "path": str(db_path),
        "exists": db_path.exists(),
        "size_bytes": 0,
        "tables": {},
        "recent_activity": {},
        "errors": [],
    }

    try:
        if not db_path.exists():
            result["errors"].append("Database file does not exist")
            return result

        result["size_bytes"] = db_path.stat().st_size

        with session_scope() as session:
            # Check each table
            tables = {
                "uploads": Upload,
                "upload_results": UploadResult,
                "job_history": JobHistory,
                "system_stats": SystemStat,
                "interface_stats": InterfaceStat,
            }

            for name, model in tables.items():
                try:
                    count = session.query(model).count()
                    result["tables"][name] = {"count": count, "status": "ok"}
                except Exception as e:
                    result["tables"][name] = {
                        "count": 0,
                        "status": "error",
                        "error": str(e),
                    }
                    result["errors"].append(f"Table {name}: {e}")

            # Check recent system_stats activity
            try:
                latest = session.query(SystemStat).order_by(desc(SystemStat.recorded_at)).first()
                if latest:
                    result["recent_activity"]["system_stats"] = {
                        "last_recorded": latest.recorded_at.isoformat(),
                        "cpu": latest.cpu_percent,
                        "memory": latest.memory_percent,
                    }
            except Exception as e:
                result["errors"].append(f"Recent activity check: {e}")

            # Check interface_stats
            try:
                iface_count = session.query(InterfaceStat).count()
                iface_latest = session.query(InterfaceStat).order_by(desc(InterfaceStat.recorded_at)).first()
                result["recent_activity"]["interface_stats"] = {
                    "total_records": iface_count,
                    "last_recorded": iface_latest.recorded_at.isoformat() if iface_latest else None,
                }
            except Exception as e:
                result["errors"].append(f"Interface stats check: {e}")

        result["status"] = "healthy" if not result["errors"] else "degraded"

    except Exception as e:
        result["errors"].append(f"Database connection error: {e}")
        result["status"] = "error"

    return result


# ==============================================================================
#  CORE OPERATIONS
# ==============================================================================


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
        with session_scope() as session:
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
    """Update the database with upload results for a specific destination."""
    refresh_needed = False
    try:
        with session_scope() as session:
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
            refresh_needed = result.status == "success"

            legacy_columns = {"geek": "uploaded_at_geek", "in": "uploaded_at_in", "omg": "uploaded_at_omg"}
            legacy_column = legacy_columns.get(dest)
            if legacy_column and result.status == "success":
                upload_columns = {row[1] for row in session.execute(text("PRAGMA table_info(uploads)")).fetchall()}
                if legacy_column in upload_columns:
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

        if refresh_needed:
            from logic.queue_metrics import request_live_queue_refresh

            request_live_queue_refresh(reason="upload-success")
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
        with session_scope() as session:
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
            # Always emit microseconds so the text matches the ORM's stored format.
            target_str = target.strftime("%Y-%m-%d %H:%M:%S.%f")
            # Use raw SQL so ORM datetime serialization cannot change the format
            session.execute(
                text("UPDATE uploads SET updated_at = :ts WHERE id = :id AND updated_at != :ts"),
                {"ts": target_str, "id": folder.id},
            )
    except Exception as e:
        logger.debug(f"pin_folder_ts_to_children non-fatal for {folder_key!r}: {e}")


def check_duplicate_dynamic(item_key: str, _itype: str, indexer_ids: List[str], filesize: Optional[int] = None) -> Dict[str, Optional[str]]:
    """Check which indexers already have this item."""
    results: Dict[str, Optional[str]] = {idx: None for idx in indexer_ids}
    try:
        with session_scope() as session:
            basename = item_key.rsplit("/", 1)[-1]
            uploads = _load_duplicate_upload_payloads(
                session,
                exact_names=[item_key, basename],
                suffix_names=[basename],
            )

            for upload in uploads:
                stored_name = upload.get("item_name", "")
                stored_size = upload.get("filesize")
                if filesize is not None and stored_name != item_key:
                    if stored_size is None:
                        continue  # size unknown -- skip basename/suffix matches without size
                if filesize is not None and stored_size is not None:
                    try:
                        stored_size_int = int(stored_size)
                    except (TypeError, ValueError):
                        stored_size_int = None
                    try:
                        expected_size_int = int(filesize)
                    except (TypeError, ValueError):
                        expected_size_int = None
                    if (
                        stored_size_int is not None
                        and expected_size_int is not None
                        and stored_size_int != expected_size_int
                    ):
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


def get_duplicate_status_batch(
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
    already used by check_duplicate_dynamic() for single-item lookups.

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
        with session_scope() as session:
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
                if current_size is not None:
                    stored_size = payload.get("filesize") if isinstance(payload, dict) else None
                    if stored_size is None:
                        # Size unknown for this record -- only trust it when it's an
                        # exact key match; a basename/suffix match with no recorded
                        # size is too weak to treat as a confirmed duplicate here.
                        if str(payload.get("item_name", "")) != key:
                            continue
                    else:
                        try:
                            stored_size_int = int(stored_size)
                            current_size_int = int(current_size)
                        except (TypeError, ValueError):
                            stored_size_int = current_size_int = None
                        if (
                            stored_size_int is not None
                            and current_size_int is not None
                            and stored_size_int != current_size_int
                        ):
                            continue  # recorded upload was a different-sized file -- not a duplicate of the current one

                payload_results = payload.get("results", {}) if isinstance(payload, dict) else {}
                for indexer_id, uploaded_at in payload_results.items():
                    if indexer_id in id_set and row[indexer_id] is None:
                        row[indexer_id] = uploaded_at
    except Exception as e:
        logger.error(f"Batch duplicate check failed for {len(unique_keys)} items: {e}")
        raise DatabaseOperationalError(f"Batch duplicate check failed for {len(unique_keys)} items") from e

    return results


def get_dashboard_data(
    active_ids: List[str],
) -> Tuple[Set[str], Dict[str, Set[str]], Dict[str, Dict[str, str]], Dict[str, Dict[str, int]]]:
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
        return set(), {}, {}, {}

    started = time.perf_counter()
    try:
        with session_scope() as session:
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
                "get_dashboard_data",
                started,
                context=f"active_ids={len(active_ids)} mapped_items={len(mapping)} fully_done={len(fully_done)} failed={len(failed_map)}",
                warn_threshold_s=0.75,
            )
            return fully_done, mapping, failed_map, filesize_by_indexer
    except Exception as e:
        logger.error(f"Failed to fetch dashboard data: {e}")
        raise DatabaseOperationalError("Failed to fetch dashboard data") from e


# ==============================================================================
#  STATS OPERATIONS
# ==============================================================================


@_retry_on_lock(max_retries=4, base_delay=0.5)
def record_system_stats(**data: Any) -> None:
    """Record historical system metrics (retries on transient locks)."""
    try:
        with session_scope() as session:
            stat = SystemStat(
                cpu_percent=data.get("cpu"),
                memory_percent=data.get("mem"),
                upload_mbps=data.get("up"),
                download_mbps=data.get("down"),
                total_sent_mb=data.get("total_sent"),
                total_recv_mb=data.get("total_recv"),
                connections=data.get("connections"),
                disk_percent=data.get("disk_percent"),
                disk_free_gb=data.get("disk_free_gb"),
                disk_read_mbps=data.get("disk_read"),
                disk_write_mbps=data.get("disk_write"),
                errors_in=data.get("errors_in"),
                errors_out=data.get("errors_out"),
                drops_in=data.get("drops_in"),
                drops_out=data.get("drops_out"),
                swap_percent=data.get("swap_percent"),
                load_avg=data.get("load_avg"),
            )
            session.add(stat)
    except Exception as e:
        logger.error(f"Failed to record system stats: {e}")


@_retry_on_lock(max_retries=4, base_delay=0.5)
def record_system_stats_batch(rows: List[Dict[str, Any]]) -> None:
    """Record multiple system-stat snapshots in one transaction."""
    if not rows:
        return

    try:
        with session_scope() as session:
            for data in rows:
                session.add(
                    SystemStat(
                        cpu_percent=data.get("cpu"),
                        memory_percent=data.get("mem"),
                        upload_mbps=data.get("up"),
                        download_mbps=data.get("down"),
                        total_sent_mb=data.get("total_sent"),
                        total_recv_mb=data.get("total_recv"),
                        connections=data.get("connections"),
                        disk_percent=data.get("disk_percent"),
                        disk_free_gb=data.get("disk_free_gb"),
                        disk_read_mbps=data.get("disk_read"),
                        disk_write_mbps=data.get("disk_write"),
                        errors_in=data.get("errors_in"),
                        errors_out=data.get("errors_out"),
                        drops_in=data.get("drops_in"),
                        drops_out=data.get("drops_out"),
                        swap_percent=data.get("swap_percent"),
                        load_avg=data.get("load_avg"),
                    )
                )
    except Exception as e:
        logger.error(f"Failed to record system stats batch: {e}")


@_retry_on_lock(max_retries=4, base_delay=0.5)
def record_interface_stats(iface_data: List[Dict[str, Any]]) -> bool:
    """Record per-interface metrics (retries on transient locks)."""
    try:
        with session_scope() as session:
            for item in iface_data:
                stat = InterfaceStat(
                    interface_name=item["name"],
                    upload_mbps=item["upload"],
                    download_mbps=item["download"],
                )
                session.add(stat)
            return True
    except Exception as e:
        logger.error(f"Failed to record interface stats: {e}")
        return False


@_retry_on_lock(max_retries=3, base_delay=1.0)
def prune_system_stats(max_records: int = 1000) -> None:
    """Keep the stats tables lean (retries on transient locks)."""
    try:
        with session_scope() as session:
            # Delete older system_stats
            sub = select(SystemStat.id).order_by(desc(SystemStat.recorded_at)).offset(max_records)
            session.execute(delete(SystemStat).where(SystemStat.id.in_(sub.scalar_subquery())))
        # Use a separate transaction for interface pruning to hold the
        # write lock for shorter bursts.
        with session_scope() as session:
            session.execute(
                delete(InterfaceStat).where(InterfaceStat.recorded_at < datetime.now(timezone.utc) - timedelta(hours=2))
            )
    except Exception as e:
        logger.error(f"Failed to prune stats: {e}")


# ==============================================================================
#  JOB HISTORY
# ==============================================================================


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


# ==============================================================================
#  DASHBOARD DATA FETCHERS
# ==============================================================================


def _serialize_stats_resource_series(stats: List[Any]) -> Dict[str, List[Any]]:
    return {
        "cpu": [s.cpu_percent or 0 for s in stats],
        "load": [s.load_avg or 0 for s in stats],
        "memory": [s.memory_percent or 0 for s in stats],
        "swap": [s.swap_percent or 0 for s in stats],
        "disk": [s.disk_percent or 0 for s in stats],
        "free": [s.disk_free_gb or 0 for s in stats],
        "disk_read": [s.disk_read_mbps or 0 for s in stats],
        "disk_write": [s.disk_write_mbps or 0 for s in stats],
    }


def _serialize_stats_network_series(stats: List[Any]) -> Dict[str, List[Any]]:
    return {
        "upload_mbps": [s.upload_mbps or 0 for s in stats],
        "download_mbps": [s.download_mbps or 0 for s in stats],
        "total_sent_mb": [s.total_sent_mb or 0 for s in stats],
        "total_recv_mb": [s.total_recv_mb or 0 for s in stats],
        "connections": [s.connections or 0 for s in stats],
        # Errors
        "network_errors": [
            (s.errors_in or 0) + (s.errors_out or 0) + (s.drops_in or 0) + (s.drops_out or 0) for s in stats
        ],
        # Timestamps
        "recorded_at": [s.recorded_at.isoformat() for s in stats],
    }


def _serialize_stats_history_rows(stats: List[Any]) -> Dict[str, List[Any]]:
    return {
        **_serialize_stats_resource_series(stats),
        **_serialize_stats_network_series(stats),
    }


def get_system_stats_history(limit: int = 100) -> Dict[str, List[Any]]:
    """Retrieve history for dashboard charts."""
    try:
        with session_scope() as session:
            stats = session.query(SystemStat).order_by(desc(SystemStat.recorded_at)).limit(limit).all()
            stats.reverse()
            return _serialize_stats_history_rows(stats)
    except Exception as e:
        logger.error(f"Failed to fetch system stats history: {e}")
        return _empty_stats_history()


def get_interface_stats_history(limit: int = 100) -> Dict[str, Dict[str, List[Any]]]:
    """Retrieve per-interface history."""
    results: Dict[str, Dict[str, List[Any]]] = {}
    try:
        with session_scope() as session:
            ifaces = session.query(InterfaceStat.interface_name).distinct().all()
            for (if_name,) in ifaces:
                stats = (
                    session.query(InterfaceStat)
                    .filter_by(interface_name=if_name)
                    .order_by(desc(InterfaceStat.recorded_at))
                    .limit(limit)
                    .all()
                )
                stats.reverse()
                results[if_name] = {
                    "upload": cast(List[Any], [s.upload_mbps for s in stats]),
                    "download": cast(List[Any], [s.download_mbps for s in stats]),
                    "recorded_at": cast(List[Any], [s.recorded_at.isoformat() for s in stats]),
                }
    except Exception as e:
        logger.error(f"Failed to fetch interface history: {e}")
    return results


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
        with session_scope() as session:
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
        with session_scope() as session:
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
                    dominant_type = max(type_counts, key=type_counts.get)  # type: ignore[arg-type]

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
        with session_scope() as session:
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
        with session_scope() as session:
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
        with session_scope() as session:
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
        with session_scope() as session:
            existing = session.execute(
                select(MutedIssue).filter_by(indexer_id=indexer_id, signature=signature)
            ).scalar_one_or_none()
            if existing is not None:
                session.delete(existing)
            return True
    except Exception as e:
        logger.error(f"Failed to unmute issue ({indexer_id}, {signature}): {e}")
        raise DatabaseOperationalError("Failed to unmute issue") from e


def get_uploads_for_job(job_id: str) -> List[Dict[str, Any]]:
    """Fetch individual items updated within a job's timeframe."""
    try:
        with session_scope() as session:
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


def delete_upload_item(name: str) -> bool:
    """Delete an item from history."""
    try:
        with session_scope() as session:
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
        with session_scope() as session:
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


# ==============================================================================
#  INTERNAL HELPERS
# ==============================================================================


def _empty_stats_history() -> Dict[str, List[Any]]:
    """Return empty structure matching get_system_stats_history output."""
    return {
        "cpu": [],
        "load": [],
        "memory": [],
        "swap": [],
        "disk": [],
        "free": [],
        "disk_read": [],
        "disk_write": [],
        "upload_mbps": [],
        "download_mbps": [],
        "total_sent_mb": [],
        "total_recv_mb": [],
        "connections": [],
        "network_errors": [],
        "recorded_at": [],
    }


def _serialize_upload(upload: Upload) -> Dict[str, Any]:
    """Convert an Upload ORM model into a flat dictionary for the API."""
    from core.registry import get_registry

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


def get_hourly_upload_stats() -> Dict[str, Any]:
    """Fetch aggregate hourly stats for the last 24 hours."""
    try:
        with session_scope() as session:
            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            cutoff_24h = now - timedelta(hours=24)

            # Get counts for the last hour by category
            last_hour_stats = (
                session.query(Upload.itype, func.count(UploadResult.id))
                .join(UploadResult)
                .filter(UploadResult.uploaded_at >= one_hour_ago)
                .filter(UploadResult.status == "success")
                .group_by(Upload.itype)
                .all()
            )

            tv_last_hour = 0
            movies_last_hour = 0
            for itype, count in last_hour_stats:
                itype_lower = itype.lower() if itype else ""
                if "tv" in itype_lower or "episode" in itype_lower:
                    tv_last_hour += count
                elif "movie" in itype_lower:
                    movies_last_hour += count

            # Get average time (duration) per upload in last 24h
            avg_time = (
                session.query(func.avg(UploadResult.duration))
                .filter(
                    UploadResult.uploaded_at >= cutoff_24h,
                    UploadResult.status == "success",
                )
                .scalar()
            ) or 0

            # Count ALL completed results (success + failed) so the frontend
            # change-detector fires for failures too, not just successes.
            total_today = (
                session.query(func.count(UploadResult.id))
                .filter(UploadResult.uploaded_at >= cutoff_24h)
                .scalar()
            ) or 0

            # Latest upload activity - catches any status change (success, fail, retry).
            latest_ts_val = (
                session.query(func.max(Upload.updated_at))
                .filter(Upload.updated_at >= cutoff_24h)
                .scalar()
            )
            latest_ts = latest_ts_val.isoformat() if latest_ts_val else None

            # Get hourly distribution for charts (legacy support)
            data = (
                session.query(
                    func.strftime("%Y-%m-%d %H:00:00", UploadResult.uploaded_at).label("hour"),
                    func.count(UploadResult.id).label("count"),
                    func.avg(UploadResult.speed_bps).label("speed"),
                )
                .filter(
                    UploadResult.uploaded_at >= cutoff_24h,
                    UploadResult.status == "success",
                )
                .group_by("hour")
                .all()
            )

            return {
                "tv_last_hour": tv_last_hour,
                "movies_last_hour": movies_last_hour,
                "total_today": int(total_today),
                "latest_ts": latest_ts,
                "avg_time_per_upload": round(float(avg_time), 1) if avg_time else 0,
                "hours": [row.hour for row in data],
                "counts": [row.count for row in data],
                "speeds": [row.speed for row in data],
            }
    except Exception as e:
        logger.error(f"Hourly stats failed: {e}")
        return {
            "tv_last_hour": 0,
            "movies_last_hour": 0,
            "total_today": 0,
            "latest_ts": None,
            "avg_time_per_upload": 0,
            "hours": [],
            "counts": [],
            "speeds": [],
        }


def get_detailed_stats() -> Dict[str, Any]:
    """Summary stats for the UI - returns full structure expected by dashboard."""
    started = time.perf_counter()
    try:
        with session_scope() as session:
            count = session.query(func.count(Upload.id)).scalar() or 0
            bytes_total = session.query(func.sum(Upload.filesize)).scalar() or 0

            # Count by type (movies/tv/misc) - handle various naming conventions
            by_category: Dict[str, int] = {
                "movies": 0,
                "tv": 0,
                "misc": 0,
                "episodes": 0,
            }
            type_counts = session.query(Upload.itype, func.count(Upload.id)).group_by(Upload.itype).all()
            for itype, cnt in type_counts:
                if itype is None:
                    continue  # Skip null types
                itype_lower = itype.lower() if itype else ""
                if itype_lower in ("movie", "movies"):
                    by_category["movies"] += cnt
                elif itype_lower in ("tv", "tv show", "tv pack"):
                    by_category["tv"] += cnt
                elif itype_lower in ("episode", "tv episode"):
                    by_category["episodes"] += cnt
                elif itype_lower == "misc":
                    by_category["misc"] += cnt

            # Count by destination/indexer
            by_destination: Dict[str, Dict[str, int]] = {}
            dest_counts = (
                session.query(
                    UploadResult.indexer_id,
                    UploadResult.status,
                    func.count(UploadResult.id),
                )
                .group_by(UploadResult.indexer_id, UploadResult.status)
                .all()
            )
            for indexer_id, status, cnt in dest_counts:
                if indexer_id not in by_destination:
                    by_destination[indexer_id] = {"success": 0, "failed": 0}

                if status == "failed":
                    by_destination[indexer_id]["failed"] += cnt
                else:
                    by_destination[indexer_id]["success"] += cnt

            # Last 24h stats
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            today_count = (
                session.query(func.count(UploadResult.id))
                .filter(UploadResult.uploaded_at >= cutoff, UploadResult.status == "success")
                .scalar()
            ) or 0

            # Average speed in last 24h
            avg_speed = (
                session.query(func.avg(UploadResult.speed_bps))
                .filter(UploadResult.uploaded_at >= cutoff, UploadResult.status == "success")
                .scalar()
            ) or 0

            result = {
                "total_items": count,
                "total_bytes": bytes_total,
                "uploads": {
                    "total": count,
                    "by_category": by_category,
                    "by_destination": by_destination,
                    "today": today_count,
                },
                "performance": {
                    "avg_speed_bps": avg_speed,
                    "avg_speed_mbps": (avg_speed / 1_000_000) if avg_speed else 0,
                    "gb_per_hour": round(avg_speed * 3600 / 1_073_741_824, 2) if avg_speed else 0,
                },
            }
            _log_db_timing(
                "get_detailed_stats",
                started,
                context=(f"total_items={count} destinations={len(by_destination)} category_rows={len(type_counts)}"),
                warn_threshold_s=0.5,
            )
            return result
    except Exception as e:
        logger.error(f"Detailed stats failed: {e}")
        return {
            "total_items": 0,
            "total_bytes": 0,
            "uploads": {
                "total": 0,
                "by_category": {},
                "by_destination": {},
                "today": 0,
            },
            "performance": {"avg_speed_bps": 0, "avg_speed_mbps": 0, "gb_per_hour": 0},
        }


def get_all_upload_stats() -> Dict[str, int]:
    """Retrieve aggregate success counts per indexer."""
    try:
        with session_scope() as session:
            rows = (
                session.query(UploadResult.indexer_id, func.count(UploadResult.id))
                .group_by(UploadResult.indexer_id)
                .all()
            )
            return {f"{idx}_count": count for idx, count in rows}
    except Exception as e:
        logger.error(f"Failed to get all upload stats: {e}")
        return {}


def mark_as_uploaded(
    item_keys: List[str],
    indexer_ids: List[str],
    itype: str = "Misc",
) -> int:
    """Mark specific items as uploaded to specific indexers (manual override).

    Creates Upload + UploadResult records so duplicate checkers skip them.
    Returns the number of new result records created.
    """
    created = 0
    try:
        with session_scope() as session:
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
        if created > 0:
            from logic.queue_metrics import request_live_queue_refresh

            request_live_queue_refresh(reason="manual-mark-uploaded")
    except Exception as e:
        logger.error(f"mark_as_uploaded failed: {e}")
    return created


def get_top_directories(limit: int = 25) -> Dict[str, Any]:
    """Retrieve top directories by scanning base folder and categorizing usage."""
    try:
        import os
        from pathlib import Path

        from core.config import get_config

        conf = get_config()
        # The user wants to see the base folder (e.g. 0--Usenet or parent of project)
        base_path = conf.base_folder
        if not base_path or not base_path.exists():
            base_path = Path(conf.script_dir).parent

        targets = []
        for fp in getattr(conf, "folder_paths", []):
            if not isinstance(fp, dict):
                continue
            path = str(fp.get("path", "") or "").strip()
            if not path:
                continue
            resolved = Path(path)
            if resolved.exists() and resolved.is_dir():
                targets.append(resolved)

        # If categories don't exist or aren't set, use base_path
        if not targets:
            targets.append(base_path)

        # Dictionary to store results: {key: {name, size, files, path}}
        usage: Dict[str, Dict[str, Any]] = {}

        # 1. PHYSICAL SCAN FIRST (Identify REAL directories)
        for target in targets:
            try:
                with os.scandir(str(target)) as it:
                    for entry in it:
                        # Skip hidden files
                        if entry.name.startswith("."):
                            continue

                        # Use directory name as key
                        if entry.is_dir():
                            name = entry.name
                            usage[name] = {
                                "name": name,
                                "size": 0,
                                "files": 0,
                                "path": str(entry.path),
                            }
            except Exception:
                continue

        # 2. DATABASE SEED (Aggregate historical sizes)
        try:
            with session_scope() as session:
                stmt = select(Upload.item_name, Upload.filesize)
                db_results = session.execute(stmt).all()
                for item_name, size in db_results:
                    # Normalize separators for Windows/Linux consistency
                    item_name = item_name.replace("\\", "/")
                    parts = item_name.split("/")

                    # If it's "Folder/File.mkv", key is "Folder"
                    # If it's just "File.mkv", key is "File.mkv" (loose file)
                    key = parts[0]

                    # Size handling (DB values can be float/int/str)
                    try:
                        size_val = int(float(size or 0))
                    except (ValueError, TypeError):
                        size_val = 0

                    if key in usage:
                        usage[key]["size"] = cast(int, usage[key]["size"]) + size_val
                        usage[key]["files"] = cast(int, usage[key]["files"]) + 1
                    else:
                        # It's a file tracked in DB but maybe not locally in a folder
                        # We only show it if it's a significant "top level" entry
                        usage[key] = {
                            "name": key,
                            "size": size_val,
                            "files": 1,
                            "path": "Remote/Historical",
                        }
        except Exception as db_e:
            logger.warning(f"DB aggregation failed: {db_e}")

        # Final list: Sort and filter
        final_list = [v for v in usage.values() if cast(int, v["size"]) > 0]
        sorted_usage = sorted(final_list, key=lambda x: cast(int, x["size"]), reverse=True)[:limit]

        return {"directories": sorted_usage}
    except Exception as e:
        logger.error(f"Failed to get top directories: {e}")
        return {"directories": []}
