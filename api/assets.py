"""Jinja templates, the asset cache-bust token and the Cache-Control policy."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates
from loguru import logger
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from core.config import get_config
from core.utils import start_watchdog_observer, stop_watchdog_observer
from version import __version__


WEBUI_ROOT = Path(__file__).resolve().parent.parent / "webui"

ASSETS_DIR = WEBUI_ROOT / "assets"

templates = Jinja2Templates(directory=str(WEBUI_ROOT))

templates.env.variable_start_string = "[["

templates.env.variable_end_string = "]]"

templates.env.globals["auth_enabled"] = lambda: getattr(get_config(), "enable_password", False)

templates.env.globals["app_version"] = __version__

class _AssetCacheBustEventHandler(FileSystemEventHandler):
    def __init__(self, owner: "_DynamicCacheBust") -> None:
        super().__init__()
        self._owner = owner

    def _note(self, raw_path: str) -> None:
        if raw_path:
            self._owner.note_asset_path(Path(raw_path))

    def on_created(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "src_path", ""))

    def on_modified(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "src_path", ""))

    def on_moved(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "dest_path", getattr(event, "src_path", "")))

    def on_deleted(self, event: Any) -> None:
        if not event.is_directory:
            self._note(getattr(event, "src_path", ""))

class _DynamicCacheBust:
    """Tracks a monotonic cache-bust token for frontend assets."""

    _ASSET_SUFFIXES = {".js", ".css"}

    def __init__(self, ttl_seconds: float = 5.0, reconcile_interval_s: float = 300.0) -> None:
        self._ttl_seconds = max(0.5, float(ttl_seconds))
        self._reconcile_interval_s = max(self._ttl_seconds, float(reconcile_interval_s))
        self._cached_value = str(int(time.time()))
        self._cached_at = 0.0
        self._observer: Optional[Any] = None
        self._lock = threading.RLock()

    def _current_token_int(self) -> int:
        try:
            return int(self._cached_value)
        except (TypeError, ValueError):
            return 0

    def _compute_value(self) -> int:
        from itertools import chain as _ic

        latest = 0
        for path in _ic(ASSETS_DIR.rglob("*.js"), ASSETS_DIR.rglob("*.css")):
            if not path.is_file():
                continue
            try:
                latest = max(latest, int(path.stat().st_mtime))
            except OSError:
                continue
        return latest or int(time.time())

    def _refresh_from_scan_locked(self) -> None:
        self._cached_value = str(max(self._compute_value(), self._current_token_int()))
        self._cached_at = time.time()

    def note_asset_path(self, path: Path) -> None:
        if path.suffix.lower() not in self._ASSET_SUFFIXES:
            return
        try:
            stamp = int(path.stat().st_mtime)
        except OSError:
            stamp = int(time.time())
        with self._lock:
            self._cached_value = str(max(stamp, self._current_token_int() + 1, int(time.time())))
            self._cached_at = time.time()

    def start(self) -> None:
        with self._lock:
            if self._observer is not None:
                return
            try:
                self._refresh_from_scan_locked()
            except Exception:
                self._cached_value = str(int(time.time()))
                self._cached_at = time.time()

        try:
            observer, _scheduled = start_watchdog_observer(
                [(_AssetCacheBustEventHandler(self), ASSETS_DIR, True)],
                observer_factory=Observer,
            )
        except Exception as exc:
            logger.debug(f"Asset cache-bust watchdog unavailable: {exc}")
            observer = None
        with self._lock:
            self._observer = observer

    def stop(self) -> None:
        with self._lock:
            observer = self._observer
            self._observer = None
        stop_watchdog_observer(observer)

    def __str__(self) -> str:
        now = time.time()
        with self._lock:
            observer_running = self._observer is not None
            cached_value = self._cached_value
            cached_at = self._cached_at

        refresh_interval = self._reconcile_interval_s if observer_running else self._ttl_seconds
        if (now - cached_at) < refresh_interval:
            return cached_value

        try:
            with self._lock:
                self._refresh_from_scan_locked()
                return self._cached_value
        except Exception:
            with self._lock:
                self._cached_value = str(max(int(now), self._current_token_int()))
                self._cached_at = now
                return self._cached_value

_asset_cache_bust = _DynamicCacheBust()

templates.env.globals["cache_bust"] = _asset_cache_bust

async def add_cache_control_header(request: Request, call_next: Callable[[Request], Any]) -> Response:
    response: Response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        import re as _re
        # Only use immutable for fingerprinted (content-hashed) assets
        if _re.search(r'\.[a-f0-9]{8,}\.(js|css)$', request.url.path):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            # Non-fingerprinted assets (queue.js etc): always revalidate
            response.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/api/"):
        # NEVER cache API responses - critical for live job progress
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    elif request.url.path.endswith((".html", "/")):
        # Don't cache HTML to ensure updates
        response.headers["Cache-Control"] = "no-cache"
    return response
