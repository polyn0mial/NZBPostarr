"""Shared CLI output helpers: result emitters, argparse value types, console setup and renderers.

App modules are imported inside the functions that use them, so the CLI starts without loading the app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


def _emit_result(args: argparse.Namespace, payload: dict[str, Any], human: str | None = None, rc: int = 0) -> int:
    """Print a queue/job command result as JSON or a short human summary."""
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
        return rc

    if human is not None:
        print(human)
    else:
        print(payload.get("message") or payload.get("status", "done"))
        control = payload.get("control")
        if isinstance(control, dict):
            print(f"  Queue paused: {bool(control.get('paused'))}")
            active = control.get("active")
            if active:
                print(f"  Active job:   {active.get('job_id')} [{active.get('status')}] ({active.get('category')})")
    return rc


def _print_status_report(conf, enabled, all_idx, required_tools, tool_paths, missing_required, resolve_indexer_enabled) -> int:
    """Print the human-readable status report (non-JSON branch of cmd_status)."""
    print("━" * 60)
    print("  NZBPostarr - System Status")
    print("━" * 60)

    # NNTP Servers
    print(f"\n  NNTP Servers: {len(conf.nntp_servers)}")
    for srv in conf.nntp_servers:
        status = "enabled" if srv.enabled else "disabled"
        print(f"    • {srv.name} ({srv.host}:{srv.port}) [{status}] [{srv.max_connections} conns]")

    # Indexers
    print(f"\n  Indexers: {len(enabled)}/{len(all_idx)} enabled")
    for idx in all_idx:
        is_on = resolve_indexer_enabled(idx, conf)
        mark = "✓" if is_on else "✗"
        print(f"    {mark} {idx.name} ({idx.id})")

    # Configured folders
    print("\n  Configured Folders:")
    for fp in conf.folder_paths:
        path = fp.get("path", "?")
        exists = Path(path).exists() if path else False
        mark = "✓" if exists else "✗"
        monitor = " [monitor]" if fp.get("monitor") else ""
        print(f"    {mark} {path}{monitor}")

    # Tools
    print("\n  Required Tools:")
    for tool in required_tools + ["mediainfo"]:
        found = tool_paths[tool]
        mark = "✓" if found else "✗"
        print(f"    {mark} {tool}: {found or 'NOT FOUND'}")

    print()
    return 1 if missing_required else 0


def _emit_pending_category(
    cat: str,
    items: list[tuple[Path, Path, str]],
    uploaded_names: set,
    result_limit: Optional[int],
    args: argparse.Namespace,
    categories_payload: dict[str, Any],
) -> tuple[int, int]:
    """Print/record one category's pending summary; returns (total, pending)."""
    pending = [
        (folder, item, rel)
        for folder, item, rel in items
        if rel not in uploaded_names and item.name not in uploaded_names
    ]

    displayed_pending = pending[:result_limit] if result_limit is not None else pending

    if args.json:
        entry: dict[str, Any] = {"pending": len(pending), "total": len(items)}
        if args.verbose:
            entry["items"] = [f"{folder.name}/{rel}" for folder, _item, rel in displayed_pending]
        categories_payload[cat] = entry
    else:
        print(f"\n  [{cat.upper()}] {len(pending)} pending / {len(items)} total")
        if args.verbose and pending:
            for folder, _item, rel in displayed_pending:
                print(f"    • {folder.name}/{rel}")
            if len(displayed_pending) < len(pending):
                print(f"    ... and {len(pending) - len(displayed_pending)} more")

    return len(items), len(pending)


def _render_stats_summary(info: dict[str, Any]) -> str:
    from logic.stats.collector import format_seconds

    cpu = info.get("cpu", {})
    memory = info.get("memory", {})
    disk = info.get("disk", {})
    network = info.get("network", {})

    lines = [
        "━" * 60,
        "  NZBPostarr - Live Stats",
        "━" * 60,
        f"  Host:        {info.get('hostname', '?')} ({info.get('platform', '?')})",
        f"  Uptime:      {format_seconds(info.get('uptime_seconds', 0) or 0)}",
        f"  CPU:         {cpu.get('percent', 0):.1f}%",
        (
            f"  Memory:      {memory.get('used_gb', 0):.2f} / {memory.get('total_gb', 0):.2f} GiB "
            f"({memory.get('percent', 0):.1f}%)"
        ),
        (
            f"  Disk:        {disk.get('used_gb', 0):.2f} / {disk.get('total_gb', 0):.2f} GiB "
            f"({disk.get('percent', 0):.1f}%)"
        ),
        f"  Free Space:  {disk.get('free_gb', 0):.2f} GiB",
        f"  Upload:      {network.get('upload_mbps', 0):.2f} MiB/s",
        f"  Download:    {network.get('download_mbps', 0):.2f} MiB/s",
        f"  Connections: {network.get('connections', 0)}",
        (
            f"  Net Errors:  in={network.get('errors_in', 0)} out={network.get('errors_out', 0)} "
            f"dropin={network.get('drops_in', 0)} dropout={network.get('drops_out', 0)}"
        ),
        "━" * 60,
    ]
    return "\n".join(lines)


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def _restart_delay(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 30:
        raise argparse.ArgumentTypeError("must be between 0 and 30 seconds")
    return parsed


def _ensure_utf8_console() -> None:
    """Make the CLI's box-drawing and status glyphs safe on a legacy console.

    Every human-readable command draws rules with U+2501/U+2500 and marks state
    with checks and crosses. On a Windows console defaulting to cp1252 those are
    unencodable, so printing them raised UnicodeEncodeError and killed the
    command outright. Reconfiguring to UTF-8 fixes it; errors="replace" is a
    backstop so an odd glyph can never crash a command again.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def _print_stream_monitor_saved(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    """Render a saved stream monitor as JSON or the human summary."""
    monitor = payload["monitor"]
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("━" * 60)
        print("  NZBPostarr - Stream Monitor Saved")
        print("━" * 60)
        print(f"  ID:            {monitor.get('id', '-')}")
        print(f"  Folder:        {monitor.get('folder_path', '-')}")
        print(f"  Category:      {monitor.get('category', 'misc')}")
        print(f"  Posting:       {monitor.get('posting_server_name') or 'default'}")
        print(f"  Submit Mode:   {monitor.get('submit_mode', 'post_and_submit')}")
        print(f"  Indexer:       {monitor.get('indexer_id') or 'all enabled'}")
        print(f"  Test Mode:     {bool(monitor.get('test_mode', False))}")
        print(f"  Dup Check:     {bool(monitor.get('enable_duplicate_check', True))}")
        print("━" * 60)
        print(
            "  Note: monitor definitions are saved now, but active folder watching only runs in the long-lived app/WebUI process."
        )


def _print_stream_jobs_queued(args: argparse.Namespace, payload: dict[str, Any], source: str, submit_mode: str) -> None:
    """Render queued stream jobs as JSON or the human summary."""
    job_ids = payload["job_ids"]
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("━" * 60)
        print("  NZBPostarr - Stream Jobs Queued")
        print("━" * 60)
        print(f"  Source:        {source}")
        print(f"  Category:      {args.category}")
        print(f"  Submit Mode:   {submit_mode}")
        print(f"  Posting:       {args.posting_server or 'default'}")
        print(f"  Indexer:       {args.indexer or 'all enabled'}")
        print(f"  Test Mode:     {args.test}")
        print(f"  Dup Check:     {not args.skip_duplicate_check}")
        print(f"  Jobs:          {len(job_ids)}")
        for job_id in job_ids:
            print(f"    • {job_id}")
        print("━" * 60)
