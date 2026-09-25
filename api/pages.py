"""HTML pages, legacy page aliases, robots.txt, error pages and the queue error beacon."""

from __future__ import annotations

import re
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from loguru import logger

from api.assets import templates
from api.deps import _stats_page_enabled


router = APIRouter()

async def not_found_exception_handler(request: Request, _exc: Exception) -> Response:
    if request.url.path.startswith("/api/"):
        detail = getattr(_exc, "detail", "Not Found")
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "detail": str(detail),
            },
        )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": 404,
            "error_title": "Page Not Found",
            "error_message": "Oops! The page you're looking for doesn't exist.",
            "icon_name": "search-x",
            "icon_bg_class": "bg-notion-accent-muted",
            "icon_color_class": "text-notion-accent",
        },
        status_code=404,
    )

async def server_error_exception_handler(request: Request, exc: Exception) -> Response:
    logger.exception(f"Internal Server Error on {request.url.path}: {exc}")

    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "detail": "Internal server error",
            },
        )

    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "request": request,
            "status_code": 500,
            "error_title": "Server Error",
            "error_message": "Something went wrong on our end. We've logged the error and are looking into it.",
            "icon_name": "alert-triangle",
            "icon_bg_class": "bg-notion-error/10",
            "icon_color_class": "text-notion-error",
        },
        status_code=500,
    )

@router.get("/robots.txt", response_class=Response)
async def robots_txt() -> Response:
    return Response(content="User-agent: *\nAllow: /\n", media_type="text/plain")

# Every page the web UI serves. Only these render; any other file under webui/ is a 404.
PAGES: dict[str, str] = {
    "": "index.html",
    "queue": "queue.html",
    "history": "history.html",
    "settings": "settings.html",
    "stats": "stats.html",
    "docs": "docs.html",
    "indexer-guides": "indexer-guides.html",
}

# Legacy page URLs, redirected permanently to the page that replaced them.
PAGE_ALIASES: dict[str, str] = {"pending": "queue", "uploads": "history"}


def _page_paths(name: str) -> tuple[str, ...]:
    if not name:
        return ("/", "/index", "/index.html")
    return (f"/{name}", f"/{name}.html")


def _page_route(template: str) -> Callable[[Request], Awaitable[Response]]:
    async def render_page(request: Request) -> Response:
        if template == "stats.html" and not _stats_page_enabled():
            raise HTTPException(status_code=404, detail="Stats page is disabled")
        return templates.TemplateResponse(request, template, {"request": request})

    return render_page


def _alias_route(target: str) -> Callable[[], Awaitable[Response]]:
    async def redirect_page() -> Response:
        return RedirectResponse(url=f"/{target}", status_code=301)

    return redirect_page


for _name, _template in PAGES.items():
    for _path in _page_paths(_name):
        router.add_api_route(_path, _page_route(_template), methods=["GET"], response_class=HTMLResponse)

for _alias, _target in PAGE_ALIASES.items():
    for _path in _page_paths(_alias):
        router.add_api_route(_path, _alias_route(_target), methods=["GET"], response_class=HTMLResponse)

def _beacon_text(value: str, limit: int) -> str:
    """Single-line, length-capped copy of a browser-supplied beacon field."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))[:limit].strip()

@router.get("/queue-error-beacon")
async def queue_error_beacon(title: str = "", detail: str = "", rev: str = "") -> Response:
    """Log a queue page load/runtime error reported by its inline overlay script."""
    logger.warning(
        f"[QUEUE-UI] Browser error beacon (rev={_beacon_text(rev, 64) or 'unknown'}): "
        f"{_beacon_text(title, 300) or 'Queue error'}"
        + (f" | {_beacon_text(detail, 1500)}" if detail else "")
    )
    return Response(status_code=204)
