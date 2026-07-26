"""
🧹 NZBPostarr — Process Reaper & Scheduler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Detects and kills runaway / hung / orphaned processes
spawned by NZBPostarr (nyuu, rar, parpar, etc.).
Also owns the singleton APScheduler instance used by
all background tasks.

Safe to call at any time — will NOT kill processes that
belong to an active upload job.

Usage:
    from logic.process_reaper import reap_orphans, schedule_reaper

    reap_orphans()               # one-shot cleanup
    schedule_reaper()            # register periodic cleanup
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Set

import psutil
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

# ============================================================
#  BACKGROUND SCHEDULER (singleton)
# ============================================================


_scheduler: BackgroundScheduler | None = None


def _create_background_scheduler() -> BackgroundScheduler:
    return BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1})


def get_scheduler() -> BackgroundScheduler:
    """Return (and lazily create) the singleton background scheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = _create_background_scheduler()
        _scheduler.start()
        logger.debug("APScheduler started")
    return _scheduler


def shutdown_scheduler() -> None:
    """Gracefully shut down the scheduler (call on app exit)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.debug("APScheduler stopped")


# ── Tool processes that NZBPostarr spawns ────────────────────────────
# Matched against the process name (case-insensitive).
TOOL_PROCESS_NAMES: Set[str] = {"nyuu", "rar", "parpar", "par2"}

# How long a tool process can run before it's considered hung (seconds).
# nyuu uploads can legitimately take hours for huge files, so we're generous.
TOOL_TIMEOUT_S: int = 6 * 3600  # 6 hours

# How long a process can be in zombie/stopped state before we reap it.
ZOMBIE_TIMEOUT_S: int = 300  # 5 minutes

# Minimum interval between full reaper scans (prevent hammering).
_MIN_INTERVAL_S: float = 30.0
_last_run_ts: float = 0.0

# ── Periodic schedule ────────────────────────────────────────────────
REAPER_INTERVAL_MINUTES: int = 10


# =====================================================================
#  Core: get the set of PIDs that are currently "protected"
# =====================================================================
def _get_protected_pids() -> Set[int]:
    """Return PIDs of subprocesses registered with an active upload job.

    These are managed by UploadService._processes and must NOT be killed.
    """
    protected: Set[int] = set()
    try:
        from logic.services import UploadService

        svc = UploadService()
        with svc._lock:
            for job_id, procs in svc._processes.items():
                job = svc._jobs.get(job_id, {})
                status = job.get("status", "")
                # Protect anything in running / stopping states
                if status in ("running", "stopping", "queued"):
                    for p in procs:
                        try:
                            pid = p.pid if hasattr(p, "pid") else int(p)
                            if pid:
                                protected.add(pid)
                                # Also protect child processes of this proc
                                try:
                                    parent = psutil.Process(pid)
                                    for child in parent.children(recursive=True):
                                        protected.add(child.pid)
                                except (psutil.NoSuchProcess, psutil.AccessDenied):
                                    pass
                        except (TypeError, ValueError):
                            pass
    except Exception as exc:
        logger.debug(f"[reaper] Could not read UploadService state: {exc}")
    return protected


def _get_own_pid_tree() -> Set[int]:
    """Return the PID of this Python process + all its children.

    We never want to kill ourselves or our web server threads.
    """
    pids: Set[int] = set()
    try:
        me = psutil.Process(os.getpid())
        pids.add(me.pid)
        for child in me.children(recursive=True):
            pids.add(child.pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pids.add(os.getpid())
    return pids


# =====================================================================
#  Process classification
# =====================================================================
class StaleProcess:
    """A process that looks like it should be reaped."""

    __slots__ = ("pid", "name", "cmdline", "status", "age_s", "reason")

    def __init__(
        self,
        pid: int,
        name: str,
        cmdline: str,
        status: str,
        age_s: float,
        reason: str,
    ):
        self.pid = pid
        self.name = name
        self.cmdline = cmdline
        self.status = status
        self.age_s = age_s
        self.reason = reason

    def __repr__(self) -> str:
        hrs = self.age_s / 3600
        return f"<Stale pid={self.pid} name={self.name!r} status={self.status} age={hrs:.1f}h reason={self.reason}>"


def _find_stale_processes(
    protected: Set[int],
    own_tree: Set[int],
) -> List[StaleProcess]:
    """Scan the OS process table for orphaned tool processes."""
    stale: List[StaleProcess] = []
    now = time.time()

    for proc in psutil.process_iter(["pid", "name", "status", "create_time", "cmdline", "ppid"]):
        try:
            info = proc.info
            pid: int = info["pid"]
            name: str = (info.get("name") or "").lower()
            status: str = info.get("status") or ""
            create_time: float = info.get("create_time") or now
            cmdline_parts: list[str] = info.get("cmdline") or []
            cmdline = " ".join(cmdline_parts)[:200]
            age = now - create_time

            # Skip protected / own processes
            if pid in protected or pid in own_tree:
                continue

            # ── Check 1: Tool processes (nyuu, rar, parpar) ──────────
            is_tool = name in TOOL_PROCESS_NAMES or any(t in cmdline.lower() for t in TOOL_PROCESS_NAMES)

            if is_tool:
                # Zombie or stopped tool process → always reap
                if status in (
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_STOPPED,
                    psutil.STATUS_DEAD,
                ):
                    stale.append(StaleProcess(pid, name, cmdline, status, age, "zombie_tool"))
                    continue

                # Disk-sleep (D state) for too long
                if status == psutil.STATUS_DISK_SLEEP and age > ZOMBIE_TIMEOUT_S:
                    stale.append(StaleProcess(pid, name, cmdline, status, age, "hung_io_tool"))
                    continue

                # Tool running longer than the timeout with no active job owning it
                if age > TOOL_TIMEOUT_S:
                    stale.append(StaleProcess(pid, name, cmdline, status, age, "timeout_tool"))
                    continue

            # ── Check 2: Orphaned Python upload.py processes ─────────
            if "upload.py" in cmdline and "python" in name:
                if status in (
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_STOPPED,
                    psutil.STATUS_DEAD,
                    psutil.STATUS_DISK_SLEEP,
                ):
                    stale.append(StaleProcess(pid, name, cmdline, status, age, "zombie_upload_script"))
                    continue

                # Very old upload.py with no parent in our tree
                ppid = info.get("ppid", 0)
                if age > TOOL_TIMEOUT_S and ppid not in own_tree:
                    stale.append(StaleProcess(pid, name, cmdline, status, age, "orphan_upload_script"))
                    continue

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return stale


# =====================================================================
#  Kill logic
# =====================================================================
def _kill_process(pid: int, name: str, escalate: bool = True) -> bool:
    """Send SIGTERM, then SIGKILL if needed."""
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return True  # already gone

    try:
        p.terminate()  # SIGTERM
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        logger.warning(f"[reaper] Cannot terminate pid {pid} ({name}): {exc}")
        return False

    if not escalate:
        return True

    # Give it a moment to die gracefully
    try:
        p.wait(timeout=3)
        return True
    except psutil.TimeoutExpired:
        pass

    # SIGKILL
    try:
        p.kill()
        p.wait(timeout=2)
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.TimeoutExpired) as exc:
        logger.warning(f"[reaper] Failed to kill pid {pid} ({name}): {exc}")
        return False


# =====================================================================
#  Public API
# =====================================================================
def reap_orphans(force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """Scan for and kill orphaned / hung tool processes.

    Args:
        force:   Skip the minimum-interval throttle.
        dry_run: Log what would be killed but don't actually kill.

    Returns:
        dict with 'scanned', 'stale_found', 'killed', 'failed', 'details'.
    """
    global _last_run_ts

    result: Dict[str, Any] = {
        "scanned": 0,
        "stale_found": 0,
        "killed": 0,
        "failed": 0,
        "skipped_throttle": False,
        "details": [],
    }

    # Throttle
    now = time.time()
    if not force and (now - _last_run_ts) < _MIN_INTERVAL_S:
        result["skipped_throttle"] = True
        return result
    _last_run_ts = now

    protected = _get_protected_pids()
    own_tree = _get_own_pid_tree()
    stale = _find_stale_processes(protected, own_tree)

    result["scanned"] = len(list(psutil.pids()))
    result["stale_found"] = len(stale)

    if not stale:
        logger.debug("[reaper] No stale processes found")
        return result

    for sp in stale:
        entry = {
            "pid": sp.pid,
            "name": sp.name,
            "reason": sp.reason,
            "age_hours": round(sp.age_s / 3600, 2),
            "status": sp.status,
            "cmdline": sp.cmdline[:120],
            "killed": False,
        }

        if dry_run:
            logger.info(f"[reaper] DRY RUN — would kill: {sp}")
        else:
            logger.info(f"[reaper] Killing stale process: {sp}")
            if _kill_process(sp.pid, sp.name):
                entry["killed"] = True
                result["killed"] += 1
            else:
                result["failed"] += 1

        result["details"].append(entry)

    summary = f"[reaper] Done: {result['stale_found']} stale, {result['killed']} killed, {result['failed']} failed"
    if result["killed"] > 0 or result["failed"] > 0:
        logger.info(summary)
    else:
        logger.debug(summary)

    return result


def reap_all_tools(include_active: bool = False) -> Dict[str, Any]:
    """Nuclear option: kill ALL tool processes (nyuu, rar, parpar).

    Used during deploy restarts when we know nothing should be running.

    Args:
        include_active: If True, also kills processes in the protected set.
    """
    result: Dict[str, Any] = {"killed": 0, "failed": 0, "details": []}

    protected = set() if include_active else _get_protected_pids()
    own_tree = _get_own_pid_tree()

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = (info.get("name") or "").lower()
            cmdline = " ".join(info.get("cmdline") or []).lower()

            if pid in own_tree:
                continue
            if pid in protected:
                continue

            is_tool = name in TOOL_PROCESS_NAMES or any(t in cmdline for t in TOOL_PROCESS_NAMES)
            is_upload_script = "upload.py" in cmdline and "python" in name

            if is_tool or is_upload_script:
                logger.info(f"[reaper] Killing tool process: pid={pid} name={name}")
                if _kill_process(pid, name):
                    result["killed"] += 1
                else:
                    result["failed"] += 1
                result["details"].append({"pid": pid, "name": name})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if result["killed"]:
        logger.info(f"[reaper] reap_all_tools: killed {result['killed']} processes")

    return result


# =====================================================================
#  Tracked process audit (in-memory Popen objects)
# =====================================================================
def audit_tracked_processes() -> Dict[str, Any]:
    """Check UploadService._processes for dead Popen objects and clean them up.

    This handles the case where a Popen was registered but its owning thread
    crashed before unregistering it.
    """
    result: Dict[str, Any] = {"cleaned": 0, "active": 0, "jobs_checked": 0}

    try:
        from logic.services import UploadService

        svc = UploadService()
        with svc._lock:
            dead_entries: List[tuple[Any, Any]] = []

            for job_id, procs in list(svc._processes.items()):
                result["jobs_checked"] += 1
                for p in list(procs):
                    try:
                        rc = p.poll()
                        if rc is not None:
                            # Process is dead but still tracked
                            dead_entries.append((job_id, p))
                        else:
                            result["active"] += 1
                    except Exception:
                        dead_entries.append((job_id, p))

            for job_id, p in dead_entries:
                try:
                    svc._processes[job_id].remove(p)
                    if not svc._processes[job_id]:
                        del svc._processes[job_id]
                    result["cleaned"] += 1
                except (ValueError, KeyError):
                    pass

    except Exception as exc:
        logger.debug(f"[reaper] audit_tracked_processes error: {exc}")

    if result["cleaned"]:
        logger.info(f"[reaper] Cleaned {result['cleaned']} dead Popen refs ({result['active']} still active)")

    return result


# =====================================================================
#  Scheduler integration
# =====================================================================
def _periodic_reaper() -> None:
    """Entry point for the scheduled periodic reaper job."""
    try:
        audit_tracked_processes()
        reap_orphans()
    except Exception as exc:
        logger.error(f"[reaper] Periodic reaper failed: {exc}")


def schedule_reaper() -> None:
    """Register the reaper as a periodic APScheduler job."""
    sched = get_scheduler()
    sched.add_job(
        _periodic_reaper,
        "interval",
        minutes=REAPER_INTERVAL_MINUTES,
        id="process_reaper",
        replace_existing=True,
    )
    logger.debug(f"[reaper] Scheduled every {REAPER_INTERVAL_MINUTES} min")


# =====================================================================
#  WAL checkpoint scheduler
# =====================================================================
WAL_CHECKPOINT_INTERVAL_HOURS: int = 6


def _periodic_wal_checkpoint() -> None:
    """Entry point for the scheduled WAL checkpoint job."""
    try:
        from core.database import checkpoint_wal

        checkpoint_wal()
    except Exception as exc:
        logger.error(f"[db] Scheduled WAL checkpoint failed: {exc}")


def schedule_wal_checkpoint() -> None:
    """Register a periodic WAL TRUNCATE checkpoint via APScheduler."""
    sched = get_scheduler()
    sched.add_job(
        _periodic_wal_checkpoint,
        "interval",
        hours=WAL_CHECKPOINT_INTERVAL_HOURS,
        id="wal_checkpoint",
        replace_existing=True,
    )
    logger.debug(f"[db] WAL checkpoint scheduled every {WAL_CHECKPOINT_INTERVAL_HOURS}h")
