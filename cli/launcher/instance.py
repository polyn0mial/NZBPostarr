"""Single-instance guard, the configured port, and the WebUI address.

psutil and core.config are imported inside functions: the launcher runs before bootstrap() has built the venv
they come from. msvcrt and fcntl are imported where used because each exists on one platform only.
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path


_INSTANCE_LOCK_HANDLE = None
# The launcher's --port, set by main.py before any port lookup.
PORT_OVERRIDE: int | None = None


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
        # The listener list only enriches the port-in-use message; psutil missing or denied leaves it empty.
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


def configured_port() -> int:
    """Best-effort read of the configured WebUI port."""
    if PORT_OVERRIDE:
        return int(PORT_OVERRIDE)
    try:
        from core.config import get_config

        return int(get_config().port)
    except Exception:
        # Before the venv exists, or with an invalid config, the launcher assumes the default port.
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
