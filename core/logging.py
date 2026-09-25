"""Loguru levels, the rotating log-file sink, the shared log helpers and the web terminal buffer.

Importing this module registers the custom levels (SECTION, PROGRESS, SUCCESS, COMPLETED,
VERBOSE, WARN) and adds ``data/logs/nzbpostarr.log``; ``core.config`` imports it so every
process that loads configuration logs the same way.
"""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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


_WEB_CONSOLE_SUPPRESSED_PREFIXES = ("[Queue Update] Found ",)


class ConsoleBuffer:
    """In-memory log buffer for the web terminal, wired as a loguru sink.

    Instead of manual ``.log()`` calls this class exposes a ``sink``
    method that can be passed directly to ``logger.add()``.  The
    special PROGRESS-collapse behaviour (consecutive PROGRESS lines
    replace the previous one) is preserved.
    """

    def __init__(self, max_lines: int = 2000) -> None:
        self._buffer: deque[dict[str, Any]] = deque(maxlen=max_lines)
        self._lock = threading.Lock()
        self._sequence = 0
        self._handler_id: Optional[int] = None

    # ── loguru sink ──────────────────────────────────────────────────

    def sink(self, message: Any) -> None:
        """Loguru sink callable - auto-filters based on verbose config."""
        level_name = message.record["level"].name
        level_no = message.record["level"].no
        log_message = message.record["message"]

        # Avoid get_config() during early startup/config load: calling it from the sink can
        # deadlock if config loading itself emits logs. core.config imports this module, so
        # read its loaded config through sys.modules instead of importing it.
        conf = getattr(sys.modules.get("core.config"), "_GLOBAL_CONFIG", None)
        verbose_enabled = bool(getattr(conf, "verbose", False)) if conf is not None else False

        # Filter console noise if verbose is off
        if not verbose_enabled and level_no < 20:
            return

        if log_message.startswith(_WEB_CONSOLE_SUPPRESSED_PREFIXES):
            return

        self._write(log_message, level_name)

    def _write(self, message: str, level: str = "INFO") -> None:
        """Append a log entry (with PROGRESS collapse)."""
        with self._lock:
            if level == "PROGRESS" and self._buffer and self._buffer[-1]["level"] == "PROGRESS":
                self._buffer[-1].update(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "msg": message,
                    }
                )
                self._sequence += 1
                self._buffer[-1]["seq"] = self._sequence
            else:
                self._sequence += 1
                self._buffer.append(
                    {
                        "seq": self._sequence,
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "level": level,
                        "msg": message,
                    }
                )

    # Keep .log() as a thin alias for any remaining callers
    log = _write

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    def get_logs(self, after_seq: int = 0, limit: Optional[int] = None) -> tuple[list[dict[str, Any]], int]:
        with self._lock:
            logs = [entry for entry in self._buffer if entry["seq"] > after_seq]
            if not logs:
                return [], self._sequence

            # If we are limiting, only return the oldest ones after the requested seq
            # to ensure the client can eventually catch up without missing data.
            if limit and len(logs) > limit:
                logs = logs[:limit]

            # The new after_seq should be the sequence of the LAST log we are returning
            new_after = logs[-1]["seq"]
            return logs, new_after

    def get_tail(self, count: int = 100) -> tuple[list[dict[str, Any]], int]:
        """Return the most recent *count* log entries (newest-last).

        Used on initial dashboard load so the client jumps straight to
        the end of the buffer instead of replaying from the beginning.
        """
        with self._lock:
            buf = list(self._buffer)
            tail = buf[-count:] if count and len(buf) > count else buf
            return tail, self._sequence

    # ── Registration helpers ─────────────────────────────────────────

    def install(self) -> None:
        """Register this buffer as a loguru sink (idempotent)."""
        if self._handler_id is None:
            # Level 1 captures everything including custom VERBOSE (5)
            self._handler_id = logger.add(self.sink, level=1)


# The web terminal's buffer; logic.runtime installs it as a sink before anything else starts.
console = ConsoleBuffer()
