"""Model Context Protocol endpoint for NZBPostarr.

Mounted at ``/mcp`` inside the running FastAPI application rather than shipped as
a standalone stdio process. That is deliberate: the ``JobEngine`` is a
process-local singleton (logic/runtime.py), so its job table, locks and child
process handles only exist in the process that built it. A separate stdio server
would construct its own empty ``JobEngine`` and could neither see nor control
the jobs the WebUI is actually running.

The ``mcp`` package is an OPTIONAL dependency and is intentionally absent from
requirements.lock: it pulls in roughly fifteen further packages (httpx, jsonschema,
cryptography, PyJWT and friends) that nothing else here needs. Install it only if
you want the endpoint:

    pip install mcp

Then set ``mcp_enabled: true`` and a strong ``mcp_token`` in config.yaml. Without
a token the endpoint stays unmounted, because it exposes queue control.

Every tool is a thin wrapper over the same service-layer functions the HTTP
routes and the headless CLI already call. No business logic lives here.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import FastAPI
from loguru import logger

from core.config import get_config
from core.redaction import redact_mapping
from version import __version__

MCP_PATH = "/mcp"

# Tools that change queue or job state. Gated behind mcp_allow_mutations so the
# default posture of a freshly enabled endpoint is read-only.
_MUTATING_TOOLS = frozenset(
    {
        "trigger_upload",
        "pause_queue",
        "resume_queue",
        "pause_job",
        "resume_job",
        "stop_job",
        "retry_job",
    }
)


def mcp_available() -> bool:
    """Report whether the optional mcp package is importable."""
    try:
        import mcp.server.fastmcp  # noqa: F401
    except Exception:
        return False
    return True


def mcp_configured(conf: Any) -> bool:
    """Report whether the operator has switched the endpoint on and set a token."""
    if not getattr(conf, "mcp_enabled", False):
        return False
    return bool(str(getattr(conf, "mcp_token", "") or "").strip())


def _service() -> Any:
    from logic.runtime import ensure_engine_started

    return ensure_engine_started()


def _safe_indexer_list() -> list[dict[str, Any]]:
    """Indexer metadata with credentials stripped."""
    from core.config import get_config
    from core.indexers.registry import get_all_indexers

    conf = get_config()
    # to_ui_dict is the same credential-free projection the WebUI receives.
    return [dict(idx.to_ui_dict(conf)) for idx in get_all_indexers()]


def build_tool_table() -> dict[str, Callable[..., Any]]:
    """Return the tool name -> implementation mapping.

    Split out from the server construction so it can be unit-tested without the
    optional mcp package installed.
    """

    def get_status() -> dict[str, Any]:
        """System status: configured tools, enabled indexers and queue health."""
        from core.config import get_config

        conf = get_config()
        service = _service()
        return {
            "version": __version__,
            "host": getattr(conf, "host", ""),
            "port": getattr(conf, "port", 0),
            "indexers": [
                {"id": idx.get("id"), "name": idx.get("name"), "enabled": idx.get("enabled")}
                for idx in _safe_indexer_list()
            ],
            "active_jobs": len(service.get_active_jobs(compact=True)),
        }

    def list_jobs() -> dict[str, Any]:
        """All active and queued jobs, compact form."""
        return {"jobs": _service().get_active_jobs(compact=True)}

    def get_job(job_id: str) -> dict[str, Any]:
        """Full detail for a single job by id."""
        job = _service().get_job(job_id)
        if job is None:
            return {"error": f"No such job: {job_id}"}
        return {"job": job}

    def get_job_items(job_id: str, finished: bool = False) -> dict[str, Any]:
        """Items belonging to a job. Set finished=true for completed items."""
        from logic.jobs.views import finished_job_items, queued_job_items

        service = _service()
        items = finished_job_items(service, job_id) if finished else queued_job_items(service, job_id)
        if items is None:
            return {"error": f"No such job: {job_id}"}
        return {"job_id": job_id, "finished": finished, "items": items}

    def get_dashboard() -> dict[str, Any]:
        """Dashboard summary: pending counts by category plus queue state."""
        from logic.stats.collector import get_dashboard_summary

        return dict(get_dashboard_summary())

    def get_stats() -> dict[str, Any]:
        """Aggregate upload statistics."""
        from logic.stats.collector import get_statistics

        return dict(get_statistics())

    def list_indexers() -> dict[str, Any]:
        """Configured indexers. Never includes API keys or usernames."""
        return {"indexers": _safe_indexer_list()}

    def get_history(limit: int = 25) -> dict[str, Any]:
        """Recent job history, newest first."""
        from core.db.job_history import get_job_history

        rows = get_job_history(limit=max(1, min(int(limit), 200)))
        return {"history": [redact_mapping(dict(row)) if isinstance(row, dict) else row for row in rows]}

    def get_recent_errors(limit: int = 25) -> dict[str, Any]:
        """Recent failed jobs and failed per-indexer uploads."""
        from logic.jobs.views import get_recent_errors as _errors

        return {"errors": _errors(limit=max(1, min(int(limit), 200)))}

    def get_logs(lines: int = 100) -> dict[str, Any]:
        """Tail of the in-memory application log."""
        from core.logging import console

        entries, _seq = console.get_tail(max(1, min(int(lines), 1000)))
        return {"logs": entries}

    def trigger_upload(category: str, limit: int = 0, test: bool = False) -> dict[str, Any]:
        """Queue an upload job for a category. Returns immediately with job ids.

        Non-blocking on purpose: a real upload runs far longer than any MCP client
        tool-call timeout. Poll get_job with the returned id for progress.
        """
        from logic.jobs.models import ProcessingJobRequest

        service = _service()
        request = ProcessingJobRequest(category=category, limit=int(limit) or None, test_mode=bool(test))
        job_id = service.start_processing_job_request(request)
        return {"job_id": job_id, "status": "queued"}

    def pause_queue() -> dict[str, Any]:
        """Pause queue processing."""
        return {"ok": bool(_service().pause_queue())}

    def resume_queue() -> dict[str, Any]:
        """Resume queue processing."""
        return {"ok": bool(_service().resume_queue())}

    def pause_job(job_id: str) -> dict[str, Any]:
        """Pause one job."""
        return {"ok": bool(_service().pause_job(job_id))}

    def resume_job(job_id: str) -> dict[str, Any]:
        """Resume one paused job."""
        return {"ok": bool(_service().resume_job(job_id))}

    def stop_job(job_id: str, clear_after_stop: bool = False) -> dict[str, Any]:
        """Stop one job, optionally clearing it from the queue."""
        return {"ok": bool(_service().stop_job(job_id, clear_after_stop=bool(clear_after_stop)))}

    def retry_job(job_id: str) -> dict[str, Any]:
        """Retry a failed job."""
        ok, new_id, message = _service().retry_job(job_id)
        return {"ok": bool(ok), "job_id": new_id, "message": message}

    return {
        "get_status": get_status,
        "list_jobs": list_jobs,
        "get_job": get_job,
        "get_job_items": get_job_items,
        "get_dashboard": get_dashboard,
        "get_stats": get_stats,
        "list_indexers": list_indexers,
        "get_history": get_history,
        "get_recent_errors": get_recent_errors,
        "get_logs": get_logs,
        "trigger_upload": trigger_upload,
        "pause_queue": pause_queue,
        "resume_queue": resume_queue,
        "pause_job": pause_job,
        "resume_job": resume_job,
        "stop_job": stop_job,
        "retry_job": retry_job,
    }


def build_mcp_asgi_app(conf: Any) -> Optional[Any]:
    """Build the streamable-HTTP ASGI app, or None when unavailable.

    Returns None (never raises) when the optional package is missing or the
    operator has not enabled and tokenized the endpoint, so app startup is
    unaffected in the default configuration.
    """
    if not mcp_configured(conf):
        return None
    if not mcp_available():
        logger.warning("MCP is enabled in config but the 'mcp' package is not installed; run: pip install mcp")
        return None

    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    allow_mutations = bool(getattr(conf, "mcp_allow_mutations", False))

    # The SDK's DNS-rebinding protection validates the Host header against an
    # explicit allow-list, and an empty list rejects everything with HTTP 421.
    # A self-hosted app is reached under arbitrary hostnames (LAN IP, reverse
    # proxy, Docker alias), so default to accepting any host and let operators
    # narrow it with mcp_allowed_hosts. The mandatory bearer token, not the Host
    # header, is what actually guards this endpoint - and a browser cannot set
    # an Authorization header cross-origin without passing CORS preflight.
    allowed_hosts = [str(h) for h in (getattr(conf, "mcp_allowed_hosts", None) or ["*"])]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=allowed_hosts != ["*"],
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_hosts,
    )

    # streamable_http_path defaults to "/mcp"; since the resulting app is itself
    # mounted at MCP_PATH, leaving the default would serve at /mcp/mcp.
    # stateless_http keeps each tool call independent, which suits a queue-control
    # endpoint that holds no per-client session state.
    server = FastMCP(
        name="nzbpostarr",
        stateless_http=True,
        streamable_http_path="/",
        transport_security=security,
    )

    registered = 0
    for name, fn in build_tool_table().items():
        if name in _MUTATING_TOOLS and not allow_mutations:
            continue
        server.add_tool(fn, name=name)
        registered += 1

    mode = "read+write" if allow_mutations else "read-only"
    logger.info(f"MCP endpoint mounted at {MCP_PATH} ({registered} tools, {mode})")
    return server.streamable_http_app()


def mount_mcp_endpoint(app: FastAPI) -> Optional[Any]:
    """Mount the optional MCP endpoint and return its ASGI app. None unless enabled, tokenized and installed."""
    try:
        mcp_app = build_mcp_asgi_app(get_config())
    except Exception as exc:  # never let an optional extra break startup
        logger.warning(f"MCP endpoint could not be built: {exc}")
        return None
    if mcp_app is None:
        return None
    app.mount(MCP_PATH, mcp_app)
    return mcp_app
