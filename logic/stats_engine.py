"""
📊 NZBPostarr - Stats & Estimation Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Calculates ETAs, averages, and performance metrics.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import asyncio
import platform
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple, cast

import humanfriendly  # type: ignore[import-untyped]
import psutil
from loguru import logger

from core import database
from logic.process_stats import ProcessStatsCollector

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

# Cache heavy per-request info in get_full_system_info()
_CACHE_LOCK = threading.Lock()
_PARTITIONS_CACHE: List[Dict[str, Any]] = []
_PARTITIONS_CACHE_TS: float = 0.0
_USERS_CACHE: List[Dict[str, Any]] = []
_USERS_CACHE_TS: float = 0.0
_TEMPS_CACHE: List[Dict[str, Any]] = []
_TEMPS_CACHE_TS: float = 0.0
_BATT_CACHE: Optional[Dict[str, Any]] = None
_BATT_CACHE_TS: float = 0.0
_IFACE_IP_CACHE: Dict[str, str] = {}
_IFACE_IP_CACHE_TS: float = 0.0

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
        hist = database.get_system_stats_history(limit=_MAX_RING)
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

        iface_hist = database.get_interface_stats_history(limit=_MAX_RING)
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
        return database.get_system_stats_history(limit)
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
        return database.get_interface_stats_history(limit)
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
    from logic.process_reaper import get_scheduler

    scheduler = get_scheduler()
    job_id = "prune_system_stats"
    if _stats_page_enabled():
        scheduler.add_job(
            database.prune_system_stats,
            "interval",
            minutes=30,
            id=job_id,
            replace_existing=True,
        )
        return

    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass


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


async def _stats_collector() -> None:
    """Background task to collect system resources and network I/O."""
    global _INTERFACE_SPEEDS, _NET_DELTA_1H, _UI_MODE
    from core.config import get_config

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
                if connections_tracking_enabled:
                    if platform.system() == "Linux":
                        try:
                            with open("/proc/net/sockstat", "r", encoding="utf-8") as f:
                                for line in f:
                                    if line.startswith("TCP:"):
                                        parts = line.split()
                                        if len(parts) > 2:
                                            _NETWORK_SPEED["connections"] = int(parts[2])
                                            break
                        except Exception:
                            pass
                    else:
                        try:
                            _NETWORK_SPEED["connections"] = len(psutil.net_connections(kind="inet"))
                        except Exception:
                            _NETWORK_SPEED["connections"] = 0
                else:
                    _NETWORK_SPEED["connections"] = 0

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
                            database.record_system_stats_batch,
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
                            await asyncio.to_thread(database.record_interface_stats, iface_data)
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
        from logic.process_reaper import get_scheduler

        get_scheduler().remove_job("prune_system_stats")
    except Exception:
        pass

    # Flush recent ring buffer snapshots to DB for restart persistence
    try:
        recent = list(_STATS_RING)[-6:]
        for s in recent:
            database.record_system_stats(
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


def _get_1h_network_delta() -> Tuple[float, float]:
    """Get the 1-hour network delta from the in-memory ring buffer."""
    try:
        ring = list(_STATS_RING)
        if len(ring) >= 2:
            sent_delta = ring[-1]["total_sent_mb"] - ring[0]["total_sent_mb"]
            recv_delta = ring[-1]["total_recv_mb"] - ring[0]["total_recv_mb"]
            return (
                sent_delta if sent_delta > 0 else 0,
                recv_delta if recv_delta > 0 else 0,
            )
    except Exception:
        pass
    return (0, 0)


def collect_instant_system_info(interval_seconds: float = 0.25) -> Dict[str, Any]:
    """Collect a one-shot system snapshot without starting the background collector.

    Intended for CLI/headless use where we want explicit stats on demand but no
    long-running background telemetry loop.
    """
    interval = max(0.05, float(interval_seconds))

    start_time = time.time()
    start_net = psutil.net_io_counters(pernic=True)
    start_disk = psutil.disk_io_counters()

    cpu_pct = psutil.cpu_percent(interval=interval)
    per_core = psutil.cpu_percent(interval=None, percpu=True)

    end_time = time.time()
    dt = max(end_time - start_time, 0.001)
    end_net = psutil.net_io_counters(pernic=True)
    end_disk = psutil.disk_io_counters()

    total_up = 0.0
    total_down = 0.0
    iface_speeds: Dict[str, Dict[str, float]] = {}
    for iface, counters in end_net.items():
        prev = start_net.get(iface)
        if prev is None:
            continue

        up = max(0, counters.bytes_sent - prev.bytes_sent) / dt
        down = max(0, counters.bytes_recv - prev.bytes_recv) / dt
        iface_speeds[iface] = {
            "upload": up / _B_IN_MIB,
            "download": down / _B_IN_MIB,
        }
        total_up += up
        total_down += down

    conn_count = 0
    if platform.system() == "Linux":
        try:
            with open("/proc/net/sockstat", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("TCP:"):
                        parts = line.split()
                        if len(parts) > 2:
                            conn_count = int(parts[2])
                            break
        except Exception:
            conn_count = 0
    else:
        try:
            conn_count = len(psutil.net_connections(kind="inet"))
        except Exception:
            conn_count = 0

    _CPU_STATS["percent"] = cpu_pct
    _CPU_STATS["per_core"] = per_core
    _INTERFACE_SPEEDS.clear()
    _INTERFACE_SPEEDS.update(iface_speeds)
    _NETWORK_SPEED.update(
        {
            "upload": total_up / _B_IN_MIB,
            "download": total_down / _B_IN_MIB,
            "connections": conn_count,
        }
    )

    if start_disk and end_disk:
        _DISK_IO_RATE["read"] = max(0, end_disk.read_bytes - start_disk.read_bytes) / dt
        _DISK_IO_RATE["write"] = max(0, end_disk.write_bytes - start_disk.write_bytes) / dt
    else:
        _DISK_IO_RATE["read"] = 0.0
        _DISK_IO_RATE["write"] = 0.0

    _TOP_PROCS.update({"cpu": [], "memory": [], "network": [], "disk": [], "total": 0})

    return get_full_system_info()


def get_full_system_info() -> Dict[str, Any]:
    """Retrieve comprehensive system statistics for the stats page."""
    import os

    global _PARTITIONS_CACHE, _PARTITIONS_CACHE_TS
    global _USERS_CACHE, _USERS_CACHE_TS
    global _TEMPS_CACHE, _TEMPS_CACHE_TS
    global _BATT_CACHE, _BATT_CACHE_TS
    global _IFACE_IP_CACHE, _IFACE_IP_CACHE_TS

    now = time.time()

    # CPU
    per_core = _CPU_STATS["per_core"]
    cpu_pct = _CPU_STATS["percent"]
    cpu_freq = psutil.cpu_freq()
    try:
        load_avg = list(psutil.getloadavg())
    except Exception:
        load_avg = [0.0, 0.0, 0.0]

    # Memory
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Disk
    device_root = os.path.splitdrive(os.path.abspath(__file__))[0] + "\\" if os.name == "nt" else "/"
    try:
        disk = psutil.disk_usage(device_root)
    except Exception:
        disk = psutil.disk_usage("/")
    disk_io = psutil.disk_io_counters()

    # Network
    net_io = psutil.net_io_counters()

    # Connections - Lighter approach: Use the background task's cached value
    # instead of re-scanning every FD on the system every time the UI polls.
    total_conns = _NETWORK_SPEED.get("connections", 0)

    # Temperatures
    temps: List[Dict[str, Any]] = []
    if hasattr(psutil, "sensors_temperatures"):
        with _CACHE_LOCK:
            if _TEMPS_CACHE and (now - _TEMPS_CACHE_TS) < 10:
                temps = list(_TEMPS_CACHE)
            else:
                fresh: List[Dict[str, Any]] = []
                try:
                    raw_temps = psutil.sensors_temperatures()
                    for name, entries in raw_temps.items():
                        for entry in entries:
                            fresh.append(
                                {
                                    "name": f"{name} {entry.label}".strip(),
                                    "current": entry.current,
                                }
                            )
                except Exception:
                    fresh = []
                _TEMPS_CACHE = fresh
                _TEMPS_CACHE_TS = now
                temps = list(fresh)

    # Battery
    batt: Optional[Dict[str, Any]] = None
    if hasattr(psutil, "sensors_battery"):
        with _CACHE_LOCK:
            if _BATT_CACHE is not None and (now - _BATT_CACHE_TS) < 10:
                batt = dict(_BATT_CACHE) if _BATT_CACHE else None
            else:
                fresh_batt: Optional[Dict[str, Any]] = None
                try:
                    bi = psutil.sensors_battery()
                    if bi:
                        fresh_batt = {
                            "percent": bi.percent,
                            "plugged": bi.power_plugged,
                            "secs_left": bi.secsleft if bi.secsleft != psutil.POWER_TIME_UNLIMITED else None,
                        }
                except Exception:
                    fresh_batt = None

                _BATT_CACHE = fresh_batt
                _BATT_CACHE_TS = now
                batt = dict(fresh_batt) if fresh_batt else None

    # Partitions
    partitions: List[Dict[str, Any]] = []
    with _CACHE_LOCK:
        if _PARTITIONS_CACHE and (now - _PARTITIONS_CACHE_TS) < 15:
            partitions = list(_PARTITIONS_CACHE)
        else:
            fresh_parts: List[Dict[str, Any]] = []
            try:
                for p in psutil.disk_partitions():
                    if not p.fstype or any(x in p.fstype for x in ["tmpfs", "dev", "overlay", "squashfs"]):
                        continue
                    if "loop" in p.device or "/snap/" in p.mountpoint:
                        continue
                    try:
                        usage = psutil.disk_usage(p.mountpoint)
                        fresh_parts.append(
                            {
                                "device": p.device,
                                "mountpoint": p.mountpoint,
                                "fstype": p.fstype,
                                "total": usage.total,
                                "used": usage.used,
                                "percent": usage.percent,
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                fresh_parts = []
            _PARTITIONS_CACHE = fresh_parts
            _PARTITIONS_CACHE_TS = now
            partitions = list(fresh_parts)

    uptime_sec = time.time() - psutil.boot_time()

    # Logged-in users
    users: List[Dict[str, Any]] = []
    with _CACHE_LOCK:
        if _USERS_CACHE and (now - _USERS_CACHE_TS) < 30:
            users = list(_USERS_CACHE)
        else:
            fresh_users: List[Dict[str, Any]] = []
            try:
                for u in psutil.users():
                    fresh_users.append(
                        {
                            "name": u.name,
                            "terminal": u.terminal or "system",
                            "host": u.host or "local",
                            "started": u.started,
                        }
                    )
            except Exception:
                fresh_users = []
            _USERS_CACHE = fresh_users
            _USERS_CACHE_TS = now
            users = list(fresh_users)

    # Build interface list for frontend
    interfaces = []
    with _CACHE_LOCK:
        if _IFACE_IP_CACHE and (now - _IFACE_IP_CACHE_TS) < 60:
            iface_ip = dict(_IFACE_IP_CACHE)
        else:
            fresh_ip: Dict[str, str] = {}
            try:
                if_addrs = psutil.net_if_addrs()
                for iface_name, addrs in if_addrs.items():
                    for addr in addrs:
                        if addr.family == socket.AF_INET:
                            fresh_ip[iface_name] = addr.address
                            break
            except Exception:
                fresh_ip = {}
            _IFACE_IP_CACHE = fresh_ip
            _IFACE_IP_CACHE_TS = now
            iface_ip = dict(fresh_ip)

    for iface_name, speeds in _INTERFACE_SPEEDS.items():
        # Get IP for this interface
        ip_addr = "No IP"
        if iface_name in iface_ip:
            ip_addr = iface_ip[iface_name]

        interfaces.append(
            {
                "name": iface_name,
                "ip": ip_addr,
                "speeds": {
                    "upload": speeds.get("upload", 0),
                    "download": speeds.get("download", 0),
                },
            }
        )

    # Use cached 1-hour network delta
    sent_delta_1h, recv_delta_1h = _NET_DELTA_1H

    return {
        "cpu": {
            "percent": cpu_pct,
            "cores": psutil.cpu_count(logical=False) or 1,
            "threads": psutil.cpu_count(logical=True) or 1,
            "frequency": {"current": cpu_freq.current} if cpu_freq else None,
            "load": load_avg,  # Frontend expects "load" not "load_avg"
            "load_avg": load_avg,  # Keep for compatibility
            "per_core": per_core,
            "brand": platform.processor() or platform.machine() or "Generic CPU",
            "model": platform.processor() or platform.machine() or "Generic CPU",  # Alias
        },
        "memory": {
            "percent": mem.percent,
            "used_gb": mem.used / _B_IN_GIB,
            "total_gb": mem.total / _B_IN_GIB,
        },
        "swap": {
            "percent": swap.percent,
            "used_gb": swap.used / _B_IN_GIB,
            "total_gb": swap.total / _B_IN_GIB,
        },
        "disk": {
            "percent": disk.percent,
            "used_gb": disk.used / _B_IN_GIB,
            "free_gb": disk.free / _B_IN_GIB,
            "total_gb": disk.total / _B_IN_GIB,
            "io": {
                "read_rate": _DISK_IO_RATE["read"],
                "write_rate": _DISK_IO_RATE["write"],
                "read_bytes": _DISK_IO_RATE["read"],  # Alias for frontend
                "write_bytes": _DISK_IO_RATE["write"],  # Alias for frontend
                "total_read": disk_io.read_bytes if disk_io else 0,
                "total_write": disk_io.write_bytes if disk_io else 0,
            },
            "partitions": partitions,  # Frontend expects this inside disk
        },
        "network": {
            "upload_mbps": _NETWORK_SPEED["upload"],
            "download_mbps": _NETWORK_SPEED["download"],
            "total_sent": net_io.bytes_sent,
            "total_recv": net_io.bytes_recv,
            "total_sent_mb": net_io.bytes_sent / _B_IN_MIB,  # Frontend expects MB
            "total_recv_mb": net_io.bytes_recv / _B_IN_MIB,  # Frontend expects MB
            "total_sent_1h_mb": sent_delta_1h,  # 1-hour delta
            "total_recv_1h_mb": recv_delta_1h,  # 1-hour delta
            "connections": total_conns,
            "connections_count": total_conns,  # Alias for frontend
            "errors_in": net_io.errin,
            "errors_out": net_io.errout,
            "drops_in": net_io.dropin,
            "drops_out": net_io.dropout,
            "errin": net_io.errin,
            "errout": net_io.errout,
            "dropin": net_io.dropin,
            "dropout": net_io.dropout,
            "interfaces": interfaces,
        },
        "procs": _TOP_PROCS,
        "processes": {  # Alias structure for frontend
            "total_count": _TOP_PROCS.get("total", 0),
            "top_cpu": _TOP_PROCS.get("cpu", []),
            "top_memory": _TOP_PROCS.get("memory", []),
            "top_network": _TOP_PROCS.get("network", []),
            "top_disk": _TOP_PROCS.get("disk", []),
        },
        "temps": temps,
        "temperatures": temps,  # Alias for frontend
        "battery": batt,
        "partitions": partitions,
        "users": users,  # Logged-in users
        "system": {
            "hostname": socket.gethostname(),
            "platform": platform.system(),
            "uptime_seconds": uptime_sec,
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "python_version": platform.python_version(),  # Alias
        },
        "uptime_seconds": uptime_sec,
        "boot_time": psutil.boot_time(),
        "hostname": socket.gethostname(),
        "platform": platform.system(),
    }
