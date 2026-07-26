"""
📦 NZBPostarr - Headless CLI Runner
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
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, List

# ============================================================
#  CLI COMMANDS
# ============================================================


def cmd_upload(args: argparse.Namespace) -> int:
    """Run an upload job synchronously via UploadService (blocking until complete)."""
    from core.registry import get_available_categories
    from logic.services import get_upload_service

    category = args.category.lower()

    # Validate category
    active_cats = [cat["id"] for cat in get_available_categories()]
    valid = list(dict.fromkeys(active_cats + ["all", "both"]))

    if category not in valid:
        print(f"Error: Unknown category '{category}'.")
        print(f"Available categories: {', '.join(valid)}")
        return 1

    print("━" * 60)
    print("  NZBPostarr — Headless Upload")
    print("━" * 60)
    print(f"  Category:    {category.upper()}")
    print(f"  Limit:       {args.limit or 'All'}")
    print(f"  Test Mode:   {args.test}")
    print(f"  Force:       {args.force}")
    if args.indexer:
        print(f"  Indexer:     {args.indexer}")
    if args.path:
        print(f"  Path:        {args.path}")
    print("━" * 60 + "\n")

    # Handle Ctrl+C gracefully — UploadService sets the thread job for us,
    # so we hook SIGINT to set stop_requested on whatever job is active.
    service = get_upload_service()
    stop_event = threading.Event()

    def _sigint_handler(_sig: int, _frame: Any) -> None:
        if stop_event.is_set():
            print("\nForce quitting...")
            sys.exit(1)
        print("\nStop requested — finishing current item...")
        stop_event.set()
        # Signal the job via the thread-local job dict
        from core.utils import get_thread_job

        job = get_thread_job()
        if job:
            job["stop_requested"] = True
            job["status"] = "stopping"

    signal.signal(signal.SIGINT, _sigint_handler)

    paths = [args.path] if args.path else None

    # Delegate entirely to UploadService — single source of truth for
    # job creation, force-flag resolution, processing, and history recording.
    try:
        job = service.run_job_sync(
            category=category,
            limit=args.limit,
            skip_packs=args.skip_packs,
            skip_episodes=args.skip_episodes,
            force=args.force,
            test_mode=args.test,
            indexer_id=args.indexer,
            paths=paths,
        )
    except KeyboardInterrupt:
        job = {
            "status": "stopped",
            "items_processed": 0,
            "items_total": 0,
            "items_skipped": 0,
        }

    status = job.get("status", "completed")
    summary = job.get("summary", {})

    print("\n" + "━" * 60)
    print(f"  Result:      {status.upper()}")
    print(f"  Duration:    {summary.get('duration', '—')}")
    print(f"  Processed:   {summary.get('processed', '0/0')}")
    print(f"  Skipped:     {summary.get('skipped', 0)}")
    print("━" * 60)

    return 0 if status == "completed" else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current configuration and system status."""
    from core.config import get_config
    from core.registry import get_registry

    conf = get_config()
    registry = get_registry()

    print("━" * 60)
    print("  NZBPostarr — System Status")
    print("━" * 60)

    # NNTP Servers
    print(f"\n  NNTP Servers: {len(conf.nntp_servers)}")
    for srv in conf.nntp_servers:
        status = "enabled" if srv.enabled else "disabled"
        print(f"    • {srv.name} ({srv.host}:{srv.port}) [{status}] [{srv.max_connections} conns]")

    # Indexers
    enabled = registry.enabled(conf)
    all_idx = registry.all()
    print(f"\n  Indexers: {len(enabled)}/{len(all_idx)} enabled")
    for idx in all_idx:
        from core.registry import resolve_indexer_enabled

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
    import shutil

    print("\n  Required Tools:")
    for tool in ["rar", "parpar", "nyuu", "mediainfo"]:
        found = shutil.which(tool)
        mark = "✓" if found else "✗"
        print(f"    {mark} {tool}: {found or 'NOT FOUND'}")

    print()
    return 0


def cmd_pending(args: argparse.Namespace) -> int:
    """Show items pending upload across all categories."""
    from core import database
    from core.config import get_config
    from core.registry import get_registry
    from logic.pending_scan import (
        collect_configured_scan_items,
        relative_key,
    )

    conf = get_config()
    registry = get_registry()
    active_ids = [idx.id for idx in registry.enabled(conf)]
    try:
        uploaded_names, _, _ = database.get_dashboard_data(active_ids)
    except database.DatabaseOperationalError as exc:
        print(f"WARNING: proceeding with empty upload snapshot due to DB error: {exc}")
        uploaded_names = set()

    # Determine which categories to show
    filter_cat = args.category.lower() if hasattr(args, "category") and args.category else None

    scanned_items = collect_configured_scan_items(conf)
    if filter_cat:
        scanned_items = [item for item in scanned_items if item[0] == filter_cat]

    if not scanned_items:
        print("No pending items matched the requested category.")
        return 1

    grand_total = 0
    grand_pending = 0

    grouped: dict[str, list[tuple[Path, Path, str]]] = {}
    for cat, folder, item in scanned_items:
        rel = relative_key(item, folder)
        grouped.setdefault(cat, []).append((folder, item, rel))

    for cat in sorted(grouped.keys()):
        items = grouped[cat]
        pending = [
            (folder, item, rel)
            for folder, item, rel in items
            if rel not in uploaded_names and item.name not in uploaded_names
        ]

        grand_total += len(items)
        grand_pending += len(pending)

        print(f"\n  [{cat.upper()}] {len(pending)} pending / {len(items)} total")
        if args.verbose and pending:
            for folder, _item, rel in pending[:50]:
                print(f"    • {folder.name}/{rel}")
            if len(pending) > 50:
                print(f"    ... and {len(pending) - 50} more")

    print(f"\n  Total: {grand_pending} pending / {grand_total} items")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Show recent job history from the database."""
    from core import database

    jobs = database.get_job_history(limit=args.limit)

    if not jobs:
        print("No job history found.")
        return 0

    print("━" * 80)
    print(f"  {'Job ID':<12} {'Category':<10} {'Status':<12} {'Processed':<12} {'Duration':<12} {'Started'}")
    print("━" * 80)

    for j in jobs:
        jid = j.get("job_id", "?")[:10]
        cat = j.get("category", "?")
        status = j.get("status", "?")
        processed = f"{j.get('items_processed', 0)}/{j.get('items_total', 0)}"
        dur = j.get("duration_seconds")
        if dur:
            from logic.stats_engine import format_seconds

            dur_str = format_seconds(dur)
        else:
            dur_str = "—"
        started = j.get("started_at", "?")
        if isinstance(started, str) and len(started) > 19:
            started = started[:19]

        print(f"  {jid:<12} {cat:<10} {status:<12} {processed:<12} {dur_str:<12} {started}")

    print("━" * 80)
    return 0


def cmd_indexers(args: argparse.Namespace) -> int:
    """Show detailed indexer information."""
    from core import database
    from core.config import get_config
    from core.registry import get_registry, resolve_indexer_enabled

    conf = get_config()
    registry = get_registry()
    stats = database.get_detailed_stats()
    by_dest = stats.get("uploads", {}).get("by_destination", {})

    print("━" * 60)
    print("  NZBPostarr — Indexer Details")
    print("━" * 60)

    for idx in registry.all():
        is_on = resolve_indexer_enabled(idx, conf)
        status = "ENABLED" if is_on else "DISABLED"
        dest_stats = by_dest.get(idx.id, {})
        success = dest_stats.get("success", 0)
        failed = dest_stats.get("failed", 0)

        print(f"\n  {idx.name} ({idx.id}) — {status}")
        print(f"    Website:    {idx.website or '—'}")
        print(f"    Submit URL: {idx.submit_url or '—'}")
        print(f"    Uploads:    {success} success, {failed} failed")
        cats = idx.categories.supported_categories() if idx.categories else []
        print(f"    Categories: {', '.join(cats) if cats else '—'}")

    print()
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    """Queue one-shot NZB stream/repost jobs or save a stream monitor definition."""
    from logic import usenet_stream
    from logic.services import get_upload_service

    submit_mode = usenet_stream.normalize_submit_mode(args.submit_mode)
    source = str(args.source).strip()

    try:
        if args.monitor:
            monitor = usenet_stream.add_stream_monitor(
                folder_path=source,
                category=args.category,
                posting_server_name=args.posting_server,
                submit_mode=submit_mode,
                indexer_id=args.indexer,
                enable_duplicate_check=not args.skip_duplicate_check,
                test_mode=args.test,
            )
            payload = {
                "status": "monitoring",
                "mode": "monitor",
                "message": f"Saved stream monitor for {monitor['folder_path']}",
                "monitor": monitor,
            }
            if args.json:
                print(json.dumps(payload, indent=2, default=str))
            else:
                print("━" * 60)
                print("  NZBPostarr — Stream Monitor Saved")
                print("━" * 60)
                print(f"  ID:            {monitor.get('id', '—')}")
                print(f"  Folder:        {monitor.get('folder_path', '—')}")
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
            return 0

        paths = usenet_stream.resolve_source_nzb_paths(source)
        service = get_upload_service()
        job_ids: list[str] = []

        if len(paths) > 1 and args.release_name:
            print("Note: ignoring --release-name for multiple NZB files.")

        for path in paths:
            job_ids.append(
                service.start_usenet_stream_job(
                    category=args.category,
                    stream_source_path=str(path),
                    stream_source_name=path.name,
                    release_name=args.release_name if len(paths) == 1 else None,
                    test_mode=args.test,
                    enable_duplicate_check=not args.skip_duplicate_check,
                    indexer_id=args.indexer,
                    posting_server_name=args.posting_server,
                    submit_mode=submit_mode,
                )
            )

        payload = {
            "status": "started",
            "mode": "batch" if len(job_ids) > 1 else "job",
            "job_id": job_ids[0] if len(job_ids) == 1 else None,
            "job_ids": job_ids,
            "source_count": len(paths),
            "message": f"Queued {len(job_ids)} stream job(s)",
        }
        if args.json:
            print(json.dumps(payload, indent=2, default=str))
        else:
            print("━" * 60)
            print("  NZBPostarr — Stream Jobs Queued")
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
        return 0
    except usenet_stream.StreamError as exc:
        print(f"Error: {exc}")
        return 1


def cmd_stream_monitors(args: argparse.Namespace) -> int:
    """List or remove saved stream monitor definitions."""
    from logic import usenet_stream

    action = getattr(args, "monitor_command", None) or "list"

    if action == "remove":
        if not usenet_stream.remove_stream_monitor(args.monitor_id):
            print(f"Error: stream monitor '{args.monitor_id}' was not found.")
            return 1
        print(f"Removed stream monitor {args.monitor_id}.")
        print("Changes apply to the next long-lived app/WebUI run, or after that process is restarted.")
        return 0

    monitors = usenet_stream.list_stream_monitors()
    if getattr(args, "json", False):
        print(json.dumps(monitors, indent=2, default=str))
        return 0

    if not monitors:
        print("No stream monitors configured.")
        return 0

    print("━" * 80)
    print("  NZBPostarr — Stream Monitors")
    print("━" * 80)
    for monitor in monitors:
        last_job = monitor.get("last_job") or {}
        print(f"  ID:            {monitor.get('id', '—')}")
        print(f"  Folder:        {monitor.get('folder_path', '—')}")
        print(f"  Category:      {monitor.get('category', 'misc')}")
        print(f"  Posting:       {monitor.get('posting_server_name') or 'default'}")
        print(f"  Submit Mode:   {monitor.get('submit_mode', 'post_and_submit')}")
        print(f"  Indexer:       {monitor.get('indexer_id') or 'all enabled'}")
        print(f"  Test Mode:     {bool(monitor.get('test_mode', False))}")
        print(f"  Dup Check:     {bool(monitor.get('enable_duplicate_check', True))}")
        if last_job:
            print(f"  Last Job:      {last_job.get('job_id', '—')} [{last_job.get('status', 'unknown')}]")
        print("─" * 80)
    return 0


def _render_stats_summary(info: dict[str, Any]) -> str:
    from logic.stats_engine import format_seconds

    cpu = info.get("cpu", {})
    memory = info.get("memory", {})
    disk = info.get("disk", {})
    network = info.get("network", {})

    lines = [
        "━" * 60,
        "  NZBPostarr — Live Stats",
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


# ============================================================
#  ARGPARSE SETUP
# ============================================================


def build_headless_parser() -> argparse.ArgumentParser:
    """Build the argument parser for headless mode."""
    parser = argparse.ArgumentParser(
        prog="nzbpostarr --headless",
        description="NZBPostarr Headless CLI — Upload automation without the WebUI.",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # ── upload ──
    p_upload = sub.add_parser("upload", help="Run an upload job")
    p_upload.add_argument(
        "category",
        help="Category to upload (tv, movies, misc, all, both)",
    )
    p_upload.add_argument(
        "--limit",
        "-l",
        type=int,
        default=None,
        help="Max items to process",
    )
    p_upload.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Test mode — prepare and submit but skip actual NNTP upload",
    )
    p_upload.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force re-upload (skip duplicate checking)",
    )
    p_upload.add_argument(
        "--indexer",
        "-i",
        default=None,
        help="Target a specific indexer by ID (e.g., geek, planet)",
    )
    p_upload.add_argument(
        "--skip-packs",
        action="store_true",
        help="Skip season packs (TV only)",
    )
    p_upload.add_argument(
        "--skip-episodes",
        action="store_true",
        help="Skip individual episodes (TV only)",
    )
    p_upload.add_argument(
        "--path",
        "-p",
        default=None,
        help="Upload a specific file or folder path",
    )

    # ── stream ──
    p_stream = sub.add_parser("stream", help="Queue direct NZB stream/repost jobs")
    p_stream.add_argument("source", help="Server-side NZB file or folder path")
    p_stream.add_argument(
        "--category",
        "-c",
        default="misc",
        help="Target category label for the queued stream job (default: misc)",
    )
    p_stream.add_argument(
        "--release-name",
        default=None,
        help="Override the release/job name for a single NZB source",
    )
    p_stream.add_argument(
        "--submit-mode",
        choices=["post_and_submit", "post_only"],
        default="post_and_submit",
        help="Post only, or post and submit to indexers (default: post_and_submit)",
    )
    p_stream.add_argument(
        "--posting-server",
        default=None,
        help="Specific enabled NNTP posting server name to use",
    )
    p_stream.add_argument(
        "--indexer",
        "-i",
        default=None,
        help="Submit to a specific indexer ID instead of all enabled indexers",
    )
    p_stream.add_argument(
        "--test",
        "-t",
        action="store_true",
        help="Prepare the stream job but skip the actual NNTP upload",
    )
    p_stream.add_argument(
        "--skip-duplicate-check",
        action="store_true",
        help="Skip duplicate checking before the repost job runs",
    )
    p_stream.add_argument(
        "--monitor",
        action="store_true",
        help="Save a watched-folder stream monitor instead of queuing immediate jobs",
    )
    p_stream.add_argument(
        "--json",
        action="store_true",
        help="Output the queued job or monitor result as JSON",
    )

    # ── status ──
    sub.add_parser("status", help="Show system status (config, tools, indexers)")

    # ── pending ──
    p_pending = sub.add_parser("pending", help="Show items pending upload")
    p_pending.add_argument(
        "category",
        nargs="?",
        default=None,
        help="Filter by category (optional)",
    )
    p_pending.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="List individual pending items",
    )

    # ── history ──
    p_history = sub.add_parser("history", help="Show recent job history")
    p_history.add_argument(
        "--limit",
        "-l",
        type=int,
        default=20,
        help="Number of jobs to show (default: 20)",
    )

    # ── indexers ──
    sub.add_parser("indexers", help="Show indexer details and stats")

    # ── stream monitors ──
    p_stream_monitors = sub.add_parser(
        "stream-monitors",
        help="List or remove saved stream monitor definitions",
    )
    sm_sub = p_stream_monitors.add_subparsers(dest="monitor_command", help="Monitor commands")
    p_stream_monitors.add_argument(
        "--json",
        action="store_true",
        help="Output monitor listings as JSON",
    )
    sm_sub.add_parser("list", help="List saved stream monitor definitions")
    p_monitor_remove = sm_sub.add_parser("remove", help="Remove a saved stream monitor definition")
    p_monitor_remove.add_argument("monitor_id", help="Stream monitor ID to remove")

    # ── stats ──
    p_stats = sub.add_parser("stats", help="Show an on-demand live system stats snapshot")
    p_stats.add_argument(
        "--json",
        action="store_true",
        help="Output the snapshot as JSON",
    )
    p_stats.add_argument(
        "--watch",
        action="store_true",
        help="Continuously print refreshed snapshots until Ctrl+C",
    )
    p_stats.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between refreshes when using --watch (default: 2.0)",
    )
    p_stats.add_argument(
        "--sample-seconds",
        type=float,
        default=0.25,
        help="Sampling window used to estimate CPU/network/disk rates (default: 0.25)",
    )

    return parser


def run_headless(argv: List[str]) -> int:
    """Entry point for headless mode. Called from main.py."""
    parser = build_headless_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    # Shared init: database, indexer registry, console buffer, tmp cleanup
    from logic.services import init_app

    init_app()

    dispatch = {
        "upload": cmd_upload,
        "stream": cmd_stream,
        "status": cmd_status,
        "pending": cmd_pending,
        "history": cmd_history,
        "indexers": cmd_indexers,
        "stream-monitors": cmd_stream_monitors,
        "stats": cmd_stats,
    }

    handler = dispatch.get(args.command)
    if handler:
        return handler(args)

    parser.print_help()
    return 0
