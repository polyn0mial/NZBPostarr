"""Stats API: /api/stats and the dashboard stats routes."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import APIRouter, Depends, Response

from api.deps import check_feature
from core import database
from logic.services import get_upload_service, UploadService


dashboard_router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

router = APIRouter(prefix="/api/stats", tags=["stats"])

@dashboard_router.get("/system-stats")
def get_system_stats() -> Dict[str, Any]:
    """Retrieve live system resource usage."""
    check_feature("dashboard")
    from logic.stats.collector import mark_ui_active
    from logic.stats.system_info import get_full_system_info

    mark_ui_active(mode="mini")
    info = get_full_system_info()
    cpu = info["cpu"]
    mem = info["memory"]
    disk = info["disk"]
    net = info["network"]

    return {
        "hostname": info["hostname"],
        "platform": info["platform"],
        "uptime_seconds": info["uptime_seconds"],
        "cpu_percent": cpu["percent"],
        "memory_percent": mem["percent"],
        "memory_used_gb": round(mem["used_gb"], 2),
        "memory_total_gb": round(mem["total_gb"], 2),
        "disk_percent": disk["percent"],
        "disk_used_gb": round(disk["used_gb"], 2),
        "disk_total_gb": round(disk["total_gb"], 2),
        "disk_free_gb": round(disk["free_gb"], 2),
        "network_upload_mbps": net["upload_mbps"],
        "network_download_mbps": net["download_mbps"],
        "conns": net["connections"],
        "errin": net["errin"],
        "errout": net["errout"],
        "dropin": net["dropin"],
        "dropout": net["dropout"],
    }

@router.get("/summary")
async def get_stats_summary(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Retrieve summarized historical statistics."""
    check_feature("stats_page")
    return await asyncio.to_thread(service.get_statistics)

@router.get("/full")
async def get_full_stats(collapsed: str = "") -> Dict[str, Any]:
    """Retrieve comprehensive system statistics from the engine."""
    check_feature("stats_page")
    from logic.stats.collector import mark_ui_active
    from logic.stats.system_info import get_full_system_info

    # Convert comma-separated string to list
    collapsed_list = [c.strip() for c in collapsed.split(",") if c.strip()]
    mark_ui_active(mode="full", collapsed=collapsed_list)

    return await asyncio.to_thread(get_full_system_info)

@router.get("/mini")
async def get_mini_stats() -> Dict[str, Any]:
    """Lightweight stats endpoint for dashboard polling."""
    check_feature("history")
    from logic.stats.collector import mark_ui_active
    from logic.stats.system_info import get_full_system_info

    mark_ui_active(mode="mini")
    info = await asyncio.to_thread(get_full_system_info)

    mem = info["memory"]
    disk = info["disk"]
    net = info["network"]

    return {
        "hostname": info["hostname"],
        "platform": info["platform"],
        "uptime_seconds": info["uptime_seconds"],
        "cpu_percent": info["cpu"]["percent"],
        "memory_used_gb": round(mem["used_gb"], 2),
        "memory_total_gb": round(mem["total_gb"], 2),
        "memory_percent": mem["percent"],
        "disk_used_gb": round(disk["used_gb"], 2),
        "disk_total_gb": round(disk["total_gb"], 2),
        "disk_free_gb": round(disk["free_gb"], 2),
        "disk_percent": disk["percent"],
        "network_upload_mbps": net["upload_mbps"],
        "network_download_mbps": net["download_mbps"],
    }

@router.get("/top-directories")
async def get_top_directories(limit: int = 25) -> Dict[str, Any]:
    """Retrieve top-level storage usage data based on processed items."""
    check_feature("stats_page")
    return await asyncio.to_thread(database.get_top_directories, limit=limit)

@router.get("/history")
def get_stats_history(response: Response, limit: int = 100) -> Dict[str, Any]:
    """Retrieve historical system performance data for the sparklines.

    Reads from the in-memory ring buffer - zero DB hits.
    """
    check_feature("history")
    from logic.stats.collector import (
        get_iface_history,
    )
    from logic.stats.collector import (
        get_stats_history as _mem_hist,
    )

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    history = _mem_hist(limit)
    iface_history = get_iface_history(limit)
    return {"history": history, "interfaces": iface_history}

@router.post("/history/record")
async def record_stats(
    cpu: float,
    memory: float,
    upload_mbps: float,
    download_mbps: float,
    disk_percent: float = 0,
    disk_free_gb: float = 0,
    disk_read: float = 0,
    disk_write: float = 0,
    total_sent: float = 0,
    total_recv: float = 0,
    connections: int = 0,
    errors_in: int = 0,
    errors_out: int = 0,
    drops_in: int = 0,
    drops_out: int = 0,
) -> Dict[str, Any]:
    """Record current performance stats to the database."""
    check_feature("stats_page")
    database.record_system_stats(
        cpu=cpu,
        mem=memory,
        up=upload_mbps,
        down=download_mbps,
        total_sent=total_sent,
        total_recv=total_recv,
        connections=connections,
        disk_percent=disk_percent,
        disk_free_gb=disk_free_gb,
        disk_read=disk_read,
        disk_write=disk_write,
        errors_in=errors_in,
        errors_out=errors_out,
        drops_in=drops_in,
        drops_out=drops_out,
    )
    return {"status": "ok"}

@dashboard_router.get("/summary")
def get_summary(
    service: UploadService = Depends(get_upload_service),
) -> Dict[str, Any]:
    """Get a summary of current stats and queue sizes for the dashboard."""
    return service.get_dashboard_summary()
