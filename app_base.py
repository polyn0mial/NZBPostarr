# Auto-split from app.py - verbatim symbol bodies, synthesized imports.

import asyncio
import copy
import hashlib
import hmac
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from pydantic import BaseModel, Field
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from core import database
from core.config import APP_ROOT, get_config
from core.redaction import SECRET_MASK
from core.utils import VIDEO_EXTENSIONS, start_watchdog_observer, stop_watchdog_observer
from logic import pending_snapshot as pending_snapshot_mod
from logic import processing, updater, usenet_stream
from logic.pending_index import get_pending_index_manager
from logic.pending_scan import (
    get_configured_category_folders,
    get_configured_folders,
    scan_configured_items,
)
from logic.queueing import ProcessingJobRequest
from logic.services import UploadService, console, get_upload_service
uploads_router = APIRouter(prefix="/api/uploads", tags=["uploads"])
dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
settings_router = APIRouter(prefix="/api/settings", tags=["settings"])
console_router = APIRouter(prefix="/api/console", tags=["console"])
stats_router = APIRouter(prefix="/api/stats", tags=["stats"])
tests_router = APIRouter(prefix="/api/tests", tags=["tests"])
system_router = APIRouter(prefix="/api/system", tags=["system"])
indexers_router = APIRouter(prefix="/api/indexers", tags=["indexers"])
pending_router = APIRouter(prefix="/api/pending", tags=["pending"])
_pending_index = get_pending_index_manager()
_anime_check_lock = threading.Lock()
_pending_refresh_lock = threading.Lock()
WEBUI_ROOT = Path(__file__).parent / "webui"
ASSETS_DIR = WEBUI_ROOT / "assets"
templates = Jinja2Templates(directory=str(WEBUI_ROOT))
templates.env.variable_start_string = "[["
templates.env.variable_end_string = "]]"
templates.env.globals["auth_enabled"] = lambda: getattr(get_config(), "enable_password", False)
_AUTH_COOKIE = "nzbp_auth"
_AUTH_COOKIE_MAX_AGE = 30 * 86400  # 30 days
_AUTH_PUBLIC_PREFIXES = ("/login", "/assets/", "/favicon", "/robots.txt")
_AUTH_PUBLIC_PATHS = {"/api/system/revision"}
_MCP_PATH = "/mcp"

__all__ = [
    'APIRouter', 'APP_ROOT', 'ASSETS_DIR', 'Any', 'AsyncExitStack', 'AsyncGenerator', 'BaseModel', 'Callable', 'Depends',
    'Dict', 'FastAPI', 'Field', 'File', 'FileResponse', 'FileSystemEventHandler', 'Form', 'GZipMiddleware',
    'HTMLResponse', 'HTTPException', 'JSONResponse', 'Jinja2Templates', 'List', 'Observer', 'Optional', 'Path',
    'ProcessingJobRequest', 'RedirectResponse', 'Request', 'Response', 'SECRET_MASK', 'Set',
    'StaticFiles', 'UploadFile', 'UploadService', 'VIDEO_EXTENSIONS', 'WEBUI_ROOT',
    '_AUTH_COOKIE', '_AUTH_COOKIE_MAX_AGE', '_AUTH_PUBLIC_PATHS', '_AUTH_PUBLIC_PREFIXES', '_MCP_PATH',
    '_anime_check_lock', '_pending_index', '_pending_refresh_lock', 'asynccontextmanager', 'asyncio',
    'console', 'console_router', 'copy', 'dashboard_router', 'database', 'get_config',
    'get_configured_category_folders', 'get_configured_folders', 'get_pending_index_manager',
    'get_upload_service', 'hashlib', 'hmac', 'indexers_router', 'json', 'logger', 'os', 'pending_router',
    'pending_snapshot_mod', 'processing', 're', 'scan_configured_items', 'settings_router',
    'start_watchdog_observer', 'stats_router', 'stop_watchdog_observer', 'system_router', 'tempfile',
    'templates', 'tests_router', 'threading', 'time', 'updater', 'uploads_router', 'urllib', 'usenet_stream',
]
