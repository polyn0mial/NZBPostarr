"""Human-readable formatting of sizes, durations, speeds and priority labels."""

from __future__ import annotations

from typing import Optional, Union, cast

import humanfriendly  # type: ignore[import-untyped]


def format_size(b: Union[int, float]) -> str:
    """Format bytes into a human-readable size string."""
    return cast(str, humanfriendly.format_size(b, binary=True))



def is_priority_key(progress_key: Optional[str]) -> bool:
    """Check if a progress key indicates a priority upload."""
    return "(P)" in (progress_key or "")


def get_priority_label(is_priority: bool) -> str:
    """Return a label for logging based on priority status."""
    return "Priority " if is_priority else ""



def parse_speed_to_bps(speed_str: str) -> float:
    """Convert speed string (e.g., '17.0 MiB/s' or '10 MB/s') to bytes per second using humanfriendly."""
    if not speed_str:
        return 0.0
    try:
        # Clean the string: remove /s and handle cases like 'MiB/s'
        clean_str = speed_str.strip().lower().replace("/s", "").replace("ps", "")
        return float(humanfriendly.parse_size(clean_str))
    except (humanfriendly.InvalidSize, ValueError):
        return 0.0


def format_seconds(seconds: float) -> str:
    """Format seconds into a human-readable ETA string using humanfriendly."""
    if seconds == float("inf") or seconds > 86400 * 365:
        return "--"
    if seconds <= 0:
        return "0s"
    if seconds < 60:
        return f"{int(seconds)}s"
    return cast(str, humanfriendly.format_timespan(seconds))

