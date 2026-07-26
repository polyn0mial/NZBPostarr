#!/usr/bin/env python3
"""
NZBPostarr - Self-bootstrapping Launcher

Run with: python main.py
- Auto-creates venv and installs deps if needed
- Offers tmux or direct mode
- Reattaches to existing tmux session if running
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path


def parse_early_args() -> argparse.Namespace:
    """Parse launcher flags needed before the application imports."""
    parser = argparse.ArgumentParser(add_help=False)
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
    args, _ = parser.parse_known_args()
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
    print(f"  [*] Found existing session: {TMUX_SESSION_NAME}")
    print(f"  🌐 WebUI: {url}")
    print("=" * 60)
    print("\n  [A] Attach to existing session")
    print("  [X] Terminate session (stop server)")
    print("  [K] Kill & restart fresh")
    print("  [D] Run direct (no tmux)")
    print("  [Q] Quit\n")

    while True:
        choice = input("  Choice [A/x/k/d/q]: ").strip().lower() or "a"
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

    if not tmux_available():
        print("  [!] tmux not found - running direct mode\n")
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
    # ── Setup wizard: explicit flag or first-run auto-detect ──
    from core.config import get_config_path

    config_file = get_config_path()
    if _early_args.setup or (not config_file.exists() and is_interactive() and "--headless" not in sys.argv):
        if not _early_args.setup:
            print("\n  No config.yaml found — looks like a first run!")
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
    if not in_venv():
        bootstrap()
    main()
