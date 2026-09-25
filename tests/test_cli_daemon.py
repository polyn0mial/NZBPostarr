"""The CLI sends mutating commands to a running WebUI instead of building a second engine."""

import json

import pytest

from cli import daemon_client
from cli import run as cli_run
from logic import runtime


@pytest.fixture()
def live_daemon(monkeypatch):
    """A WebUI that answers on the port; every HTTP call is recorded and answered from `replies`."""
    calls: list[tuple] = []
    replies: dict[str, tuple[int, dict]] = {}

    def fake_request(method, path, *, json_body=None, form=None):
        calls.append((method, path, json_body, form))
        return replies.get(path, (200, {}))

    def no_engine(*_args, **_kwargs):
        raise AssertionError("the CLI must not build an engine while the WebUI daemon runs")

    monkeypatch.setattr(daemon_client, "webui_is_listening", lambda: True)
    monkeypatch.setattr(daemon_client, "_request", fake_request)
    monkeypatch.setattr(runtime, "init_core", no_engine)
    monkeypatch.setattr(runtime, "ensure_engine_started", no_engine)
    monkeypatch.setattr(runtime.JobEngine, "__init__", no_engine)
    return calls, replies


def test_queue_pause_goes_to_the_daemon_without_constructing_upload_service(live_daemon, capsys) -> None:
    calls, replies = live_daemon
    replies["/queue/pause"] = (
        200,
        {
            "status": "paused",
            "message": "Queue processing paused. New jobs remain queued until resumed.",
            "control": {"paused": True, "active": None},
        },
    )

    assert cli_run.run_headless(["queue", "pause"]) == 0

    assert calls == [("POST", "/queue/pause", None, None)]
    assert capsys.readouterr().out == (
        "Queue processing paused. New jobs remain queued until resumed.\n  Queue paused: True\n"
    )


def test_queue_stop_clear_and_revalidate_use_their_routes(live_daemon, capsys) -> None:
    calls, replies = live_daemon
    replies["/queue/revalidate"] = (200, {"status": "success", "inspected": 3, "updated": 1, "cancelled": 0})

    assert cli_run.run_headless(["queue", "stop", "--clear", "--json"]) == 0
    assert cli_run.run_headless(["queue", "revalidate", "--skip-paused"]) == 0

    assert calls[0][:2] == ("POST", "/queue/stop-clear")
    assert calls[1] == ("POST", "/queue/revalidate", {"include_paused": False}, None)
    assert capsys.readouterr().out.splitlines()[-1] == "Inspected 3, updated 1, cancelled 0."


def test_job_errors_keep_the_in_process_wording_and_exit_code(live_daemon, capsys) -> None:
    calls, replies = live_daemon
    replies["/jobs/job-1/pause"] = (404, {"detail": "Job not found or not running"})
    replies["/jobs/job-2/retry"] = (409, {"detail": "Job is not eligible for retry"})

    assert cli_run.run_headless(["queue", "job", "pause", "job-1"]) == 1
    assert cli_run.run_headless(["queue", "job", "retry", "job-2", "--json"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("Error: job not found or not running\n")
    assert json.loads(out.split("\n", 1)[1]) == {"status": "error", "message": "Job is not eligible for retry"}


def test_stream_monitor_remove_goes_to_the_daemon(live_daemon, capsys) -> None:
    calls, replies = live_daemon
    replies["/stream-monitors/missing"] = (404, {"detail": "Stream monitor not found"})

    assert cli_run.run_headless(["stream-monitors", "remove", "missing"]) == 1

    assert calls == [("DELETE", "/stream-monitors/missing", None, None)]
    assert capsys.readouterr().out == "Error: stream monitor 'missing' was not found.\n"


def test_read_only_commands_stay_in_process(monkeypatch) -> None:
    monkeypatch.setattr(daemon_client, "webui_is_listening", lambda: pytest.fail("status must not probe the daemon"))
    args = cli_run.build_headless_parser().parse_args(["queue", "status"])

    assert daemon_client.run_via_daemon(args) is None


def test_unreachable_daemon_fails_instead_of_starting_a_second_engine(live_daemon, monkeypatch, capsys) -> None:
    def refuse(*_args, **_kwargs):
        raise daemon_client.DaemonRequestError("connection refused")

    monkeypatch.setattr(daemon_client, "_request", refuse)

    assert cli_run.run_headless(["queue", "resume"]) == 1
    assert "could not reach the running WebUI daemon" in capsys.readouterr().out
