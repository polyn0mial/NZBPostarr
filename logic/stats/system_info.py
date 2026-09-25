"""One-shot and cached system information for the stats page and CLI."""

import platform
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil
from loguru import logger

from logic.stats import collector
from logic.stats.collector import _B_IN_GIB, _B_IN_MIB

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


def _get_1h_network_delta() -> Tuple[float, float]:
    """Get the 1-hour network delta from the in-memory ring buffer."""
    try:
        ring = list(collector._STATS_RING)
        if len(ring) >= 2:
            sent_delta = ring[-1]["total_sent_mb"] - ring[0]["total_sent_mb"]
            recv_delta = ring[-1]["total_recv_mb"] - ring[0]["total_recv_mb"]
            return (
                sent_delta if sent_delta > 0 else 0,
                recv_delta if recv_delta > 0 else 0,
            )
    except (KeyError, TypeError, RuntimeError) as e:
        logger.debug(f"1h network delta unavailable: {e}")
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

    collector._CPU_STATS["percent"] = cpu_pct
    collector._CPU_STATS["per_core"] = per_core
    collector._INTERFACE_SPEEDS.clear()
    collector._INTERFACE_SPEEDS.update(iface_speeds)
    collector._NETWORK_SPEED.update(
        {
            "upload": total_up / _B_IN_MIB,
            "download": total_down / _B_IN_MIB,
            "connections": conn_count,
        }
    )

    if start_disk and end_disk:
        collector._DISK_IO_RATE["read"] = max(0, end_disk.read_bytes - start_disk.read_bytes) / dt
        collector._DISK_IO_RATE["write"] = max(0, end_disk.write_bytes - start_disk.write_bytes) / dt
    else:
        collector._DISK_IO_RATE["read"] = 0.0
        collector._DISK_IO_RATE["write"] = 0.0

    collector._TOP_PROCS.update({"cpu": [], "memory": [], "network": [], "disk": [], "total": 0})

    return get_full_system_info()


def _cached_temps(now: float) -> List[Dict[str, Any]]:
    """Return sensor temperatures, refreshing the 10s cache when stale."""
    global _TEMPS_CACHE, _TEMPS_CACHE_TS
    if not hasattr(psutil, "sensors_temperatures"):
        return []
    with _CACHE_LOCK:
        if _TEMPS_CACHE and (now - _TEMPS_CACHE_TS) < 10:
            return list(_TEMPS_CACHE)
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
        return list(fresh)


def _cached_battery(now: float) -> Optional[Dict[str, Any]]:
    """Return battery status, refreshing the 10s cache when stale."""
    global _BATT_CACHE, _BATT_CACHE_TS
    if not hasattr(psutil, "sensors_battery"):
        return None
    with _CACHE_LOCK:
        if _BATT_CACHE is not None and (now - _BATT_CACHE_TS) < 10:
            return dict(_BATT_CACHE) if _BATT_CACHE else None
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
        return dict(fresh_batt) if fresh_batt else None


def _cached_partitions(now: float) -> List[Dict[str, Any]]:
    """Return disk partitions, refreshing the 15s cache when stale."""
    global _PARTITIONS_CACHE, _PARTITIONS_CACHE_TS
    with _CACHE_LOCK:
        if _PARTITIONS_CACHE and (now - _PARTITIONS_CACHE_TS) < 15:
            return list(_PARTITIONS_CACHE)
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
        return list(fresh_parts)


def _cached_users(now: float) -> List[Dict[str, Any]]:
    """Return logged-in users, refreshing the 30s cache when stale."""
    global _USERS_CACHE, _USERS_CACHE_TS
    with _CACHE_LOCK:
        if _USERS_CACHE and (now - _USERS_CACHE_TS) < 30:
            return list(_USERS_CACHE)
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
        return list(fresh_users)


def _cached_iface_ip(now: float) -> Dict[str, str]:
    """Return the per-interface IPv4 address map, refreshing the 60s cache when stale."""
    global _IFACE_IP_CACHE, _IFACE_IP_CACHE_TS
    with _CACHE_LOCK:
        if _IFACE_IP_CACHE and (now - _IFACE_IP_CACHE_TS) < 60:
            return dict(_IFACE_IP_CACHE)
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
        return dict(fresh_ip)


def get_full_system_info() -> Dict[str, Any]:
    """Retrieve comprehensive system statistics for the stats page."""
    import os

    now = time.time()

    # CPU
    per_core = collector._CPU_STATS["per_core"]
    cpu_pct = collector._CPU_STATS["percent"]
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
    total_conns = collector._NETWORK_SPEED.get("connections", 0)

    # Temperatures
    temps = _cached_temps(now)

    # Battery
    batt = _cached_battery(now)

    # Partitions
    partitions = _cached_partitions(now)

    uptime_sec = time.time() - psutil.boot_time()

    # Logged-in users
    users = _cached_users(now)

    # Build interface list for frontend
    interfaces = []
    iface_ip = _cached_iface_ip(now)

    for iface_name, speeds in collector._INTERFACE_SPEEDS.items():
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
    sent_delta_1h, recv_delta_1h = collector._NET_DELTA_1H

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
                "read_rate": collector._DISK_IO_RATE["read"],
                "write_rate": collector._DISK_IO_RATE["write"],
                "read_bytes": collector._DISK_IO_RATE["read"],  # Alias for frontend
                "write_bytes": collector._DISK_IO_RATE["write"],  # Alias for frontend
                "total_read": disk_io.read_bytes if disk_io else 0,
                "total_write": disk_io.write_bytes if disk_io else 0,
            },
            "partitions": partitions,  # Frontend expects this inside disk
        },
        "network": {
            "upload_mbps": collector._NETWORK_SPEED["upload"],
            "download_mbps": collector._NETWORK_SPEED["download"],
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
        "procs": collector._TOP_PROCS,
        "processes": {  # Alias structure for frontend
            "total_count": collector._TOP_PROCS.get("total", 0),
            "top_cpu": collector._TOP_PROCS.get("cpu", []),
            "top_memory": collector._TOP_PROCS.get("memory", []),
            "top_network": collector._TOP_PROCS.get("network", []),
            "top_disk": collector._TOP_PROCS.get("disk", []),
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
