"""Interactive launch-mode selection: tmux sessions, systemd detection, direct mode."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from cli.launcher.bootstrap import PROJECT_NAME, ROOT, VENV_PY
from cli.launcher.instance import get_webui_url, webui_is_listening


TMUX_SESSION_NAME = "nzbpostarr"


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
