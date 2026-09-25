"""Session auth middleware, /login, /logout and the MCP token check."""

from __future__ import annotations

import hmac
import urllib.parse
from typing import Any, Callable

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from api import deps
from api.assets import templates
from api.mcp import MCP_PATH
from api.pages import PAGE_ALIASES, _page_paths
from core.auth import AUTH_COOKIE, AUTH_COOKIE_MAX_AGE, sign_auth_cookie, verify_auth_cookie
from core.config import get_config


router = APIRouter()

_AUTH_PUBLIC_PREFIXES = ("/login", "/assets/", "/favicon", "/robots.txt")

# Legacy page aliases only answer a permanent redirect; the page they point at stays gated.
_AUTH_PUBLIC_PATHS = {"/api/system/revision"} | {path for alias in PAGE_ALIASES for path in _page_paths(alias)}

def _verify_mcp_token(request: Request) -> bool:
    """Constant-time bearer-token check for the MCP endpoint."""
    expected = str(getattr(get_config(), "mcp_token", "") or "").strip()
    if not expected:
        return False

    header = request.headers.get("authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme.lower() != "bearer":
        return False
    return hmac.compare_digest(presented.strip(), expected)

async def session_auth_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    """Cookie-session auth: redirects unauthenticated browsers to /login, returns 401 for API."""
    path = request.url.path

    # Always allow public paths
    if path in _AUTH_PUBLIC_PATHS or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
        return await call_next(request)

    # The MCP endpoint is not a browser and will never carry the session cookie,
    # so it authenticates with its own bearer token instead. It is never simply
    # exempted: no valid token means no access, whether or not web login is on.
    if path == MCP_PATH or path.startswith(MCP_PATH + "/"):
        if not _verify_mcp_token(request):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    # Read through api.deps, the one config seam the page and stats routes share.
    conf = deps.get_config()
    if getattr(conf, "enable_password", False) and getattr(conf, "web_password", None):
        token = request.cookies.get(AUTH_COOKIE, "")
        if not verify_auth_cookie(token):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
            next_url = urllib.parse.quote(path, safe="")
            return RedirectResponse(url=f"/login?next={next_url}", status_code=302)

    return await call_next(request)

@router.get("/login", response_class=HTMLResponse)
async def get_login(request: Request) -> Response:
    """Login page - only shown when auth is enabled; otherwise redirects home."""
    if not (getattr(get_config(), "enable_password", False) and getattr(get_config(), "web_password", None)):
        return RedirectResponse(url="/", status_code=302)
    error = request.query_params.get("error", "")
    next_url = request.query_params.get("next", "/")
    return templates.TemplateResponse(request, "login.html", {"request": request, "error": error, "next_url": next_url})

@router.post("/login", response_class=HTMLResponse)
async def post_login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next_url: str = Form("/"),
) -> Response:
    """Process login form; set auth cookie on success."""
    conf = get_config()
    expected_username = getattr(conf, "web_username", "admin")
    expected_password = getattr(conf, "web_password", None) or ""

    if hmac.compare_digest(username, expected_username) and hmac.compare_digest(password, expected_password):
        safe_next = next_url if next_url.startswith("/") and not next_url.startswith("//") else "/"
        response = RedirectResponse(url=safe_next, status_code=302)
        response.set_cookie(
            AUTH_COOKIE,
            sign_auth_cookie(username),
            max_age=AUTH_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    # Invalid credentials: redirect back to login with error flag
    next_encoded = urllib.parse.quote(next_url, safe="")
    return RedirectResponse(url=f"/login?error=invalid&next={next_encoded}", status_code=302)

@router.get("/logout")
async def logout() -> Response:
    """Clear auth cookie and redirect to /login."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(AUTH_COOKIE)
    return response
