"""Upload statistics as the dashboard reads them: a short-lived cache over the database totals."""

from __future__ import annotations

import threading
import time
from typing import Any

from core import database
from core.config import get_config
from logic.pending.index import get_pending_index_manager
from logic.pending.view import build_dashboard_summary

_stats_lock = threading.Lock()
_stats_cache: dict[str, Any] = {}
_stats_cache_ts = 0.0


def get_statistics() -> dict[str, Any]:
    """Retrieve aggregated upload statistics from the database."""
    return database.get_detailed_stats()


def get_statistics_cached(now: float, ttl_s: float) -> dict[str, Any]:
    global _stats_cache, _stats_cache_ts

    with _stats_lock:
        if _stats_cache and (now - _stats_cache_ts) < ttl_s:
            return _stats_cache
    stats = get_statistics()
    with _stats_lock:
        _stats_cache = stats
        _stats_cache_ts = now
    return stats


def invalidate_statistics_cache() -> None:
    """Drop cached upload stats so the next dashboard request reads fresh values."""
    global _stats_cache, _stats_cache_ts

    with _stats_lock:
        _stats_cache = {}
        _stats_cache_ts = 0.0


def get_dashboard_summary() -> dict[str, Any]:
    """Return dashboard summary derived from the pending-index snapshot and live stats."""
    conf = get_config()
    stats_ttl = float(max(1.0, min(5.0, getattr(conf, "ui_refresh_seconds", 2) or 2)))
    stats = get_statistics_cached(time.time(), stats_ttl)
    pending_state = get_pending_index_manager().get_state()
    if not isinstance(pending_state, dict) or pending_state.get("snapshot") is None:
        get_pending_index_manager().request_refresh(reason="dashboard-summary")
    return build_dashboard_summary(conf, stats, pending_state)
