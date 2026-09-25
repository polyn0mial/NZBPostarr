"""The detached background WebUI: state file, lifecycle lock, start, stop and status."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from cli.launcher.bootstrap import ROOT
from cli.launcher.instance import _port_listener_details, configured_port, webui_is_listening


DAEMON_START_TIMEOUT_SECONDS = 10.0
DAEMON_START_POLL_SECONDS = 0.1


class DaemonInspectionUnavailable(RuntimeError):
    """Raised when the runtime cannot safely inspect a stored process identity."""


def _daemon_state_path() -> Path:
    """Return the durable state path for a detached launcher child."""
    configured_path = os.environ.get("NZBPOSTARR_CONFIG")
    config_path = Path(configured_path).expanduser() if configured_path else ROOT / ".config" / "nzbpostarr" / "config.yaml"
    return config_path.parent / "daemon-state.json"


def _daemon_log_path() -> Path:
    """Return the config-owned output log for detached launcher children."""
    return _daemon_state_path().with_name("daemon.log")


def _daemon_lifecycle_lock_path() -> Path:
    """Return the advisory lock shared by lifecycle control commands."""
    return _daemon_state_path().with_name("daemon-lifecycle.lock")


@contextlib.contextmanager
def _daemon_lifecycle_lock():
    """Acquire a nonblocking cross-platform lifecycle lock."""
    path = _daemon_lifecycle_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == "":
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield True
    except OSError:
        yield False
    finally:
        if acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _read_daemon_state() -> dict[str, object] | None:
    """Read daemon state, treating malformed or missing state as absent."""
    try:
        data = json.loads(_daemon_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("pid"), int) or not isinstance(data.get("create_time"), (int, float)):
        return None
    return data


def _write_daemon_state(
    pid: int,
    create_time: float,
    *,
    status: str = "running",
    token: str | None = None,
) -> dict[str, object]:
    """Atomically publish a daemon process identity."""
    path = _daemon_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    payload: dict[str, object] = {"pid": int(pid), "create_time": float(create_time), "status": status}
    if token:
        payload["token"] = token
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return payload


def _same_daemon_state(current: dict[str, object], expected: dict[str, object]) -> bool:
    """Compare all durable identity fields before a destructive state change."""
    keys = ("pid", "create_time", "status", "token")
    return all(current.get(key) == expected.get(key) for key in keys)


def _remove_daemon_state(expected: dict[str, object] | None = None) -> bool:
    """Compare-and-delete state, preserving a newer child registration."""
    if expected is not None:
        current = _read_daemon_state()
        if current is None or not _same_daemon_state(current, expected):
            return False
    try:
        _daemon_state_path().unlink()
    except FileNotFoundError:
        return False
    return True


def _daemon_process_matches(state: dict[str, object]):
    """Return the live process only when the stored PID identity still matches."""
    try:
        import psutil

        process = psutil.Process(int(state["pid"]))
        recorded_time = float(state["create_time"])
        if abs(process.create_time() - recorded_time) > 0.01:
            return None
        return process
    except ImportError as exc:
        raise DaemonInspectionUnavailable("psutil is unavailable") from exc
    except (KeyError, TypeError, ValueError, OSError):
        return None
    except Exception:
        # psutil raises several platform-specific subclasses when a process exits.
        return None


def _daemon_status(*, clean_stale: bool = True) -> tuple[bool | None, dict[str, object] | None]:
    """Return whether the detached process is live and its persisted identity."""
    state = _read_daemon_state()
    if state is None:
        return False, None
    try:
        process = _daemon_process_matches(state)
    except DaemonInspectionUnavailable:
        return None, state
    if state.get("status") == "starting":
        if process is not None:
            return False, state
        if clean_stale:
            _remove_daemon_state(state)
            return False, None
        return False, state
    if process is not None:
        return True, state
    if clean_stale:
        _remove_daemon_state(state)
    return False, state


def _print_daemon_status() -> int:
    with _daemon_lifecycle_lock() as locked:
        if not locked:
            print("NZBPostarr daemon lifecycle operation is already in progress.", file=sys.stderr)
            return 1
        running, state = _daemon_status()
        if running and state is not None:
            print(f"NZBPostarr daemon is running (pid {state['pid']}).")
            return 0
        if running is None:
            print("Unable to inspect NZBPostarr daemon safely because psutil is unavailable.", file=sys.stderr)
            return 1
        print("NZBPostarr daemon is not running.")
        return 1


def _stop_daemon() -> int:
    """Stop only the process that still matches the stored PID creation time."""
    with _daemon_lifecycle_lock() as locked:
        if not locked:
            print("NZBPostarr daemon lifecycle operation is already in progress.", file=sys.stderr)
            return 1
        state = _read_daemon_state()
        if state is None:
            print("NZBPostarr daemon is not running.")
            return 0
        try:
            process = _daemon_process_matches(state)
        except DaemonInspectionUnavailable:
            print("Unable to inspect NZBPostarr daemon safely because psutil is unavailable.", file=sys.stderr)
            return 1
        if process is None:
            _remove_daemon_state(state)
            print("NZBPostarr daemon is not running.")
            return 0
        try:
            process.terminate()
            process.wait(timeout=10)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception as exc:
                print(f"Unable to stop NZBPostarr daemon: {exc}", file=sys.stderr)
                return 1

        _remove_daemon_state(state)
        print("NZBPostarr daemon stopped.")
        return 0


def _daemon_command(token: str, host: str | None = None, port: int | None = None) -> list[str]:
    """Build the child invocation without inheriting interactive launcher flags."""
    command = [sys.executable, str(ROOT / "main.py"), "--daemon-child", "--daemon-token", token, "--direct"]
    if host:
        command.extend(["--host", str(host)])
    if port:
        command.extend(["--port", str(port)])
    return command


def _start_daemon(host: str | None = None, port: int | None = None) -> int:
    """Launch a detached child after confirming no recorded child remains live."""
    with _daemon_lifecycle_lock() as locked:
        if not locked:
            print("NZBPostarr daemon lifecycle operation is already in progress.", file=sys.stderr)
            return 1
        running, state = _daemon_status()
        if running and state is not None:
            print(f"NZBPostarr daemon is already running (pid {state['pid']}).")
            return 0
        if state is not None and state.get("status") == "starting":
            print("NZBPostarr daemon startup is already in progress.", file=sys.stderr)
            return 1
        if running is None:
            print("Unable to inspect NZBPostarr daemon safely because psutil is unavailable.", file=sys.stderr)
            return 1

        listen_port = configured_port()
        listeners = _port_listener_details(listen_port)
        if listeners:
            print(f"Refusing to start NZBPostarr daemon: port {listen_port} is already in use.", file=sys.stderr)
            return 1

        try:
            import psutil

            parent_create_time = psutil.Process(os.getpid()).create_time()
        except Exception as exc:
            print(f"Unable to reserve NZBPostarr daemon startup: {exc}", file=sys.stderr)
            return 1
        token = secrets.token_urlsafe(24)
        reservation = _write_daemon_state(os.getpid(), parent_create_time, status="starting", token=token)
        log_path = _daemon_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_handle:
            popen_kwargs: dict[str, object] = {
                "cwd": str(ROOT),
                "stdin": subprocess.DEVNULL,
                "stdout": log_handle,
                "stderr": log_handle,
                "close_fds": True,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            else:
                popen_kwargs["start_new_session"] = True
            try:
                process = subprocess.Popen(_daemon_command(token, host, port), **popen_kwargs)
            except OSError as exc:
                _remove_daemon_state(reservation)
                print(f"Unable to start NZBPostarr daemon: {exc}. See {log_path}", file=sys.stderr)
                return 1

        deadline = time.monotonic() + DAEMON_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _remove_daemon_state(reservation)
                print(f"NZBPostarr daemon exited during startup. See {log_path}", file=sys.stderr)
                return 1
            child_state = _read_daemon_state()
            if child_state is not None and child_state.get("token") == token and child_state.get("status") == "running":
                try:
                    child_process = _daemon_process_matches(child_state)
                except DaemonInspectionUnavailable:
                    _remove_daemon_state(reservation)
                    print(f"Unable to inspect NZBPostarr daemon startup safely. See {log_path}", file=sys.stderr)
                    return 1
                if child_process is not None and webui_is_listening():
                    print(f"NZBPostarr daemon started (pid {process.pid}).")
                    return 0
            time.sleep(DAEMON_START_POLL_SECONDS)

        _remove_daemon_state(reservation)
        print(f"NZBPostarr daemon did not become ready. See {log_path}", file=sys.stderr)
        return 1


def _run_daemon_child(token: str | None, run_app: Callable[[], None]) -> None:
    """Register this detached child, then run the normal foreground server."""
    try:
        import psutil

        create_time = psutil.Process(os.getpid()).create_time()
    except Exception as exc:
        print(f"Unable to register NZBPostarr daemon: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    reservation = _read_daemon_state()
    if not token or reservation is None or reservation.get("status") != "starting" or reservation.get("token") != token:
        print("Unable to register NZBPostarr daemon: startup reservation is missing.", file=sys.stderr)
        raise SystemExit(1)
    state = _write_daemon_state(os.getpid(), create_time, token=token)
    try:
        run_app()
    finally:
        _remove_daemon_state(state)
