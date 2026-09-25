"""Background stats collector: live rates, ring-buffer history and stats flags."""

import asyncio
import platform
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple, cast

import humanfriendly  # type: ignore[import-untyped]
import psutil
from loguru import logger

from core.config import get_config
from core.db import stats as db_stats
from logic.pending.index import get_pending_index_manager
from logic.pending.view import build_dashboard_summary
from logic.stats.process_stats import ProcessStatsCollector

_B_IN_MIB = cast(int, humanfriendly.parse_size("1 MiB"))
_B_IN_GIB = humanfriendly.parse_size("1 GiB")

_NETWORK_SPEED: Dict[str, float] = {
    "upload": 0.0,
    "download": 0.0,
    "connections": 0,
}
_INTERFACE_SPEEDS: Dict[str, Dict[str, float]] = {}
_DISK_IO_RATE: Dict[str, float] = {"read": 0.0, "write": 0.0}
_CPU_STATS: Dict[str, Any] = {"percent": 0.0, "per_core": []}
_TOP_PROCS: Dict[str, Any] = {
    "cpu": [],
    "memory": [],
    "network": [],
    "disk": [],
    "total": 0,
}
_NET_DELTA_1H: Tuple[float, float] = (0.0, 0.0)
_PROCESS_STATS_COLLECTOR = ProcessStatsCollector()
_STATS_TASK: Optional[asyncio.Task[None]] = None

# DB flush task - single async task handles all periodic DB writes
_DB_RECORD_TASK: Optional[asyncio.Task[None]] = None

# ---------------------------------------------------------------------------
#  IN-MEMORY RING BUFFERS - serve sparkline data without touching SQLite
# ---------------------------------------------------------------------------
_MAX_RING = 60  # ~50 min at one sample every 50 s
_STATS_RING: Deque[Dict[str, Any]] = deque(maxlen=_MAX_RING)
_IFACE_RING: Dict[str, Deque[Dict[str, Any]]] = {}  # iface_name -> deque
_RING_SEEDED = False  # True once DB seed has run


def _seed_ring_from_db() -> None:
    """One-time: load persisted history from SQLite into ring buffers.

    Called from a thread so it doesn't block the event loop.
    """
    global _RING_SEEDED
    if _RING_SEEDED:
        return
    try:
        hist = db_stats.get_system_stats_history(limit=_MAX_RING)
        timestamps = hist.get("recorded_at", [])
        for i, ts in enumerate(timestamps):
            _STATS_RING.append(
                {
                    "cpu": hist["cpu"][i],
                    "load": hist["load"][i],
                    "memory": hist["memory"][i],
                    "swap": hist["swap"][i],
                    "disk": hist["disk"][i],
                    "free": hist["free"][i],
                    "disk_read": hist["disk_read"][i],
                    "disk_write": hist["disk_write"][i],
                    "upload_mbps": hist["upload_mbps"][i],
                    "download_mbps": hist["download_mbps"][i],
                    "total_sent_mb": hist["total_sent_mb"][i],
                    "total_recv_mb": hist["total_recv_mb"][i],
                    "connections": hist["connections"][i],
                    "errors_in": 0,
                    "errors_out": 0,
                    "drops_in": 0,
                    "drops_out": 0,
                    "recorded_at": ts,
                }
            )

        iface_hist = db_stats.get_interface_stats_history(limit=_MAX_RING)
        for if_name, data in iface_hist.items():
            ring: Deque[Dict[str, Any]] = deque(maxlen=_MAX_RING)
            for j, ts in enumerate(data.get("recorded_at", [])):
                ring.append(
                    {
                        "upload": data["upload"][j],
                        "download": data["download"][j],
                        "recorded_at": ts,
                    }
                )
            _IFACE_RING[if_name] = ring

        logger.debug(f"Ring buffer seeded: {len(_STATS_RING)} system, {len(_IFACE_RING)} ifaces")
    except Exception as e:
        logger.debug(f"Ring seed from DB failed (OK on first run): {e}")
    _RING_SEEDED = True


def get_stats_history(limit: int = 60) -> Dict[str, List[Any]]:
    """Return sparkline history directly from the in-memory ring buffer.

    Falls back to DB if the ring is empty (e.g. very first boot).
    """
    ring = list(_STATS_RING)
    if not ring:
        # Ring not yet populated - fallback to DB
        return db_stats.get_system_stats_history(limit)
    if limit and len(ring) > limit:
        ring = ring[-limit:]
    return {
        "cpu": [s["cpu"] for s in ring],
        "load": [s["load"] for s in ring],
        "memory": [s["memory"] for s in ring],
        "swap": [s["swap"] for s in ring],
        "disk": [s["disk"] for s in ring],
        "free": [s["free"] for s in ring],
        "disk_read": [s["disk_read"] for s in ring],
        "disk_write": [s["disk_write"] for s in ring],
        "upload_mbps": [s["upload_mbps"] for s in ring],
        "download_mbps": [s["download_mbps"] for s in ring],
        "total_sent_mb": [s["total_sent_mb"] for s in ring],
        "total_recv_mb": [s["total_recv_mb"] for s in ring],
        "connections": [s["connections"] for s in ring],
        "network_errors": [
            s.get("errors_in", 0) + s.get("errors_out", 0) + s.get("drops_in", 0) + s.get("drops_out", 0) for s in ring
        ],
        "recorded_at": [s["recorded_at"] for s in ring],
    }


def get_iface_history(limit: int = 60) -> Dict[str, Dict[str, List[Any]]]:
    """Return per-interface sparkline history from memory.

    Falls back to DB if the ring is empty.
    """
    if not _IFACE_RING:
        return db_stats.get_interface_stats_history(limit)
    result: Dict[str, Dict[str, List[Any]]] = {}
    for if_name, ring in _IFACE_RING.items():
        items = list(ring)
        if limit and len(items) > limit:
            items = items[-limit:]
        result[if_name] = {
            "upload": [s["upload"] for s in items],
            "download": [s["download"] for s in items],
            "recorded_at": [s["recorded_at"] for s in items],
        }
    return result


# Visibility tracking to optimize polling density
_LAST_UI_PING: float = 0.0
_UI_MODE: str = "quiet"  # "mini", "full", or "quiet"
_COLLAPSED_SECTIONS: List[str] = []


def mark_ui_active(mode: str = "mini", collapsed: Optional[List[str]] = None) -> None:
    """Mark the UI as active to speed up background polling."""
    global _LAST_UI_PING, _UI_MODE, _COLLAPSED_SECTIONS
    _LAST_UI_PING = time.time()
    _UI_MODE = mode
    _COLLAPSED_SECTIONS = collapsed or []


def _dashboard_stats_modules(conf: Optional[Any] = None) -> List[str]:
    from core.config import get_config

    current = conf or get_config()
    modules = getattr(current, "dashboard_stats_modules", []) or []
    return [str(module).strip() for module in modules if str(module).strip()][:6]


def _stats_page_enabled(conf: Optional[Any] = None) -> bool:
    from core.config import get_config

    current = conf or get_config()
    return bool(getattr(current, "stats_page_enabled", True))


def _dashboard_stats_enabled(conf: Optional[Any] = None) -> bool:
    from core.config import get_config

    current = conf or get_config()
    return bool(getattr(current, "dashboard_stats_enabled", True) and _dashboard_stats_modules(current))


def _history_tracking_enabled(conf: Optional[Any] = None) -> bool:
    return _stats_page_enabled(conf) or _dashboard_stats_enabled(conf)


def _connections_tracking_enabled(conf: Optional[Any] = None) -> bool:
    modules = set(_dashboard_stats_modules(conf))
    return _stats_page_enabled(conf) or "connections" in modules or "net_errors" in modules


def get_dashboard_stats_modules(conf: Optional[Any] = None) -> List[str]:
    """Public wrapper for dashboard stats module resolution."""
    return _dashboard_stats_modules(conf)


def stats_page_enabled(conf: Optional[Any] = None) -> bool:
    """Public wrapper for stats page visibility."""
    return _stats_page_enabled(conf)


def dashboard_stats_enabled(conf: Optional[Any] = None) -> bool:
    """Public wrapper for dashboard stats visibility."""
    return _dashboard_stats_enabled(conf)


def history_tracking_enabled(conf: Optional[Any] = None) -> bool:
    """Public wrapper for stats history/collector visibility."""
    return _history_tracking_enabled(conf)


def connections_tracking_enabled(conf: Optional[Any] = None) -> bool:
    """Public wrapper for connection-tracking visibility."""
    return _connections_tracking_enabled(conf)


def sync_collector_schedule() -> None:
    """Keep the periodic stats prune job aligned with the dedicated stats page setting."""
    from core.scheduler import get_scheduler

    scheduler = get_scheduler()
    job_id = "prune_system_stats"
    if _stats_page_enabled():
        scheduler.add_job(
            db_stats.prune_system_stats,
            "interval",
            minutes=30,
            id=job_id,
            replace_existing=True,
        )
        return

    try:
        scheduler.remove_job(job_id)
    except Exception as e:
        logger.debug(f"Stats prune job not removed (not scheduled): {e}")


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
    return humanfriendly.format_timespan(seconds)


def _collect_top_processes() -> None:
    """Refresh process statistics through the isolated resource collector."""
    snapshot = _PROCESS_STATS_COLLECTOR.collect(
        ui_mode=_UI_MODE,
        last_ui_ping=_LAST_UI_PING,
        collapsed_sections=set(_COLLAPSED_SECTIONS),
        network_speed=_NETWORK_SPEED,
    )
    if snapshot is not None:
        _TOP_PROCS.update(snapshot)


def _refresh_active_connection_count(connections_tracking_enabled: bool) -> None:
    """Update _NETWORK_SPEED["connections"] for the mini-mode (non stats-page) path.

    Extracted from _stats_collector to keep its own branching down.
    """
    if not connections_tracking_enabled:
        _NETWORK_SPEED["connections"] = 0
        return
    if platform.system() == "Linux":
        try:
            with open("/proc/net/sockstat", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TCP:"):
                        parts = line.split()
                        if len(parts) > 2:
                            _NETWORK_SPEED["connections"] = int(parts[2])
                            break
        except (OSError, ValueError) as e:
            logger.debug(f"Reading /proc/net/sockstat failed: {e}")
    else:
        try:
            _NETWORK_SPEED["connections"] = len(psutil.net_connections(kind="inet"))
        except Exception:
            _NETWORK_SPEED["connections"] = 0


async def _stats_collector() -> None:
    """Background task to collect system resources and network I/O."""
    global _INTERFACE_SPEEDS, _NET_DELTA_1H, _UI_MODE
    from core.config import get_config
    from logic.stats.system_info import _get_1h_network_delta  # system_info imports this module

    last_net_io = psutil.net_io_counters(pernic=True)
    last_disk_io = psutil.disk_io_counters()

    # Ring buffers are already seeded by start_collector() before we reach here.
    # Initial network delta from ring buffer (no DB hit)
    _NET_DELTA_1H = _get_1h_network_delta()

    last_time = time.time()
    loop_count = 0

    while True:
        try:
            conf = get_config()
            stats_page_enabled = _stats_page_enabled(conf)
            history_tracking_enabled = _history_tracking_enabled(conf)
            connections_tracking_enabled = _connections_tracking_enabled(conf)

            # Dynamic sleep based on UI activity
            # If no activity in 30s, drop to check every 15s.
            # If mini-mode (dashboard), use configured interval.
            # If full-mode (stats page), use a fixed snappy 2s.
            idle_time = time.time() - _LAST_UI_PING
            if idle_time > 30:
                _UI_MODE = "quiet"
                sleep_time = 15.0
            elif _UI_MODE == "full" and stats_page_enabled:
                sleep_time = 2.0
            else:
                sleep_time = float(getattr(conf, "ui_refresh_seconds", 2))

            await asyncio.sleep(sleep_time)
            now = time.time()
            dt = now - last_time
            if dt <= 0:
                continue

            # CPU (overall and per-core)
            _CPU_STATS["percent"] = psutil.cpu_percent()
            _CPU_STATS["per_core"] = psutil.cpu_percent(percpu=True)

            # Memory and Disk (Global) - Efficiently fetch once per loop
            # These are usually cheap (read from /proc/meminfo or statfs)
            mem_info = psutil.virtual_memory()
            disk_info = psutil.disk_usage("/")

            # Collect top processes (Optimized in previous step)
            if stats_page_enabled:
                _collect_top_processes()
            else:
                _TOP_PROCS.update({"cpu": [], "memory": [], "network": [], "disk": [], "total": 0})
                _refresh_active_connection_count(connections_tracking_enabled)

            # Network - Only call pernic=True to get both total and per-interface in one pass
            current_net_io = psutil.net_io_counters(pernic=True)
            total_up = 0.0
            total_down = 0.0
            total_sent = 0
            total_recv = 0
            total_errin = 0
            total_errout = 0
            total_dropin = 0
            total_dropout = 0

            for iface, io in current_net_io.items():
                # Totals for DB
                total_sent += io.bytes_sent
                total_recv += io.bytes_recv
                total_errin += io.errin
                total_errout += io.errout
                total_dropin += io.dropin
                total_dropout += io.dropout

                # Speed calculation
                if iface in last_net_io:
                    prev = last_net_io[iface]
                    up = (io.bytes_sent - prev.bytes_sent) / dt
                    down = (io.bytes_recv - prev.bytes_recv) / dt
                    _INTERFACE_SPEEDS[iface] = {
                        "upload": up / _B_IN_MIB,
                        "download": down / _B_IN_MIB,
                    }
                    total_up += up
                    total_down += down

            _NETWORK_SPEED.update({"upload": total_up / _B_IN_MIB, "download": total_down / _B_IN_MIB})
            last_net_io = current_net_io

            # Disk I/O (Global)
            # Use current_disk_io to calculate rates
            current_disk_io = psutil.disk_io_counters()
            if current_disk_io and last_disk_io:
                _DISK_IO_RATE["read"] = (current_disk_io.read_bytes - last_disk_io.read_bytes) / dt
                _DISK_IO_RATE["write"] = (current_disk_io.write_bytes - last_disk_io.write_bytes) / dt
            last_disk_io = current_disk_io

            last_time = now
            loop_count += 1

            # ── SNAPSHOT every 10 iterations (~50 s) ───────────────
            if history_tracking_enabled and loop_count % 10 == 0:
                conn_count = _NETWORK_SPEED.get("connections", 0)
                load_avg = getattr(psutil, "getloadavg", lambda: (0, 0, 0))()[0]
                swap = psutil.swap_memory()

                snapshot = {
                    "cpu": _CPU_STATS["percent"],
                    "load": load_avg,
                    "memory": mem_info.percent,
                    "swap": swap.percent,
                    "disk": disk_info.percent,
                    "free": disk_info.free / _B_IN_GIB,
                    "disk_read": _DISK_IO_RATE["read"] / _B_IN_MIB,
                    "disk_write": _DISK_IO_RATE["write"] / _B_IN_MIB,
                    "upload_mbps": _NETWORK_SPEED["upload"],
                    "download_mbps": _NETWORK_SPEED["download"],
                    "total_sent_mb": total_sent / _B_IN_MIB,
                    "total_recv_mb": total_recv / _B_IN_MIB,
                    "connections": conn_count,
                    "errors_in": total_errin,
                    "errors_out": total_errout,
                    "drops_in": total_dropin,
                    "drops_out": total_dropout,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
                _STATS_RING.append(snapshot)

                # Per-interface ring
                for name, speeds in _INTERFACE_SPEEDS.items():
                    if name not in _IFACE_RING:
                        _IFACE_RING[name] = deque(maxlen=_MAX_RING)
                    _IFACE_RING[name].append(
                        {
                            "upload": speeds["upload"],
                            "download": speeds["download"],
                            "recorded_at": snapshot["recorded_at"],
                        }
                    )

                # Refresh 1h delta from ring buffer (pure memory, no DB)
                _NET_DELTA_1H = _get_1h_network_delta()

            # ── FLUSH to DB every 60 iterations (~5 min) ──────────
            if stats_page_enabled and loop_count % 60 == 0 and loop_count > 0:

                async def _db_flush() -> None:
                    try:
                        # Grab the most-recent snapshot for DB persistence
                        recent = list(_STATS_RING)[-6:]  # last 6 snapshots (~5 min)
                        await asyncio.to_thread(
                            db_stats.record_system_stats_batch,
                            [
                                {
                                    "cpu": s["cpu"],
                                    "mem": s["memory"],
                                    "up": s["upload_mbps"],
                                    "down": s["download_mbps"],
                                    "total_sent": s["total_sent_mb"],
                                    "total_recv": s["total_recv_mb"],
                                    "connections": s["connections"],
                                    "disk_percent": s["disk"],
                                    "disk_free_gb": s["free"],
                                    "disk_read": s["disk_read"],
                                    "disk_write": s["disk_write"],
                                    "load_avg": s["load"],
                                    "swap_percent": s["swap"],
                                    "errors_in": s["errors_in"],
                                    "errors_out": s["errors_out"],
                                    "drops_in": s["drops_in"],
                                    "drops_out": s["drops_out"],
                                }
                                for s in recent
                            ],
                        )
                        # Interface stats - just the latest snapshot
                        iface_data = [
                            {
                                "name": n,
                                "upload": sp["upload"],
                                "download": sp["download"],
                            }
                            for n, sp in _INTERFACE_SPEEDS.items()
                        ]
                        if iface_data:
                            await asyncio.to_thread(db_stats.record_interface_stats, iface_data)
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        logger.debug(f"DB flush failed: {e}")

                global _DB_RECORD_TASK
                if _DB_RECORD_TASK is None or _DB_RECORD_TASK.done():
                    _DB_RECORD_TASK = asyncio.create_task(_db_flush())

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug(f"Stats error: {e}")


async def start_collector() -> None:
    global _STATS_TASK
    if _STATS_TASK is None:
        # Seed ring buffers from DB BEFORE the server accepts connections.
        # This ensures the first page load gets historical sparkline data.
        await asyncio.to_thread(_seed_ring_from_db)

        _STATS_TASK = asyncio.create_task(_stats_collector())

    sync_collector_schedule()


async def stop_collector() -> None:
    global _STATS_TASK
    if _STATS_TASK:
        _STATS_TASK.cancel()
        _STATS_TASK = None

    try:
        from core.scheduler import get_scheduler

        get_scheduler().remove_job("prune_system_stats")
    except Exception as e:
        logger.debug(f"Stats prune job not removed on stop: {e}")

    # Flush recent ring buffer snapshots to DB for restart persistence
    try:
        recent = list(_STATS_RING)[-6:]
        for s in recent:
            db_stats.record_system_stats(
                cpu=s["cpu"],
                mem=s["memory"],
                up=s["upload_mbps"],
                down=s["download_mbps"],
                total_sent=s["total_sent_mb"],
                total_recv=s["total_recv_mb"],
                connections=s["connections"],
                disk_percent=s["disk"],
                disk_free_gb=s["free"],
                disk_read=s["disk_read"],
                disk_write=s["disk_write"],
                load_avg=s["load"],
                swap_percent=s["swap"],
                errors_in=s["errors_in"],
                errors_out=s["errors_out"],
                drops_in=s["drops_in"],
                drops_out=s["drops_out"],
            )
        logger.debug(f"Flushed {len(recent)} stats snapshots to DB on shutdown")
    except Exception as e:
        logger.debug(f"Shutdown flush failed (non-fatal): {e}")


# ── Upload statistics for the dashboard ─────────────────────────────

_upload_stats_lock = threading.Lock()
_upload_stats_cache: Dict[str, Any] = {}
_upload_stats_cache_ts = 0.0


def get_statistics() -> Dict[str, Any]:
    """Retrieve aggregated upload statistics from the database."""
    return db_stats.get_detailed_stats()


def get_statistics_cached(now: float, ttl_s: float) -> Dict[str, Any]:
    global _upload_stats_cache, _upload_stats_cache_ts

    with _upload_stats_lock:
        if _upload_stats_cache and (now - _upload_stats_cache_ts) < ttl_s:
            return _upload_stats_cache
    stats = get_statistics()
    with _upload_stats_lock:
        _upload_stats_cache = stats
        _upload_stats_cache_ts = now
    return stats


def invalidate_statistics_cache() -> None:
    """Drop cached upload stats so the next dashboard request reads fresh values."""
    global _upload_stats_cache, _upload_stats_cache_ts

    with _upload_stats_lock:
        _upload_stats_cache = {}
        _upload_stats_cache_ts = 0.0


def get_dashboard_summary() -> Dict[str, Any]:
    """Return dashboard summary derived from the pending-index snapshot and live stats."""
    conf = get_config()
    stats_ttl = float(max(1.0, min(5.0, getattr(conf, "ui_refresh_seconds", 2) or 2)))
    stats = get_statistics_cached(time.time(), stats_ttl)
    pending_state = get_pending_index_manager().get_state()
    if not isinstance(pending_state, dict) or pending_state.get("snapshot") is None:
        get_pending_index_manager().request_refresh(reason="dashboard-summary")
    return build_dashboard_summary(conf, stats, pending_state)
