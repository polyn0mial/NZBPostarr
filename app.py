"""NZBPostarr web application: the composition root."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager, AsyncExitStack
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from api import ROUTERS
from api.assets import ASSETS_DIR, add_cache_control_header
from api.auth import session_auth_middleware
from api.mcp import mount_mcp_endpoint
from api.pages import not_found_exception_handler, server_error_exception_handler
from api.pending import _pending_watch_folders, _scan_pending_all
from logic import runtime


_MCP_ASGI_APP: Any = None

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    start = time.time()
    logger.info("🚀 WebUI Initializing...")

    await runtime.start(pending_scan=_scan_pending_all, pending_watch_folders=_pending_watch_folders)

    logger.info(f"✨ Startup complete in {time.time() - start:.3f}s")

    # A mounted sub-app does not get its lifespan run by the parent, and the MCP
    # streamable-HTTP handler needs its session manager task group started or
    # every request fails with "Task group is not initialized".
    async with AsyncExitStack() as _mcp_stack:
        if _MCP_ASGI_APP is not None:
            await _mcp_stack.enter_async_context(_MCP_ASGI_APP.router.lifespan_context(_MCP_ASGI_APP))
        yield

    await runtime.stop()


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
