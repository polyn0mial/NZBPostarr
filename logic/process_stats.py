"""Best-effort per-process CPU, memory, network, and disk statistics."""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass, field
from typing import Any

import psutil
from loguru import logger

_B_IN_MIB = 1024 * 1024


def read_tcp_connection_count() -> int | None:
    """Read the lightweight Linux TCP socket count when available."""
    if platform.system() != "Linux":
        return None
    try:
        with open("/proc/net/sockstat", encoding="utf-8") as handle:
            for line in handle:
                if not line.startswith("TCP:"):
                    continue
                parts = line.split()
                return int(parts[2]) if len(parts) > 2 else None
    except (OSError, TypeError, ValueError):
        return None
    return None


@dataclass(slots=True)
class ProcessStatsCollector:
    """Own process-stat caches independently of the main system collector."""

    network_cache_ttl_s: float = 5.0
    io_cache: dict[int, tuple[float, int, int]] = field(default_factory=dict)
    network_cache: list[dict[str, Any]] = field(default_factory=list)
    network_cache_ts: float = 0.0

    @staticmethod
    def _collect_process_rows() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for process in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = process.info
                rows.append(
                    {
                        "pid": info["pid"],
                        "name": info["name"] or "Unknown",
                        "cpu": info["cpu_percent"] or 0,
                        "memory": info["memory_percent"] or 0,
                        "status": info["status"] or "unknown",
                    }
                )
            except (KeyError, psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return rows

    def _collect_network_rows(self, now: float, network_speed: dict[str, float]) -> list[dict[str, Any]]:
        if self.network_cache and (now - self.network_cache_ts) <= self.network_cache_ttl_s:
            return self.network_cache

        connections = psutil.net_connections(kind="inet")
        if platform.system() != "Linux":
            network_speed["connections"] = len(connections)
        by_pid: dict[int, int] = {}
        for connection in connections:
            if connection.pid:
                by_pid[connection.pid] = by_pid.get(connection.pid, 0) + 1

        fresh: list[dict[str, Any]] = []
        for pid, count in sorted(by_pid.items(), key=lambda item: item[1], reverse=True)[:10]:
            try:
                process = psutil.Process(pid)
                fresh.append(
                    {
                        "pid": pid,
                        "name": process.name(),
                        "connections": count,
                        "cpu": process.cpu_percent(),
                        "status": process.status(),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.network_cache = fresh
        self.network_cache_ts = now
        return fresh

    def _collect_disk_rows(self, pids: set[int], now: float) -> list[dict[str, Any]]:
        disk_rows: list[dict[str, Any]] = []
        for pid in pids:
            try:
                process = psutil.Process(pid)
                with process.oneshot():
                    counters = process.io_counters()
                    name = process.name()
                previous = self.io_cache.get(pid)
                if previous:
                    elapsed = now - previous[0]
                    if elapsed > 0:
                        read_rate = (counters.read_bytes - previous[1]) / elapsed
                        write_rate = (counters.write_bytes - previous[2]) / elapsed
                        if read_rate > 1024 or write_rate > 1024:
                            disk_rows.append(
                                {
                                    "pid": pid,
                                    "name": name,
                                    "read": read_rate / _B_IN_MIB,
                                    "write": write_rate / _B_IN_MIB,
                                }
                            )
                self.io_cache[pid] = (now, counters.read_bytes, counters.write_bytes)
            except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
                continue

        self.io_cache = {
            pid: previous
            for pid, previous in self.io_cache.items()
            if now - previous[0] <= 60
        }
        return sorted(disk_rows, key=lambda row: row["read"] + row["write"], reverse=True)[:10]

    def collect(
        self,
        *,
        ui_mode: str,
        last_ui_ping: float,
        collapsed_sections: set[str],
        network_speed: dict[str, float],
    ) -> dict[str, Any] | None:
        """Return a process snapshot, or ``None`` when collection is skipped."""
        if ui_mode == "quiet" and time.time() - last_ui_ping > 30:
            return None

        try:
            now = time.time()
            tcp_count = read_tcp_connection_count()
            if tcp_count is not None:
                network_speed["connections"] = tcp_count

            process_rows = self._collect_process_rows()
            top_cpu = sorted(process_rows, key=lambda row: row["cpu"], reverse=True)[:10]
            top_memory = sorted(process_rows, key=lambda row: row["memory"], reverse=True)[:10]
            detailed_pids = {row["pid"] for row in top_cpu} | {row["pid"] for row in top_memory[:5]}

            network_rows: list[dict[str, Any]] = []
            if "network" not in collapsed_sections:
                try:
                    network_rows = self._collect_network_rows(now, network_speed)
                except (psutil.AccessDenied, PermissionError):
                    network_rows = []

            disk_rows = (
                self._collect_disk_rows(detailed_pids, now)
                if "disk" not in collapsed_sections
                else []
            )
            return {
                "cpu": top_cpu,
                "memory": top_memory,
                "network": network_rows,
                "disk": disk_rows,
                "total": len(process_rows),
            }
        except (OSError, RuntimeError, psutil.Error) as exc:
            logger.debug(f"Process collection error: {exc}")
            return None
