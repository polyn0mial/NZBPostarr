"""Posting progress: ETA and speed tracking for the posting pipeline."""

import time
from typing import Any, Dict, List, Optional, Tuple

import humanfriendly  # type: ignore[import-untyped]

from logic.stats.collector import format_seconds, parse_speed_to_bps


class ProgressTracker:
    def __init__(self, total_bytes: int):
        self.total_bytes = max(0, total_bytes)
        self.start_time = time.time()
        self.last_update = time.time()
        self.current_bytes = 0
        self.history: List[Tuple[float, int]] = []  # List of (timestamp, bytes) for rolling average

    def update(self, percent: float, speed_str: Optional[str] = None) -> Dict[str, Any]:
        """Calculate ETA and stats based on current percentage."""
        # Ensure percent is within bounds
        percent = max(0.0, min(100.0, float(percent)))

        if self.total_bytes > 0:
            self.current_bytes = int(self.total_bytes * (percent / 100))
        else:
            self.current_bytes = 0

        elapsed = time.time() - self.start_time
        remaining_bytes = max(0, self.total_bytes - self.current_bytes)

        # Use provided speed string or calculate from elapsed
        if speed_str:
            bps = parse_speed_to_bps(speed_str)
        else:
            # For calculated speed, we need some bytes and some time
            # Allow calculation after 0.5s to show something sooner
            bps = self.current_bytes / elapsed if elapsed > 0.5 and self.current_bytes > 0 else 0

        # Avoid division by zero and provide sensible ETA
        if bps > 0:
            eta_sec = remaining_bytes / bps
            eta_str = format_seconds(eta_sec)
        else:
            # If we've started but have no speed yet, show calculated if possible
            eta_str = "--" if percent < 100 else "0s"

        speed_display = speed_str
        if not speed_display:
            if bps > 0:
                speed_display = f"{humanfriendly.format_size(bps, binary=True)}/s"
            else:
                speed_display = "0.0 B/s" if percent < 100 else "DONE"

        # If speed_str was provided but was "0 B/s", keep it as is or format it
        if speed_str and bps == 0:
            speed_display = speed_str

        return {
            "percent": int(percent),
            "speed": speed_display,
            "eta": eta_str,
            "bps": bps,
        }


def get_performance_rating(current_bps: float, avg_bps: float) -> str:
    """Compare current speed to average and return a rating."""
    if avg_bps <= 0:
        return "New Server"

    ratio = current_bps / avg_bps
    if ratio > 1.2:
        return "Excellent (+20%)"
    if ratio > 1.05:
        return "Good (+5%)"
    if ratio > 0.95:
        return "Normal"
    if ratio > 0.7:
        return "Slow (-30%)"
    return "Poor (-30%+)"
