"""System, interface and upload statistics."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast, Dict, List

from loguru import logger
from sqlalchemy import delete, desc, func, select

from core.db.engine import _log_db_timing, _retry_on_lock, session_scope
from core.db.models import InterfaceStat, SystemStat, Upload, UploadResult


@_retry_on_lock(max_retries=4, base_delay=0.5)
def record_system_stats(**data: Any) -> None:
    """Record historical system metrics (retries on transient locks)."""
    try:
        with session_scope() as session:
            stat = SystemStat(
                cpu_percent=data.get("cpu"),
                memory_percent=data.get("mem"),
                upload_mbps=data.get("up"),
                download_mbps=data.get("down"),
                total_sent_mb=data.get("total_sent"),
                total_recv_mb=data.get("total_recv"),
                connections=data.get("connections"),
                disk_percent=data.get("disk_percent"),
                disk_free_gb=data.get("disk_free_gb"),
                disk_read_mbps=data.get("disk_read"),
                disk_write_mbps=data.get("disk_write"),
                errors_in=data.get("errors_in"),
                errors_out=data.get("errors_out"),
                drops_in=data.get("drops_in"),
                drops_out=data.get("drops_out"),
                swap_percent=data.get("swap_percent"),
                load_avg=data.get("load_avg"),
            )
            session.add(stat)
    except Exception as e:
        logger.error(f"Failed to record system stats: {e}")

@_retry_on_lock(max_retries=4, base_delay=0.5)
def record_system_stats_batch(rows: List[Dict[str, Any]]) -> None:
    """Record multiple system-stat snapshots in one transaction."""
    if not rows:
        return

    try:
        with session_scope() as session:
            for data in rows:
                session.add(
                    SystemStat(
                        cpu_percent=data.get("cpu"),
                        memory_percent=data.get("mem"),
                        upload_mbps=data.get("up"),
                        download_mbps=data.get("down"),
                        total_sent_mb=data.get("total_sent"),
                        total_recv_mb=data.get("total_recv"),
                        connections=data.get("connections"),
                        disk_percent=data.get("disk_percent"),
                        disk_free_gb=data.get("disk_free_gb"),
                        disk_read_mbps=data.get("disk_read"),
                        disk_write_mbps=data.get("disk_write"),
                        errors_in=data.get("errors_in"),
                        errors_out=data.get("errors_out"),
                        drops_in=data.get("drops_in"),
                        drops_out=data.get("drops_out"),
                        swap_percent=data.get("swap_percent"),
                        load_avg=data.get("load_avg"),
                    )
                )
    except Exception as e:
        logger.error(f"Failed to record system stats batch: {e}")

@_retry_on_lock(max_retries=4, base_delay=0.5)
def record_interface_stats(iface_data: List[Dict[str, Any]]) -> bool:
    """Record per-interface metrics (retries on transient locks)."""
    try:
        with session_scope() as session:
            for item in iface_data:
                stat = InterfaceStat(
                    interface_name=item["name"],
                    upload_mbps=item["upload"],
                    download_mbps=item["download"],
                )
                session.add(stat)
            return True
    except Exception as e:
        logger.error(f"Failed to record interface stats: {e}")
        return False

@_retry_on_lock(max_retries=3, base_delay=1.0)
def prune_system_stats(max_records: int = 1000) -> None:
    """Keep the stats tables lean (retries on transient locks)."""
    try:
        with session_scope() as session:
            # Delete older system_stats
            sub = select(SystemStat.id).order_by(desc(SystemStat.recorded_at)).offset(max_records)
            session.execute(delete(SystemStat).where(SystemStat.id.in_(sub.scalar_subquery())))
        # Use a separate transaction for interface pruning to hold the
        # write lock for shorter bursts.
        with session_scope() as session:
            session.execute(
                delete(InterfaceStat).where(InterfaceStat.recorded_at < datetime.now(timezone.utc) - timedelta(hours=2))
            )
    except Exception as e:
        logger.error(f"Failed to prune stats: {e}")

def _serialize_stats_resource_series(stats: List[Any]) -> Dict[str, List[Any]]:
    return {
        "cpu": [s.cpu_percent or 0 for s in stats],
        "load": [s.load_avg or 0 for s in stats],
        "memory": [s.memory_percent or 0 for s in stats],
        "swap": [s.swap_percent or 0 for s in stats],
        "disk": [s.disk_percent or 0 for s in stats],
        "free": [s.disk_free_gb or 0 for s in stats],
        "disk_read": [s.disk_read_mbps or 0 for s in stats],
        "disk_write": [s.disk_write_mbps or 0 for s in stats],
    }

def _serialize_stats_network_series(stats: List[Any]) -> Dict[str, List[Any]]:
    return {
        "upload_mbps": [s.upload_mbps or 0 for s in stats],
        "download_mbps": [s.download_mbps or 0 for s in stats],
        "total_sent_mb": [s.total_sent_mb or 0 for s in stats],
        "total_recv_mb": [s.total_recv_mb or 0 for s in stats],
        "connections": [s.connections or 0 for s in stats],
        # Errors
        "network_errors": [
            (s.errors_in or 0) + (s.errors_out or 0) + (s.drops_in or 0) + (s.drops_out or 0) for s in stats
        ],
        # Timestamps
        "recorded_at": [s.recorded_at.isoformat() for s in stats],
    }

def _serialize_stats_history_rows(stats: List[Any]) -> Dict[str, List[Any]]:
    return {
        **_serialize_stats_resource_series(stats),
        **_serialize_stats_network_series(stats),
    }

def get_system_stats_history(limit: int = 100) -> Dict[str, List[Any]]:
    """Retrieve history for dashboard charts."""
    try:
        with session_scope() as session:
            stats = session.query(SystemStat).order_by(desc(SystemStat.recorded_at)).limit(limit).all()
            stats.reverse()
            return _serialize_stats_history_rows(stats)
    except Exception as e:
        logger.error(f"Failed to fetch system stats history: {e}")
        return _empty_stats_history()

def get_interface_stats_history(limit: int = 100) -> Dict[str, Dict[str, List[Any]]]:
    """Retrieve per-interface history."""
    results: Dict[str, Dict[str, List[Any]]] = {}
    try:
        with session_scope() as session:
            ifaces = session.query(InterfaceStat.interface_name).distinct().all()
            for (if_name,) in ifaces:
                stats = (
                    session.query(InterfaceStat)
                    .filter_by(interface_name=if_name)
                    .order_by(desc(InterfaceStat.recorded_at))
                    .limit(limit)
                    .all()
                )
                stats.reverse()
                results[if_name] = {
                    "upload": cast(List[Any], [s.upload_mbps for s in stats]),
                    "download": cast(List[Any], [s.download_mbps for s in stats]),
                    "recorded_at": cast(List[Any], [s.recorded_at.isoformat() for s in stats]),
                }
    except Exception as e:
        logger.error(f"Failed to fetch interface history: {e}")
    return results

def _empty_stats_history() -> Dict[str, List[Any]]:
    """Return empty structure matching get_system_stats_history output."""
    return {
        "cpu": [],
        "load": [],
        "memory": [],
        "swap": [],
        "disk": [],
        "free": [],
        "disk_read": [],
        "disk_write": [],
        "upload_mbps": [],
        "download_mbps": [],
        "total_sent_mb": [],
        "total_recv_mb": [],
        "connections": [],
        "network_errors": [],
        "recorded_at": [],
    }

def get_hourly_upload_stats() -> Dict[str, Any]:
    """Fetch aggregate hourly stats for the last 24 hours."""
    try:
        with session_scope() as session:
            now = datetime.now(timezone.utc)
            one_hour_ago = now - timedelta(hours=1)
            cutoff_24h = now - timedelta(hours=24)

            # Get counts for the last hour by category
            last_hour_stats = (
                session.query(Upload.itype, func.count(UploadResult.id))
                .join(UploadResult)
                .filter(UploadResult.uploaded_at >= one_hour_ago)
                .filter(UploadResult.status == "success")
                .group_by(Upload.itype)
                .all()
            )

            tv_last_hour = 0
            movies_last_hour = 0
            for itype, count in last_hour_stats:
                itype_lower = itype.lower() if itype else ""
                if "tv" in itype_lower or "episode" in itype_lower:
                    tv_last_hour += count
                elif "movie" in itype_lower:
                    movies_last_hour += count

            # Get average time (duration) per upload in last 24h
            avg_time = (
                session.query(func.avg(UploadResult.duration))
                .filter(
                    UploadResult.uploaded_at >= cutoff_24h,
                    UploadResult.status == "success",
                )
                .scalar()
            ) or 0

            # Count ALL completed results (success + failed) so the frontend
            # change-detector fires for failures too, not just successes.
            total_today = (
                session.query(func.count(UploadResult.id))
                .filter(UploadResult.uploaded_at >= cutoff_24h)
                .scalar()
            ) or 0

            # Latest upload activity - catches any status change (success, fail, retry).
            latest_ts_val = (
                session.query(func.max(Upload.updated_at))
                .filter(Upload.updated_at >= cutoff_24h)
                .scalar()
            )
            latest_ts = latest_ts_val.isoformat() if latest_ts_val else None

            # Get hourly distribution for charts (legacy support)
            data = (
                session.query(
                    func.strftime("%Y-%m-%d %H:00:00", UploadResult.uploaded_at).label("hour"),
                    func.count(UploadResult.id).label("count"),
                    func.avg(UploadResult.speed_bps).label("speed"),
                )
                .filter(
                    UploadResult.uploaded_at >= cutoff_24h,
                    UploadResult.status == "success",
                )
                .group_by("hour")
                .all()
            )

            return {
                "tv_last_hour": tv_last_hour,
                "movies_last_hour": movies_last_hour,
                "total_today": int(total_today),
                "latest_ts": latest_ts,
                "avg_time_per_upload": round(float(avg_time), 1) if avg_time else 0,
                "hours": [row.hour for row in data],
                "counts": [row.count for row in data],
                "speeds": [row.speed for row in data],
            }
    except Exception as e:
        logger.error(f"Hourly stats failed: {e}")
        return {
            "tv_last_hour": 0,
            "movies_last_hour": 0,
            "total_today": 0,
            "latest_ts": None,
            "avg_time_per_upload": 0,
            "hours": [],
            "counts": [],
            "speeds": [],
        }

def get_detailed_stats() -> Dict[str, Any]:
    """Summary stats for the UI - returns full structure expected by dashboard."""
    started = time.perf_counter()
    try:
        with session_scope() as session:
            count = session.query(func.count(Upload.id)).scalar() or 0
            bytes_total = session.query(func.sum(Upload.filesize)).scalar() or 0

            # Count by type (movies/tv/misc) - handle various naming conventions
            by_category: Dict[str, int] = {
                "movies": 0,
                "tv": 0,
                "misc": 0,
                "episodes": 0,
            }
            type_counts = session.query(Upload.itype, func.count(Upload.id)).group_by(Upload.itype).all()
            for itype, cnt in type_counts:
                if itype is None:
                    continue  # Skip null types
                itype_lower = itype.lower() if itype else ""
                if itype_lower in ("movie", "movies"):
                    by_category["movies"] += cnt
                elif itype_lower in ("tv", "tv show", "tv pack"):
                    by_category["tv"] += cnt
                elif itype_lower in ("episode", "tv episode"):
                    by_category["episodes"] += cnt
                elif itype_lower == "misc":
                    by_category["misc"] += cnt

            # Count by destination/indexer
            by_destination: Dict[str, Dict[str, int]] = {}
            dest_counts = (
                session.query(
                    UploadResult.indexer_id,
                    UploadResult.status,
                    func.count(UploadResult.id),
                )
                .group_by(UploadResult.indexer_id, UploadResult.status)
                .all()
            )
            for indexer_id, status, cnt in dest_counts:
                if indexer_id not in by_destination:
                    by_destination[indexer_id] = {"success": 0, "failed": 0}

                if status == "failed":
                    by_destination[indexer_id]["failed"] += cnt
                else:
                    by_destination[indexer_id]["success"] += cnt

            # Last 24h stats
            cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            today_count = (
                session.query(func.count(UploadResult.id))
                .filter(UploadResult.uploaded_at >= cutoff, UploadResult.status == "success")
                .scalar()
            ) or 0

            # Average speed in last 24h
            avg_speed = (
                session.query(func.avg(UploadResult.speed_bps))
                .filter(UploadResult.uploaded_at >= cutoff, UploadResult.status == "success")
                .scalar()
            ) or 0

            result = {
                "total_items": count,
                "total_bytes": bytes_total,
                "uploads": {
                    "total": count,
                    "by_category": by_category,
                    "by_destination": by_destination,
                    "today": today_count,
                },
                "performance": {
                    "avg_speed_bps": avg_speed,
                    "avg_speed_mbps": (avg_speed / 1_000_000) if avg_speed else 0,
                    "gb_per_hour": round(avg_speed * 3600 / 1_073_741_824, 2) if avg_speed else 0,
                },
            }
            _log_db_timing(
                "get_detailed_stats",
                started,
                context=(f"total_items={count} destinations={len(by_destination)} category_rows={len(type_counts)}"),
                warn_threshold_s=0.5,
            )
            return result
    except Exception as e:
        logger.error(f"Detailed stats failed: {e}")
        return {
            "total_items": 0,
            "total_bytes": 0,
            "uploads": {
                "total": 0,
                "by_category": {},
                "by_destination": {},
                "today": 0,
            },
            "performance": {"avg_speed_bps": 0, "avg_speed_mbps": 0, "gb_per_hour": 0},
        }

def get_all_upload_stats() -> Dict[str, int]:
    """Retrieve aggregate success counts per indexer."""
    try:
        with session_scope() as session:
            rows = (
                session.query(UploadResult.indexer_id, func.count(UploadResult.id))
                .group_by(UploadResult.indexer_id)
                .all()
            )
            return {f"{idx}_count": count for idx, count in rows}
    except Exception as e:
        logger.error(f"Failed to get all upload stats: {e}")
        return {}

def get_top_directories(limit: int = 25) -> Dict[str, Any]:
    """Retrieve top directories by scanning base folder and categorizing usage."""
    try:
        import os
        from pathlib import Path

        from core.config import get_config

        conf = get_config()
        # The user wants to see the base folder (e.g. 0--Usenet or parent of project)
        base_path = conf.base_folder
        if not base_path or not base_path.exists():
            base_path = Path(conf.script_dir).parent

        targets = []
        for fp in getattr(conf, "folder_paths", []):
            if not isinstance(fp, dict):
                continue
            path = str(fp.get("path", "") or "").strip()
            if not path:
                continue
            resolved = Path(path)
            if resolved.exists() and resolved.is_dir():
                targets.append(resolved)

        # If categories don't exist or aren't set, use base_path
        if not targets:
            targets.append(base_path)

        # Dictionary to store results: {key: {name, size, files, path}}
        usage: Dict[str, Dict[str, Any]] = {}

        # 1. PHYSICAL SCAN FIRST (Identify REAL directories)
        for target in targets:
            try:
                with os.scandir(str(target)) as it:
                    for entry in it:
                        # Skip hidden files
                        if entry.name.startswith("."):
                            continue

                        # Use directory name as key
                        if entry.is_dir():
                            name = entry.name
                            usage[name] = {
                                "name": name,
                                "size": 0,
                                "files": 0,
                                "path": str(entry.path),
                            }
            except Exception:
                continue

        # 2. DATABASE SEED (Aggregate historical sizes)
        try:
            with session_scope() as session:
                stmt = select(Upload.item_name, Upload.filesize)
                db_results = session.execute(stmt).all()
                for item_name, size in db_results:
                    # Normalize separators for Windows/Linux consistency
                    item_name = item_name.replace("\\", "/")
                    parts = item_name.split("/")

                    # If it's "Folder/File.mkv", key is "Folder"
                    # If it's just "File.mkv", key is "File.mkv" (loose file)
                    key = parts[0]

                    # Size handling (DB values can be float/int/str)
                    try:
                        size_val = int(float(size or 0))
                    except (ValueError, TypeError):
                        size_val = 0

                    if key in usage:
                        usage[key]["size"] = cast(int, usage[key]["size"]) + size_val
                        usage[key]["files"] = cast(int, usage[key]["files"]) + 1
                    else:
                        # It's a file tracked in DB but maybe not locally in a folder
                        # We only show it if it's a significant "top level" entry
                        usage[key] = {
                            "name": key,
                            "size": size_val,
                            "files": 1,
                            "path": "Remote/Historical",
                        }
        except Exception as db_e:
            logger.warning(f"DB aggregation failed: {db_e}")

        # Final list: Sort and filter
        final_list = [v for v in usage.values() if cast(int, v["size"]) > 0]
        sorted_usage = sorted(final_list, key=lambda x: cast(int, x["size"]), reverse=True)[:limit]

        return {"directories": sorted_usage}
    except Exception as e:
        logger.error(f"Failed to get top directories: {e}")
        return {"directories": []}
