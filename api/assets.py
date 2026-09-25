"""Jinja templates, the asset cache-bust token and the Cache-Control policy."""

from __future__ import annotations

import hashlib
import re
from itertools import chain
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates

from core.config import get_config
from version import __version__


WEBUI_ROOT = Path(__file__).resolve().parent.parent / "webui"

ASSETS_DIR = WEBUI_ROOT / "assets"

templates = Jinja2Templates(directory=str(WEBUI_ROOT))

templates.env.variable_start_string = "[["

templates.env.variable_end_string = "]]"

templates.env.globals["auth_enabled"] = lambda: get_config().enable_password

templates.env.globals["app_version"] = __version__

def compute_asset_token(assets_dir: Optional[Path] = None) -> str:
    """Cache-bust token for the built bundle and stylesheets.

    Hashes (relative path, size, mtime_ns) of webui/assets/js/dist/** and webui/assets/css/*.css,
    so any rebuilt or edited asset yields a new ``?t=`` value.
    """
    root = assets_dir or ASSETS_DIR
    files = chain((root / "js" / "dist").rglob("*"), (root / "css").glob("*.css"))
    digest = hashlib.sha1(usedforsecurity=False)
    for path in sorted(files):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{path.relative_to(root).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()[:12]


# Computed once at startup: the running server serves one asset build.
asset_cache_bust = compute_asset_token()

templates.env.globals["cache_bust"] = asset_cache_bust

async def add_cache_control_header(request: Request, call_next: Callable[[Request], Any]) -> Response:
    response: Response = await call_next(request)
    if request.url.path.startswith("/assets/"):
        # Only use immutable for fingerprinted (content-hashed) assets
        if re.search(r'\.[a-f0-9]{8,}\.(js|css)$', request.url.path):
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
