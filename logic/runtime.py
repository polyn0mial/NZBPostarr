"""Process runtime: the ordered start and stop of the long-lived services, and the job engine.

The WebUI lifespan calls start()/stop(); the headless CLI calls init_core(). The job engine is
built on first use and starts its scheduler only through ensure_engine_started(), which runs
where the first request (or the boot reaper scan) first needs the engine.
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from core.config import get_config
from core.logging import console
from logic.jobs.engine import JobEngine

_engine: Optional[JobEngine] = None
_engine_lock = threading.Lock()
_boot_reaper_task: Optional[asyncio.Task[Any]] = None


def get_engine() -> JobEngine:
    """The process's job engine; building it restores persisted jobs but starts no thread."""
    global _engine

    with _engine_lock:
        if _engine is None:
            _engine = JobEngine()
        return _engine


def ensure_engine_started() -> JobEngine:
    """The job engine with its scheduler running (started once, on first use)."""
    engine = get_engine()
    engine.start()
    return engine


def init_core() -> None:
    """Shared initialization for both WebUI and headless modes.

    Installs the web-terminal log buffer, initializes the database and the indexer
    registry, and schedules a one-shot cleanup of stale tmp data.
    """
    from core.database import init_database
    from core.registry import get_registry
    from core.utils import run_global_purge
    from logic.process_reaper import get_scheduler

    console.install()

    logger.info("Initializing database...")
    init_database()

    logger.info("Loading indexer registry...")
    get_registry()

    # Clean up stale tmp data via scheduler (one-shot, runs once at startup)
    sched = get_scheduler()
    sched.add_job(run_global_purge, id="startup_purge", replace_existing=True)


async def sync_stats_collector(conf: Optional[Any] = None) -> bool:
    """Start or stop the stats history collector to match the config; True when it runs."""
    from logic.stats_engine import history_tracking_enabled, start_collector, stop_collector, sync_collector_schedule

    current = conf or get_config()
    required = bool(history_tracking_enabled(current))
    if required:
        await start_collector()
    else:
        await stop_collector()
    sync_collector_schedule()
    return required


async def start(
    *,
    pending_scan: Callable[[], dict[str, Any]],
    pending_watch_folders: Callable[[Any], list[Path]],
) -> None:
    """Start the WebUI's services in order: core, stats collector, folder and stream monitors,
    the pending index, then the process reaper."""
    from logic import usenet_stream
    from logic.autoupload import start_folder_monitor
    from logic.pending.completion import prewarm_pending_indexer_context
    from logic.pending.index import get_pending_index_manager

    begin = time.time()
    init_core()
    conf = get_config()
    logger.info(f"  DB path: {conf.log_db}")
    logger.debug(f"  [1/2] Core systems ready ({time.time() - begin:.3f}s)")

    if await sync_stats_collector(conf):
        logger.debug(f"  [2/2] Stats Collector started ({time.time() - begin:.3f}s)")
    else:
        logger.debug(f"  [2/2] Stats Collector skipped ({time.time() - begin:.3f}s)")

    # Folder Monitor (experimental) - auto-upload on new content
    await start_folder_monitor()
    logger.debug(f"  [3/3] Folder Monitor checked ({time.time() - begin:.3f}s)")

    await usenet_stream.start_stream_monitors()
    logger.debug(f"  [3.25/4] Stream Monitor checked ({time.time() - begin:.3f}s)")

    # Pending index manager (request-path offload): watcher invalidation + periodic reconcile.
    pending_index = get_pending_index_manager()
    pending_index.configure(pending_scan)
    pending_index.start(pending_watch_folders(conf))
    logger.debug(f"  [3.5/4] Pending index manager started ({time.time() - begin:.3f}s)")

    # Warm the pending tree's indexer context off the request path (daemon thread).
    prewarm_pending_indexer_context()

    # Process reaper - periodic cleanup of hung/orphaned tool processes
    _arm_process_reaper()
    logger.debug(f"  [4/4] Process Reaper armed ({time.time() - begin:.3f}s)")


async def stop() -> None:
    """Stop what start() started, in reverse, then flush the database WAL."""
    from core.database import checkpoint_wal
    from logic import usenet_stream
    from logic.autoupload import stop_folder_monitor
    from logic.pending.index import get_pending_index_manager
    from logic.process_reaper import shutdown_scheduler
    from logic.stats_engine import stop_collector

    await _stop_startup_reaper()
    get_pending_index_manager().stop()
    await usenet_stream.stop_stream_monitors()
    await stop_folder_monitor()
    await stop_collector()
    if _engine is not None:
        _engine.stop()
    checkpoint_wal()  # flush WAL before process exits
    shutdown_scheduler()


def _arm_process_reaper() -> None:
    """Start periodic cleanup immediately and offload the boot scan to the background."""
    global _boot_reaper_task

    from logic.process_reaper import schedule_reaper, schedule_wal_checkpoint

    schedule_reaper()
    schedule_wal_checkpoint()

    if _boot_reaper_task is not None and not _boot_reaper_task.done():
        _boot_reaper_task.cancel()

    _boot_reaper_task = asyncio.create_task(_run_startup_reaper(), name="startup-process-reaper")


async def _stop_startup_reaper() -> None:
    """Cancel or drain the background boot reaper task during shutdown."""
    global _boot_reaper_task

    task = _boot_reaper_task
    _boot_reaper_task = None
    if task is None:
        return

    if not task.done():
        task.cancel()

    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _run_startup_reaper() -> None:
    """Run the initial orphan-process scan without blocking app startup."""
    from logic.process_reaper import reap_orphans

    try:
        result = await asyncio.to_thread(reap_orphans, force=True)
        logger.debug(
            "[startup] Boot reaper scan finished "
            f"(stale={result.get('stale_found', 0)} killed={result.get('killed', 0)} failed={result.get('failed', 0)})"
        )
    except asyncio.CancelledError:
        logger.debug("[startup] Boot reaper scan cancelled")
        raise
    except Exception as exc:
        logger.error(f"[startup] Boot reaper scan failed: {exc}")
