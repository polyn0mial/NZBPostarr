"""`logs`: recent console lines from the shared buffer."""

from __future__ import annotations

import argparse
import json


def cmd_logs(args: argparse.Namespace) -> int:
    """Show recent console log lines from the shared in-memory buffer."""
    from core.logging import console

    logs, last_seq = console.get_tail(args.lines)

    if args.json:
        print(json.dumps({"logs": logs, "last_seq": last_seq, "count": len(logs)}, indent=2, default=str))
        return 0

    if not logs:
        print("No log entries yet.")
        return 0

    for entry in logs:
        ts = entry.get("ts", "")
        level = entry.get("level", "INFO")
        msg = entry.get("msg", "")
        print(f"[{ts}] {level:<8} {msg}")
    return 0
