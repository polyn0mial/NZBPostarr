"""The one producer of timestamps for raw SQL; they match the ORM's stored format."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _utc_now():
    return datetime.now(timezone.utc)


def sql_timestamp(value: datetime) -> str:
    """Format a naive UTC datetime for raw SQL exactly as the ORM stores it (always with microseconds)."""
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _isoformat_utc(value: Optional[datetime]) -> Optional[str]:
    """Serialize datetimes with an explicit UTC offset for API consumers."""
    if value is None:
        return None

    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
