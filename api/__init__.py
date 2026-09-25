"""The HTTP API routers, in registration order.

FastAPI matches routes in registration order, so this order is load-bearing: a literal path must be
registered before a parameter path that would shadow it, and the page router comes last.
"""

from fastapi import APIRouter

from api import (
    auth,
    console,
    history,
    indexers,
    jobs,
    pages,
    pending,
    settings,
    staging,
    stats,
    stream,
    system,
)

ROUTERS: tuple[APIRouter, ...] = (
    jobs.router,
    staging.router,
    history.router,
    stream.router,
    stats.dashboard_router,
    system.dashboard_router,
    settings.router,
    console.router,
    stats.router,
    system.tests_router,
    system.router,
    indexers.router,
    pending.router,
    auth.router,
    pages.router,
)
