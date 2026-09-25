"""Loguru levels, the rotating log-file sink and the shared log helpers.

Importing this module registers the custom levels (SECTION, PROGRESS, SUCCESS, COMPLETED,
VERBOSE, WARN) and adds ``data/logs/nzbpostarr.log``; ``core.config`` imports it so every
process that loads configuration logs the same way.
"""

from __future__ import annotations

import time
from pathlib import Path

from loguru import logger
from rich.text import Text


# Add custom levels to Loguru
def _register_levels() -> None:
    levels = [
        ("SECTION", 35, "<cyan>"),
        ("PROGRESS", 25, "<yellow>"),
        ("SUCCESS", 26, "<green>"),
        ("COMPLETED", 27, "<magenta>"),
        ("VERBOSE", 5, "<blue>"),
        ("WARN", 30, "<yellow>"),
    ]
    for name, no, color in levels:
        try:
            logger.level(name, no=no, color=color)
        except (TypeError, ValueError):
            pass


_register_levels()

# Configure File Logging
try:
    # Keep runtime logs out of the repo root (and out of git).
    log_file = Path(__file__).resolve().parent.parent / "data" / "logs" / "nzbpostarr.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(log_file),
        rotation="10 MB",
        retention="7 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        encoding="utf-8",
        enqueue=True,
    )
except Exception:
    pass


def log_backend_timing(
    name: str,
    start_time: float,
    *,
    context: str = "",
    warn_threshold_s: float = 1.0,
) -> None:
    """Emit timing details for expensive backend work when verbose logging is enabled."""
    from core import config as config_mod

    conf = getattr(config_mod, "_GLOBAL_CONFIG", None)
    if not bool(getattr(conf, "verbose", False)):
        return

    elapsed = time.perf_counter() - start_time
    suffix = f" | {context}" if context else ""
    message = f"[backend-timing] {name} took {elapsed:.3f}s{suffix}"
    logger.log("DEBUG" if elapsed >= warn_threshold_s else "VERBOSE", message)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string."""
    return str(Text.from_ansi(text).plain)


def log_info(m: str, level: str = "INFO") -> None:
    logger.log(level, m)


def log_success(m: str) -> None:
    logger.log("SUCCESS", m)


def log_completed(m: str) -> None:
    logger.log("COMPLETED", m)


def log_verbose(m: str) -> None:
    logger.log("VERBOSE", m)

