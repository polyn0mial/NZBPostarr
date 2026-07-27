"""Small, shared redaction helpers for logs and ordinary API responses."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SECRET_MASK = "••••••••"
LOG_REDACTION = "[REDACTED]"

_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[-_.])"
    r"(?:api|api[-_]?keys?|apikey|tokens?|secrets?|password|passwd|pass|authorization|auth|usernames?|user)"
    r"(?:$|[-_.])",
    re.IGNORECASE,
)
# Any scheme, not just http(s): NNTP/FTP/WS URLs carry userinfo too, and a
# credential-bearing nntp://user:pass@host in a log line must be masked.
_URL_RE = re.compile(r"[a-z][a-z0-9+.\-]*://[^\s\"'<>]+", re.IGNORECASE)
_KEY_VALUE_RE = re.compile(
    r"(?P<prefix>(?<![\w-])"
    r"(?:api|api[-_]?keys?|apikey|tokens?|secrets?|password|passwd|pass|authorization|auth|usernames?|user)"
    r"\s*[=:]\s*)(?P<value>[^\s&,;]+)",
    re.IGNORECASE,
)


def is_sensitive_key(key: Any) -> bool:
    return bool(_SENSITIVE_KEY_RE.search(str(key or "")))


def mask_secret(value: Any) -> Any:
    return SECRET_MASK if value not in (None, "") else value


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return a recursively copied mapping with sensitive values removed."""
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        if is_sensitive_key(key):
            redacted[str(key)] = LOG_REDACTION if value not in (None, "") else value
        elif isinstance(value, Mapping):
            redacted[str(key)] = redact_mapping(value)
        elif isinstance(value, list):
            redacted[str(key)] = [
                redact_mapping(item) if isinstance(item, Mapping) else item for item in value
            ]
        else:
            redacted[str(key)] = value
    return redacted


def redact_url(raw_url: Any) -> str:
    """Mask URL userinfo and sensitive query-string values."""
    text = str(raw_url or "")
    try:
        parsed = urlsplit(text)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        if parsed.port:
            hostname = f"{hostname}:{parsed.port}"
        if parsed.username is not None:
            userinfo = LOG_REDACTION
            if parsed.password is not None:
                userinfo = f"{userinfo}:{LOG_REDACTION}"
            hostname = f"{userinfo}@{hostname}"

        query = urlencode(
            [
                (key, LOG_REDACTION if is_sensitive_key(key) else value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ],
            doseq=True,
        )
        return urlunsplit((parsed.scheme, hostname, parsed.path, query, parsed.fragment))
    except ValueError:
        return _KEY_VALUE_RE.sub(lambda match: f"{match.group('prefix')}{LOG_REDACTION}", text)


def redact_text(value: Any, *, secrets: Iterable[Any] = ()) -> str:
    """Remove known secret values and credential-bearing URL/query fragments."""
    text = str(value or "")
    for secret in sorted(
        {str(secret) for secret in secrets if secret not in (None, "")},
        key=len,
        reverse=True,
    ):
        text = text.replace(secret, LOG_REDACTION)
    text = _URL_RE.sub(lambda match: redact_url(match.group(0)), text)
    return _KEY_VALUE_RE.sub(lambda match: f"{match.group('prefix')}{LOG_REDACTION}", text)
