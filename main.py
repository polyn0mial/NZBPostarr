#!/usr/bin/env python3
"""
NZBPostarr - Self-bootstrapping Launcher

Run with: python main.py
- Auto-creates venv and installs deps if needed
- Offers tmux or direct mode
- Reattaches to existing tmux session if running

The app, uvicorn, core.config and setup are imported inside functions: they need the venv that bootstrap() creates.
"""

import argparse
import os
import sys

from cli.launcher import instance
from cli.launcher.bootstrap import PROJECT_NAME, ROOT, VENV_PY, bootstrap, in_venv
from cli.launcher.daemon import _print_daemon_status, _run_daemon_child, _start_daemon, _stop_daemon
from cli.launcher.instance import acquire_single_instance_guard, get_server_ip
from cli.launcher.tmux import TMUX_SESSION_NAME, ask_run_mode, create_tmux_session, in_tmux


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
instance.PORT_OVERRIDE = _early_args.port

if sys.pycache_prefix is None:
    sys.pycache_prefix = str(ROOT / ".local" / "cache" / "pycache")


def run_app() -> None:
    """Run the actual web application."""
    from app import app
    from core.config import get_config

    settings = get_config()
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
    from cli.run import run_headless

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
        raise SystemExit(_start_daemon(_early_args.host, _early_args.port))
    if _early_args.daemon_child:
        _run_daemon_child(_early_args.daemon_token, run_app)
        return

    # ── Setup wizard: explicit flag or first-run auto-detect ──
    from core.config import get_config_path

    config_file = get_config_path()
    if _early_args.setup or (not config_file.exists() and is_interactive() and not _early_args.headless):
        if not _early_args.setup:
            print("\n  No config.yaml found - looks like a first run!")
            print("  Launching setup wizard...\n")
        from setup import main as setup_main

        setup_main()
        return

    # ── Headless mode: bypass all WebUI/tmux logic ──
    if _early_args.headless:
        _headless_mode()
        return

    if _early_args.direct or not is_interactive():
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
