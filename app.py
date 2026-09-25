"""NZBPostarr web application: the composition root."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Any, AsyncGenerator, Optional

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from api import ROUTERS
from api.assets import ASSETS_DIR, add_cache_control_header
from api.auth import session_auth_middleware
from api.mcp import mount_mcp_endpoint
from api.deps import _stats_collector_required, _sync_stats_collector_state
from api.pages import not_found_exception_handler, server_error_exception_handler
from api.pending import _pending_index, _pending_watch_folders, _scan_pending_all
from core.config import get_config
from logic import usenet_stream


_boot_reaper_task: Optional[asyncio.Task[Any]] = None

_MCP_ASGI_APP: Any = None

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

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    start = time.time()
    logger.info("🚀 WebUI Initializing...")

    # Shared init: database, indexer registry, console buffer, tmp cleanup
    from logic.services import init_app

    init_app()
    conf = get_config()

    logger.info(f"  DB path: {conf.log_db}")
    logger.debug(f"  [1/2] Core systems ready ({time.time() - start:.3f}s)")

    await _sync_stats_collector_state(conf)
    if _stats_collector_required(conf):
        logger.debug(f"  [2/2] Stats Collector started ({time.time() - start:.3f}s)")
    else:
        logger.debug(f"  [2/2] Stats Collector skipped ({time.time() - start:.3f}s)")

    # Folder Monitor (experimental) - auto-upload on new content
    from logic.autoupload import start_folder_monitor

    await start_folder_monitor()
    logger.debug(f"  [3/3] Folder Monitor checked ({time.time() - start:.3f}s)")

    await usenet_stream.start_stream_monitors()
    logger.debug(f"  [3.25/4] Stream Monitor checked ({time.time() - start:.3f}s)")

    # Pending index manager (request-path offload): watcher invalidation + periodic reconcile.
    _pending_index.configure(_scan_pending_all)
    _pending_index.start(_pending_watch_folders(conf))
    logger.debug(f"  [3.5/4] Pending index manager started ({time.time() - start:.3f}s)")

    # Warm the pending tree's indexer context off the request path (daemon thread).
    from logic.pending.completion import prewarm_pending_indexer_context

    prewarm_pending_indexer_context()

    # Process reaper - periodic cleanup of hung/orphaned tool processes
    _arm_process_reaper()
    logger.debug(f"  [4/4] Process Reaper armed ({time.time() - start:.3f}s)")

    logger.info(f"✨ Startup complete in {time.time() - start:.3f}s")

    # A mounted sub-app does not get its lifespan run by the parent, and the MCP
    # streamable-HTTP handler needs its session manager task group started or
    # every request fails with "Task group is not initialized".
    async with AsyncExitStack() as _mcp_stack:
        if _MCP_ASGI_APP is not None:
            await _mcp_stack.enter_async_context(_MCP_ASGI_APP.router.lifespan_context(_MCP_ASGI_APP))
        yield

    from core.database import checkpoint_wal
    from logic.autoupload import stop_folder_monitor
    from core.scheduler import shutdown_scheduler
    from logic.stats_engine import stop_collector

    await _stop_startup_reaper()
    _pending_index.stop()
    await usenet_stream.stop_stream_monitors()
    await stop_folder_monitor()
    await stop_collector()
    checkpoint_wal()  # flush WAL before process exits
    shutdown_scheduler()

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


app = FastAPI(title="NZBPostarr", lifespan=lifespan, docs_url="/swagger", redoc_url=None)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(BaseHTTPMiddleware, dispatch=add_cache_control_header)
app.add_middleware(BaseHTTPMiddleware, dispatch=session_auth_middleware)

if ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(ASSETS_DIR)), name="assets")

for _router in ROUTERS:
    app.include_router(_router)

_MCP_ASGI_APP = mount_mcp_endpoint(app)

app.add_exception_handler(404, not_found_exception_handler)
app.add_exception_handler(Exception, server_error_exception_handler)
app.add_exception_handler(500, server_error_exception_handler)
