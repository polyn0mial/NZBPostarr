"""Auth cookie signing and verification (pure; shared by the web app and the CLI)."""

from __future__ import annotations

import hashlib
import hmac
import time

from core.config import get_config


AUTH_COOKIE = "nzbp_auth"

AUTH_COOKIE_MAX_AGE = 30 * 86400  # 30 days

def auth_secret() -> str:
    """Derive a signing secret from the current web_password."""
    pw = getattr(get_config(), "web_password", None) or ""
    return hashlib.sha256(f"nzbpostarr-auth:{pw}".encode()).hexdigest()

def sign_auth_cookie(username: str) -> str:
    ts = str(int(time.time()))
    msg = f"{username}:{ts}".encode()
    sig = hmac.new(auth_secret().encode(), msg, hashlib.sha256).hexdigest()
    return f"{ts}.{username}.{sig}"

def verify_auth_cookie(token: str) -> bool:
    try:
        ts_str, username, sig = token.split(".", 2)
        if time.time() - int(ts_str) > AUTH_COOKIE_MAX_AGE:
            return False
        msg = f"{username}:{ts_str}".encode()
        expected = hmac.new(auth_secret().encode(), msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    # A malformed cookie is simply not a login: too few parts or a non-numeric timestamp
    # (ValueError), a timestamp too large for float arithmetic (OverflowError), or a
    # non-ASCII signature that compare_digest refuses (TypeError).
    except (ValueError, OverflowError, TypeError):
        return False
