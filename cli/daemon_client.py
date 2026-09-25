"""Send mutating CLI commands to a running WebUI daemon.

When the WebUI already serves the configured port, a CLI that built its own
JobEngine would run a second engine over the same queue and database.
Mutating queue, job and stream commands therefore call the daemon's existing
HTTP routes on localhost, signed with a session cookie from core.auth, and
print exactly what the in-process commands print.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cli.launcher.instance import configured_port, webui_is_listening
from cli.output import _emit_result, _print_stream_jobs_queued, _print_stream_monitor_saved

API_PREFIX = "/api/uploads"
REQUEST_TIMEOUT_SECONDS = 30.0
_QUEUE_ACTIONS = {"pause", "resume", "stop", "clear", "revalidate", "job"}
_JOB_NOT_FOUND = {
    "pause": "Job not found or not running",
    "resume": "Job not found or not paused",
    "stop": "Job not found",
    "retry": "Job not found",
    "promote": "Job not found or not queued",
}


class DaemonRequestError(RuntimeError):
    """The daemon could not be reached or answered with something other than JSON."""


def _is_mutating(args: argparse.Namespace) -> bool:
    if args.command == "queue":
        return (getattr(args, "queue_command", None) or "status") in _QUEUE_ACTIONS
    if args.command == "stream-monitors":
        return getattr(args, "monitor_command", None) == "remove"
    return bool(args.command == "stream")


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Call one daemon route on localhost and return (HTTP status, JSON body)."""
    from core.auth import AUTH_COOKIE, sign_auth_cookie

    headers = {"Cookie": f"{AUTH_COOKIE}={sign_auth_cookie('cli')}", "Accept": "application/json"}
    data: bytes | None = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    url = f"http://127.0.0.1:{configured_port()}{API_PREFIX}{path}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    # Localhost only: never route the daemon call through an environment proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        raise DaemonRequestError(str(exc)) from exc
    try:
        body = json.loads(raw or b"{}")
    except ValueError as exc:
        raise DaemonRequestError(f"non-JSON response (HTTP {status})") from exc
    return status, body if isinstance(body, dict) else {"data": body}


def _failed(status: int, body: dict[str, Any]) -> int:
    print(f"Error: WebUI daemon returned HTTP {status}: {body.get('detail') or body}")
    return 1


def _queue(args: argparse.Namespace) -> int:
    action = getattr(args, "queue_command", None) or "status"
    if action == "job":
        return _queue_job(args)
    if action == "stop" and getattr(args, "clear", False):
        status, body = _request("POST", "/queue/stop-clear")
    elif action == "revalidate":
        status, body = _request(
            "POST", "/queue/revalidate", json_body={"include_paused": not getattr(args, "skip_paused", False)}
        )
    else:
        status, body = _request("POST", f"/queue/{action}")
    if status != 200:
        return _failed(status, body)
    if action == "clear":
        return _emit_result(args, body, human=f"Cleared {body.get('cleared')} queued job(s).")
    if action == "revalidate":
        human = f"Inspected {body['inspected']}, updated {body['updated']}, cancelled {body['cancelled']}."
        return _emit_result(args, body, human=human)
    return _emit_result(args, body)


def _queue_job(args: argparse.Namespace) -> int:
    job_command = getattr(args, "job_command", None)
    job_id = getattr(args, "job_id", None)
    if job_command not in _JOB_NOT_FOUND:
        print("Error: no per-job command given. Use '--headless queue job --help' to see available commands.")
        return 1

    job_path = urllib.parse.quote(str(job_id), safe="")
    if job_command == "promote":
        path = f"/queue/{job_path}/promote"
    elif job_command == "stop" and getattr(args, "clear", False):
        path = f"/jobs/{job_path}/stop-clear"
    else:
        path = f"/jobs/{job_path}/{job_command}"
    status, body = _request("POST", path)

    if status in (404, 409):
        message = "Job is not eligible for retry" if status == 409 else _JOB_NOT_FOUND[job_command]
        human = f"Error: {message}" if job_command == "retry" else f"Error: {message[0].lower()}{message[1:]}"
        return _emit_result(args, {"status": "error", "message": message}, human=human, rc=1)
    if status != 200:
        return _failed(status, body)
    if job_command in ("retry", "promote"):
        return _emit_result(args, body)
    return _emit_result(args, {"status": body.get("status"), "job_id": job_id, "message": body.get("message")})


def _stream(args: argparse.Namespace) -> int:
    from logic import usenet_stream

    submit_mode = usenet_stream.normalize_submit_mode(args.submit_mode)
    source = str(args.source).strip()
    form = {
        "source_path": source,
        "monitor_folder": "true" if args.monitor else "false",
        "category": str(args.category or ""),
        "submit_mode": submit_mode,
        "test_mode": "true" if args.test else "false",
        "enable_duplicate_check": "false" if args.skip_duplicate_check else "true",
    }
    for field, value in (
        ("release_name", None if args.monitor else args.release_name),
        ("posting_server_name", args.posting_server),
        ("indexer_id", args.indexer),
    ):
        if value:
            form[field] = str(value)
    status, body = _request("POST", "/stream-nzb", form=form)
    if status == 400:
        print(f"Error: {body.get('detail')}")
        return 1
    if status != 200:
        return _failed(status, body)

    if args.monitor:
        monitor = body.get("monitor") or {}
        payload = {
            "status": "monitoring",
            "mode": "monitor",
            "message": f"Saved stream monitor for {monitor.get('folder_path')}",
            "monitor": monitor,
        }
        _print_stream_monitor_saved(args, payload)
        return 0

    job_ids = list(body.get("job_ids") or [])
    if len(job_ids) > 1 and args.release_name:
        print("Note: ignoring --release-name for multiple NZB files.")
    payload = {
        "status": "started",
        "mode": "batch" if len(job_ids) > 1 else "job",
        "job_id": job_ids[0] if len(job_ids) == 1 else None,
        "job_ids": job_ids,
        "source_count": len(job_ids),
        "message": f"Queued {len(job_ids)} stream job(s)",
    }
    _print_stream_jobs_queued(args, payload, source, submit_mode)
    return 0


def _stream_monitor_remove(args: argparse.Namespace) -> int:
    monitor_path = urllib.parse.quote(str(args.monitor_id), safe="")
    status, body = _request("DELETE", f"/stream-monitors/{monitor_path}")
    if status == 404:
        print(f"Error: stream monitor '{args.monitor_id}' was not found.")
        return 1
    if status != 200:
        return _failed(status, body)
    print(f"Removed stream monitor {args.monitor_id}.")
    print("Changes apply to the next long-lived app/WebUI run, or after that process is restarted.")
    return 0


def run_via_daemon(args: argparse.Namespace) -> int | None:
    """Run a mutating command through the live WebUI; None means run it in-process."""
    if not _is_mutating(args) or not webui_is_listening():
        return None
    try:
        if args.command == "queue":
            return _queue(args)
        if args.command == "stream":
            return _stream(args)
        return _stream_monitor_remove(args)
    except DaemonRequestError as exc:
        print(f"Error: could not reach the running WebUI daemon: {exc}")
        return 1
