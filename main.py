#!/usr/bin/env python3
"""
NZBPostarr - Self-bootstrapping Launcher

Run with: python main.py
- Auto-creates venv and installs deps if needed
- Offers tmux or direct mode
- Reattaches to existing tmux session if running
"""

import argparse
import contextlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


def read_version() -> str:
    """Read the canonical version without importing the application."""
    try:
        from version import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def build_early_parser() -> argparse.ArgumentParser:
    """Build the launcher argument parser."""
    parser = argparse.ArgumentParser(
        prog="nzbpostarr",
        add_help=False,
        description="NZBPostarr launcher. Starts the WebUI, or the headless CLI with --headless.",
        epilog="Run 'python main.py --headless --help' for the full headless command reference.",
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message and exit")
    parser.add_argument(
        "-V",
        "--version",
        action="store_true",
        help="Show the NZBPostarr version and exit",
    )
    parser.add_argument("--direct", action="store_true", help="Run in direct mode (skip tmux selection)")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless CLI mode (no WebUI required)",
    )
    parser.add_argument("--host", default=None, help="Host to bind to")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run the first-run setup wizard",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Start the WebUI in the background",
    )
    parser.add_argument("--stop", action="store_true", help="Stop the background WebUI")
    parser.add_argument("--status", action="store_true", help="Show background WebUI status")
    parser.add_argument("--daemon-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--daemon-token", default=None, help=argparse.SUPPRESS)
    return parser


def parse_early_args() -> argparse.Namespace:
    """Parse launcher flags needed before the application imports.

    Unknown flags are only tolerated in headless mode, where everything after
    ``--headless`` belongs to the headless subparser. Outside headless mode an
    unrecognized flag is an error: silently ignoring it used to start the WebUI,
    so a typo like ``--prot 9000`` bound the default port instead of failing.
    """
    parser = build_early_parser()
    args, unknown = parser.parse_known_args()

    if args.version:
        print(f"NZBPostarr {read_version()}")
        raise SystemExit(0)

    if args.help and not args.headless:
        parser.print_help()
        raise SystemExit(0)

    if unknown and not args.headless:
        parser.print_usage(sys.stderr)
        plural = "s" if len(unknown) > 1 else ""
        print(
            f"nzbpostarr: error: unrecognized argument{plural}: {' '.join(unknown)}",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return args


def is_interactive() -> bool:
    """Check if running in an interactive terminal."""
    return sys.stdin.isatty() and sys.stdout.isatty()


# Parse launcher arguments before project imports.
_early_args = parse_early_args()


# ============================================================
#  CONFIGURATION
# ============================================================
PROJECT_NAME = "NZBPostarr"
TMUX_SESSION_NAME = "nzbpostarr"
_INSTANCE_LOCK_HANDLE = None

# The launcher lives directly in the workspace root.
ROOT = Path(__file__).resolve().parent
if sys.pycache_prefix is None:
    sys.pycache_prefix = str(ROOT / ".local" / "cache" / "pycache")
VENV = ROOT / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQS = ROOT / "requirements.lock"
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


def _daemon_command(token: str) -> list[str]:
    """Build the child invocation without inheriting interactive launcher flags."""
    command = [sys.executable, str(ROOT / "main.py"), "--daemon-child", "--daemon-token", token, "--direct"]
    if _early_args.host:
        command.extend(["--host", str(_early_args.host)])
    if _early_args.port:
        command.extend(["--port", str(_early_args.port)])
    return command


def _start_daemon() -> int:
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

        port = configured_port()
        listeners = _port_listener_details(port)
        if listeners:
            print(f"Refusing to start NZBPostarr daemon: port {port} is already in use.", file=sys.stderr)
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
                process = subprocess.Popen(_daemon_command(token), **popen_kwargs)
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


def _run_daemon_child(token: str | None) -> None:
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


def _lock_file_for_port(port: int) -> Path:
    """Return the single-instance lock path for this WebUI port."""
    # Use a stable path across shells, services, sudo, and detached restarts.
    # XDG_RUNTIME_DIR can differ between launch contexts, which lets two
    # processes take different locks for the same port.
    lock_dir = Path(os.environ.get("NZBPOSTARR_LOCK_DIR") or "/tmp")
    return lock_dir / f"nzbpostarr-{int(port)}.lock"


def _try_lock_file(path: Path) -> bool:
    """Acquire an advisory process lock and keep the file handle alive."""
    global _INSTANCE_LOCK_HANDLE
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    _INSTANCE_LOCK_HANDLE = handle
    return True


def _port_listener_details(port: int) -> list[str]:
    """Return descriptions of processes already listening on the port."""
    details: list[str] = []
    try:
        import psutil

        for conn in psutil.net_connections(kind="tcp"):
            laddr = getattr(conn, "laddr", None)
            if not laddr or getattr(laddr, "port", None) != port:
                continue
            if conn.status != psutil.CONN_LISTEN or conn.pid in (None, os.getpid()):
                continue
            try:
                proc = psutil.Process(conn.pid)
                cmd = " ".join(proc.cmdline())
            except (psutil.Error, OSError):
                cmd = "unknown"
            details.append(f"pid={conn.pid} cmd={cmd}")
    except Exception:
        pass

    if details:
        return details

    # Fallback when psutil is unavailable: detect the port but not the owner.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        if sock.connect_ex(("127.0.0.1", int(port))) == 0:
            return [f"unknown process already accepts connections on port {port}"]
    return []


def acquire_single_instance_guard(host: str, port: int) -> None:
    """Prevent duplicate WebUI processes from starting on the same port."""
    lock_path = _lock_file_for_port(port)
    if not _try_lock_file(lock_path):
        print(f"[startup] Another NZBPostarr instance is already starting/running for port {port}.")
        print(f"[startup] Lock: {lock_path}")
        sys.exit(98)

    listeners = _port_listener_details(port)
    if listeners:
        print(f"[startup] Refusing to start duplicate NZBPostarr on {host}:{port}.")
        for detail in listeners:
            print(f"[startup] Existing listener: {detail}")
        sys.exit(98)


def in_venv() -> bool:
    """Check if running inside virtual environment."""
    return sys.prefix != sys.base_prefix


def in_tmux() -> bool:
    """Check if we're already inside a tmux session."""
    return os.environ.get("TMUX") is not None


def tmux_available() -> bool:
    """Check if tmux is installed."""
    return shutil.which("tmux") is not None


def tmux_session_exists() -> bool:
    """Check if our unique tmux session is running."""
    if not tmux_available():
        return False
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", TMUX_SESSION_NAME],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def configured_port() -> int:
    """Best-effort read of the configured WebUI port."""
    if _early_args.port:
        return int(_early_args.port)
    try:
        from core.config import get_config

        return int(get_config().port)
    except Exception:
        return 8000


def webui_is_listening() -> bool:
    """Check whether something is actually serving the WebUI port.

    A tmux session outliving a crashed app is the common case: create_tmux_session
    parks the pane on a blocking `read` after a failure, so `tmux has-session`
    keeps succeeding long after uvicorn died. Probing the port distinguishes a
    live instance from an empty shell.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            return sock.connect_ex(("127.0.0.1", configured_port())) == 0
    except OSError:
        return False


def systemd_service_active() -> bool:
    """Check whether a systemd unit already supervises the app."""
    if not sys.platform.startswith("linux") or shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "nzbpostarr"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


_SERVER_IP = None


def get_server_ip() -> str:
    """Get the server's primary IP address."""
    global _SERVER_IP
    if _SERVER_IP:
        return str(_SERVER_IP)

    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        _SERVER_IP = str(s.getsockname()[0])
        s.close()
        return _SERVER_IP
    except socket.error:
        return "localhost"


def get_webui_url() -> str:
    """Get the WebUI URL from settings."""
    try:
        from core.config import get_config

        settings = get_config()
        if settings.host == "0.0.0.0":
            host_display = get_server_ip()
        elif settings.host == "127.0.0.1":
            host_display = "localhost"
        else:
            host_display = settings.host
        return f"http://{host_display}:{settings.port}"
    except (ImportError, AttributeError, ValueError):
        return f"http://{get_server_ip()}:8000"


def attach_tmux_session() -> None:
    """Attach to existing tmux session."""
    url = get_webui_url()
    print(f"\n[tmux] Attaching to existing session '{TMUX_SESSION_NAME}'...")
    print(f"  🌐 WebUI: {url}")
    print("  💡 Detach: Ctrl+B then D\n")
    os.execlp("tmux", "tmux", "attach-session", "-t", TMUX_SESSION_NAME)


def create_tmux_session() -> None:
    """Create new tmux session and run the app inside it."""
    url = get_webui_url()
    print(f"\n[tmux] Creating new session '{TMUX_SESSION_NAME}'...")
    print(f"  🌐 WebUI: {url}")
    print("  💡 Detach: Ctrl+B then D\n")

    venv_py_str = str(VENV_PY.resolve()) if VENV_PY.exists() else "python3"
    cmd = (
        f"cd '{ROOT.resolve()}' && '{venv_py_str}' '{ROOT / 'main.py'}' --direct "
        "|| { echo 'Press Enter to exit...'; read; }"
    )
    os.execlp("tmux", "tmux", "new-session", "-s", TMUX_SESSION_NAME, "bash", "-c", cmd)


def _handle_existing_session() -> str:
    """Handle case when tmux session already exists."""
    url = get_webui_url()
    alive = webui_is_listening()
    print(f"  [*] Found existing session: {TMUX_SESSION_NAME}")
    if alive:
        print(f"  🌐 WebUI: {url}")
    else:
        print("  [!] The session exists but the WebUI is NOT responding.")
        print("      The app inside it has most likely exited or crashed.")
    print("=" * 60)
    print("\n  [A] Attach to existing session" + ("" if alive else " (inspect the crash output)"))
    print("  [X] Terminate session (stop server)")
    print("  [K] Kill & restart fresh" + (" (recommended)" if not alive else ""))
    print("  [D] Run direct (no tmux)")
    print("  [Q] Quit\n")

    default_choice = "a" if alive else "k"
    prompt = "  Choice [A/x/k/d/q]: " if alive else "  Choice [a/x/K/d/q]: "

    while True:
        choice = input(prompt).strip().lower() or default_choice
        if choice == "a":
            attach_tmux_session()
        if choice == "x":
            subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION_NAME], check=False)
            print(f"  [*] Terminated session '{TMUX_SESSION_NAME}'")
            sys.exit(0)
        if choice == "k":
            subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION_NAME], check=False)
            print(f"  [*] Killed session '{TMUX_SESSION_NAME}'")
            return "tmux"
        if choice == "d":
            return "direct"
        if choice == "q":
            sys.exit(0)
        print("  Invalid choice. Try again.")


def _handle_new_session() -> str:
    """Handle case when no tmux session exists."""
    print("=" * 60)
    print("\n  [T] Run in tmux (recommended)")
    print("  [D] Run direct (foreground)")
    print("  [Q] Quit\n")

    while True:
        choice = input("  Choice [T/d/q]: ").strip().lower() or "t"
        if choice == "t":
            return "tmux"
        if choice == "d":
            return "direct"
        if choice == "q":
            sys.exit(0)
        print("  Invalid choice. Try again.")


def ask_run_mode() -> str:
    """Ask user for run mode."""
    print("\n" + "=" * 60)
    print(f"  {PROJECT_NAME} - Launch Mode")
    print("=" * 60)

    if systemd_service_active():
        # systemd already provides persistence, restart-on-crash and start-on-boot.
        # Launching a second copy here would only trip the single-instance guard.
        print("  [!] A systemd service 'nzbpostarr' is already running.")
        print("      Manage it with: systemctl status|stop|restart nzbpostarr")
        print("      Starting another instance would fail the single-instance guard.\n")
        sys.exit(0)

    if not tmux_available():
        print("  [!] tmux not found - running in the foreground.")
        if sys.platform.startswith("linux"):
            print("      For a persistent service, re-run: python main.py --setup")
        else:
            print("      Closing this terminal will stop the app.")
        print()
        return "direct"

    if in_tmux():
        return "direct"

    if tmux_session_exists():
        return _handle_existing_session()
    return _handle_new_session()


def bootstrap() -> None:
    """Create venv, install deps, relaunch."""
    print("\n" + "=" * 60)
    print(f"  {PROJECT_NAME} - Bootstrapping")
    print("=" * 60 + "\n")

    if not VENV.exists():
        print("[1/3] Creating .venv...")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    else:
        print("[1/3] .venv exists")

    if REQS.exists():
        # Optimization: Skip pip check if requirements haven't changed since last successful install
        marker = VENV / ".last_pip_check"
        if not marker.exists() or REQS.stat().st_mtime > marker.stat().st_mtime:
            print("[2/3] Installing/Updating requirements (this may take a moment)...")
            subprocess.run(
                [
                    str(VENV_PY),
                    "-m",
                    "pip",
                    "install",
                    "-q",
                    "--disable-pip-version-check",
                    "-r",
                    str(REQS),
                ],
                check=True,
            )
            marker.touch()
        else:
            print("[2/3] Requirements up to date")
    else:
        print(f"[2/3] Warning: {REQS} not found")

    print("[3/3] Relaunching in venv...\n")
    result = subprocess.run([str(VENV_PY), str(ROOT / "main.py"), *sys.argv[1:]], check=False)
    sys.exit(result.returncode)


def run_app() -> None:
    """Run the actual web application."""
    from app import Settings, app

    settings = Settings()
    host = _early_args.host if _early_args.host else settings.host
    port = _early_args.port if _early_args.port else settings.port
    acquire_single_instance_guard(str(host), int(port))

    if host == "0.0.0.0":
        host_display = get_server_ip()
    elif host == "127.0.0.1":
        host_display = "localhost"
    else:
        host_display = host
    url = f"http://{host_display}:{port}"

    print("\n" + "=" * 60)
    print(f"  {PROJECT_NAME}")
    print("=" * 60)
    print(f"  Python:  {sys.executable}")
    if in_tmux():
        print(f"  Tmux:    {TMUX_SESSION_NAME}")
        print("=" * 60)
        print(f"\n  WebUI: {url}")
        print("  Detach: Ctrl+B then D")
    else:
        print("=" * 60)
        print(f"\n  WebUI: {url}")
    print("\n" + "=" * 60 + "\n")

    import uvicorn

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


def _headless_mode() -> None:
    from logic.headless import run_headless

    # Collect all args after --headless for the headless subparser
    argv = sys.argv[:]
    if "--headless" in argv:
        argv.remove("--headless")
    # Also strip other early flags that were already consumed
    for flag in ("--direct",):
        if flag in argv:
            argv.remove(flag)
    # Remove the script name before handing arguments to the headless parser.
    if argv and (argv[0].endswith("main.py") or argv[0].endswith("__main__.py")):
        argv = argv[1:]

    exit_code = run_headless(argv)
    sys.exit(exit_code)


def main() -> None:
    """Entry point."""
    if _early_args.status:
        raise SystemExit(_print_daemon_status())
    if _early_args.stop:
        raise SystemExit(_stop_daemon())
    if _early_args.daemon:
        raise SystemExit(_start_daemon())
    if _early_args.daemon_child:
        _run_daemon_child(_early_args.daemon_token)
        return

    # ── Setup wizard: explicit flag or first-run auto-detect ──
    from core.config import get_config_path

    config_file = get_config_path()
    if _early_args.setup or (not config_file.exists() and is_interactive() and "--headless" not in sys.argv):
        if not _early_args.setup:
            print("\n  No config.yaml found - looks like a first run!")
            print("  Launching setup wizard...\n")
        from setup import main as setup_main

        setup_main()
        return

    # ── Headless mode: bypass all WebUI/tmux logic ──
    if "--headless" in sys.argv or _early_args.headless:
        _headless_mode()
        return

    if "--direct" in sys.argv or _early_args.direct or not is_interactive():
        if "--direct" in sys.argv:
            sys.argv.remove("--direct")
        run_app()
        return

    mode = ask_run_mode()
    if mode == "tmux":
        create_tmux_session()
    else:
        run_app()


if __name__ == "__main__":
    lifecycle_control = "--status" in sys.argv or "--stop" in sys.argv
    if lifecycle_control and not in_venv():
        if VENV_PY.exists():
            os.execv(str(VENV_PY), [str(VENV_PY), str(ROOT / "main.py"), *sys.argv[1:]])
        print("NZBPostarr lifecycle commands require the existing .venv runtime.", file=sys.stderr)
        raise SystemExit(1)
    if not in_venv() and not lifecycle_control:
        bootstrap()
    main()
