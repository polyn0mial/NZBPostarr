"""
NZBPostarr - Headless CLI Runner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Provides a full CLI interface for running uploads without the WebUI.
The entire webui/ folder can be deleted when using this mode.

Usage examples:
  python main.py --headless upload tv
  python main.py --headless upload movies --limit 5
  python main.py --headless upload all --test
    python main.py --headless stream /path/to/file.nzb
    python main.py --headless stream /path/to/folder --monitor
    python main.py --headless stream-monitors list
    python main.py --headless stats
  python main.py --headless status
  python main.py --headless pending
  python main.py --headless history
  python main.py --headless indexers
  python main.py --headless queue status
  python main.py --headless queue pause
  python main.py --headless queue job stop <job_id>
  python main.py --headless logs --lines 50
  python main.py --headless --version

Most commands accept --json for machine-readable output.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

from typing import List

from cli.commands.config import cmd_config
from cli.commands.history import cmd_history, cmd_indexers, cmd_issues
from cli.commands.logs import cmd_logs
from cli.commands.pending import cmd_pending
from cli.commands.queue import cmd_queue
from cli.commands.stats import cmd_stats
from cli.commands.status import cmd_status
from cli.commands.stream import cmd_stream, cmd_stream_monitors
from cli.commands.system import cmd_system
from cli.commands.upload import cmd_upload
from cli.daemon_client import run_via_daemon
from cli.output import _ensure_utf8_console
from cli.parser import build_headless_parser


def run_headless(argv: List[str]) -> int:
    """Entry point for headless mode. Called from main.py."""
    _ensure_utf8_console()
    parser = build_headless_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # A mutating command goes to the running WebUI so the CLI never builds a second engine.
    routed = run_via_daemon(args)
    if routed is not None:
        return routed

    # Shared init: database, indexer registry, console buffer, tmp cleanup
    from logic.runtime import init_core

    init_core()

    dispatch = {
        "upload": cmd_upload,
        "stream": cmd_stream,
        "status": cmd_status,
        "pending": cmd_pending,
        "history": cmd_history,
        "issues": cmd_issues,
        "indexers": cmd_indexers,
        "stream-monitors": cmd_stream_monitors,
        "stats": cmd_stats,
        "queue": cmd_queue,
        "config": cmd_config,
        "system": cmd_system,
        "logs": cmd_logs,
    }

    handler = dispatch.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 0
