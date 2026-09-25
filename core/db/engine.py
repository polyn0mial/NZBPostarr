"""The SQLite engine: pool, pragmas, sessions, lock retry, WAL checkpoint and health."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Dict, Generator, Optional, TypeVar

from loguru import logger
from sqlalchemy import create_engine, desc, Engine, event, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from core.db.models import InterfaceStat, JobHistory, SystemStat, Upload, UploadResult
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
