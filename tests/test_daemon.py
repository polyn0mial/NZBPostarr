"""Tests for the detached launcher lifecycle."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def launcher(monkeypatch):
    """Load main.py with a clean argv without invoking its bootstrapper."""
    monkeypatch.setattr(sys, "argv", [str(ROOT / "main.py")])
    spec = importlib.util.spec_from_file_location("_test_nzbpostarr_main", ROOT / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _configure_state_path(monkeypatch, launcher, tmp_path):
    state_path = tmp_path / "daemon-state.json"
    monkeypatch.setattr(launcher, "_daemon_state_path", lambda: state_path)
    return state_path


def test_early_parser_exposes_daemon_lifecycle_flags(launcher) -> None:
    parser = launcher.build_early_parser()

    assert parser.parse_args(["--daemon"]).daemon is True
    assert parser.parse_args(["--status"]).status is True
    assert parser.parse_args(["--stop"]).stop is True
    child = parser.parse_args(["--daemon-child", "--daemon-token", "token"])
    assert child.daemon_child is True
    assert child.daemon_token == "token"


def test_daemon_state_is_written_atomically_and_checked_by_process_identity(tmp_path, monkeypatch, launcher) -> None:
    _configure_state_path(monkeypatch, launcher, tmp_path)
    state = launcher._write_daemon_state(1234, 456.25, token="token")

    assert launcher._read_daemon_state() == state

    class Process:
        def create_time(self) -> float:
            return 456.25

    monkeypatch.setattr(launcher, "_daemon_process_matches", lambda saved: Process())
    assert launcher._daemon_status() == (True, state)


def test_compare_and_delete_preserves_newer_child_state(tmp_path, monkeypatch, launcher) -> None:
    state_path = _configure_state_path(monkeypatch, launcher, tmp_path)
    reservation = launcher._write_daemon_state(1, 1.0, status="starting", token="first")
    current = launcher._write_daemon_state(2, 2.0, token="second")

    assert launcher._remove_daemon_state(reservation) is False
    assert launcher._read_daemon_state() == current
    assert state_path.exists()


def test_stale_starting_reservation_is_removed(tmp_path, monkeypatch, launcher) -> None:
    state_path = _configure_state_path(monkeypatch, launcher, tmp_path)
    launcher._write_daemon_state(1234, 456.25, status="starting", token="stale")
    monkeypatch.setattr(launcher, "_daemon_process_matches", lambda _state: None)

    assert launcher._daemon_status() == (False, None)
    assert not state_path.exists()


def test_missing_psutil_fails_safe_without_removing_state(tmp_path, monkeypatch, launcher, capsys) -> None:
    state_path = _configure_state_path(monkeypatch, launcher, tmp_path)
    state = launcher._write_daemon_state(1234, 456.25)
    monkeypatch.setattr(
        launcher,
        "_daemon_process_matches",
        lambda saved: (_ for _ in ()).throw(launcher.DaemonInspectionUnavailable("missing")),
    )

    assert launcher._stop_daemon() == 1
    assert launcher._read_daemon_state() == state
    assert state_path.exists()
    assert "psutil is unavailable" in capsys.readouterr().err


def test_start_daemon_uses_reservation_and_waits_for_ready_child(tmp_path, monkeypatch, launcher, capsys) -> None:
    _configure_state_path(monkeypatch, launcher, tmp_path)
    captured: dict[str, object] = {}

    class Process:
        def __init__(self, pid: int):
            self.pid = pid

        def create_time(self) -> float:
            return 456.25

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        token = command[command.index("--daemon-token") + 1]
        launcher._write_daemon_state(4321, 456.25, token=token)
        return Process(4321)

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda pid: Process(pid)))
    monkeypatch.setattr(launcher, "_daemon_status", lambda: (False, None))
    monkeypatch.setattr(launcher, "configured_port", lambda: 8000)
    monkeypatch.setattr(launcher, "_port_listener_details", lambda port: [])
    monkeypatch.setattr(launcher, "_daemon_process_matches", lambda saved: Process(int(saved["pid"])))
    monkeypatch.setattr(launcher, "webui_is_listening", lambda: True)
    monkeypatch.setattr(launcher.subprocess, "Popen", fake_popen)

    assert launcher._start_daemon() == 0
    assert "--daemon-child" in captured["command"]
    assert "--daemon-token" in captured["command"]
    kwargs = captured["kwargs"]
    assert kwargs["stdin"] is launcher.subprocess.DEVNULL
    assert kwargs["stdout"].name.endswith("daemon.log")
    assert kwargs["stderr"].name.endswith("daemon.log")
    if launcher.os.name == "nt":
        assert kwargs["creationflags"]
    else:
        assert kwargs["start_new_session"] is True
    assert capsys.readouterr().out == "NZBPostarr daemon started (pid 4321).\n"


def test_double_start_reservation_is_not_overwritten(tmp_path, monkeypatch, launcher, capsys) -> None:
    state_path = _configure_state_path(monkeypatch, launcher, tmp_path)
    reservation = launcher._write_daemon_state(1, 1.0, status="starting", token="reserved")
    monkeypatch.setattr(launcher, "_daemon_status", lambda: (False, reservation))

    assert launcher._start_daemon() == 1
    assert launcher._read_daemon_state() == reservation
    assert state_path.exists()
    assert "already in progress" in capsys.readouterr().err


def test_failed_child_handshake_reports_log_and_removes_reservation(tmp_path, monkeypatch, launcher, capsys) -> None:
    state_path = _configure_state_path(monkeypatch, launcher, tmp_path)

    class ParentProcess:
        def create_time(self) -> float:
            return 456.25

    class FailedChild:
        pid = 4321

        def poll(self):
            return 1

    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda pid: ParentProcess()))
    monkeypatch.setattr(launcher, "_daemon_status", lambda: (False, None))
    monkeypatch.setattr(launcher, "configured_port", lambda: 8000)
    monkeypatch.setattr(launcher, "_port_listener_details", lambda port: [])
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: FailedChild())

    assert launcher._start_daemon() == 1
    assert not state_path.exists()
    stderr = capsys.readouterr().err
    assert "exited during startup" in stderr
    assert "daemon.log" in stderr


def test_daemon_child_cleans_only_its_own_state_after_server_exits(tmp_path, monkeypatch, launcher) -> None:
    state_path = _configure_state_path(monkeypatch, launcher, tmp_path)

    class Process:
        def create_time(self) -> float:
            return 456.25

    token = "reserved"
    launcher._write_daemon_state(1, 1.0, status="starting", token=token)
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(Process=lambda pid: Process()))
    monkeypatch.setattr(launcher, "run_app", lambda: None)

    launcher._run_daemon_child(token)

    assert not state_path.exists()
