"""`stats`: an on-demand live system stats snapshot."""

from __future__ import annotations

import argparse
import json
import time

from cli.output import _render_stats_summary


def cmd_stats(args: argparse.Namespace) -> int:
    """Show an on-demand system stats snapshot in CLI mode."""
    from logic.stats_engine import collect_instant_system_info

    sample_seconds = max(0.05, float(args.sample_seconds))
    watch_interval = max(0.25, float(args.interval))

    def _emit() -> None:
        snapshot = collect_instant_system_info(interval_seconds=sample_seconds)
        if args.json:
            print(json.dumps(snapshot, indent=2, default=str))
        else:
            print(_render_stats_summary(snapshot))

    try:
        _emit()
        while args.watch:
            time.sleep(watch_interval)
            print()
            _emit()
    except KeyboardInterrupt:
        if args.watch:
            print("\nStopped.")
        return 0

    return 0
