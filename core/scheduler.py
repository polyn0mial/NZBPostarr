"""The one APScheduler instance every background task registers on."""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

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
