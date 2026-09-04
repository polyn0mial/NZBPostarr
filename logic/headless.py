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
  python main.py --headless queue status
  python main.py --headless queue pause
  python main.py --headless queue job stop <job_id>
  python main.py --headless logs --lines 50
  python main.py --headless --version

Most commands accept --json for machine-readable output.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import argparse
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, List, Optional

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
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "message": f"Unknown category '{category}'.",
                        "available_categories": valid,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            print(f"Error: Unknown category '{category}'.")
            print(f"Available categories: {', '.join(valid)}")
        return 1

    if not args.json:
        print("━" * 60)
        print("  NZBPostarr - Headless Upload")
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

    # Handle Ctrl+C gracefully - UploadService sets the thread job for us,
    # so we hook SIGINT to set stop_requested on whatever job is active.
    service = get_upload_service()
    stop_event = threading.Event()

    def _sigint_handler(_sig: int, _frame: Any) -> None:
        if stop_event.is_set():
            print("\nForce quitting...")
            sys.exit(1)
        print("\nStop requested - finishing current item...")
        stop_event.set()
        # Signal the job via the thread-local job dict
        from core.utils import get_thread_job

        job = get_thread_job()
        if job:
            job["stop_requested"] = True
            job["status"] = "stopping"

    signal.signal(signal.SIGINT, _sigint_handler)

    paths = [args.path] if args.path else None

    # Delegate entirely to UploadService - single source of truth for
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

    if args.json:
        payload = {
            "status": status,
            "category": category,
            "job_id": job.get("job_id"),
            "summary": {
                "duration": summary.get("duration", "-"),
                "processed": summary.get("processed", "0/0"),
                "skipped": summary.get("skipped", 0),
            },
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("\n" + "━" * 60)
        print(f"  Result:      {status.upper()}")
        print(f"  Duration:    {summary.get('duration', '-')}")
        print(f"  Processed:   {summary.get('processed', '0/0')}")
        print(f"  Skipped:     {summary.get('skipped', 0)}")
        print("━" * 60)

    return 0 if status == "completed" else 1


def cmd_status(args: argparse.Namespace) -> int:
    """Show current configuration and system status.

    Exit codes: 0 when every required tool (rar, parpar, nyuu) is found on
    PATH; 1 when any required tool is missing. mediainfo is optional and
    does not affect the exit code.
    """
    from core.config import get_config
    from core.registry import get_registry, resolve_indexer_enabled

    conf = get_config()
    registry = get_registry()
    enabled = registry.enabled(conf)
    all_idx = registry.all()

    import shutil

    required_tools = ["rar", "parpar", "nyuu"]
    tool_paths = {tool: shutil.which(tool) for tool in required_tools + ["mediainfo"]}
    missing_required = [tool for tool in required_tools if not tool_paths[tool]]

    if args.json:
        payload = {
            "nntp_servers": [
                {
                    "name": srv.name,
                    "host": srv.host,
                    "port": srv.port,
                    "enabled": bool(srv.enabled),
                    "max_connections": srv.max_connections,
                }
                for srv in conf.nntp_servers
            ],
            "indexers": {
                "enabled": len(enabled),
                "total": len(all_idx),
                "items": [
                    {"id": idx.id, "name": idx.name, "enabled": resolve_indexer_enabled(idx, conf)}
                    for idx in all_idx
                ],
            },
            "folders": [
                {
                    "path": fp.get("path", "?"),
                    "exists": Path(fp["path"]).exists() if fp.get("path") else False,
                    "monitor": bool(fp.get("monitor")),
                }
                for fp in conf.folder_paths
            ],
            "tools": tool_paths,
            "missing_required_tools": missing_required,
        }
        print(json.dumps(payload, indent=2, default=str))
        return 1 if missing_required else 0

    return _print_status_report(conf, enabled, all_idx, required_tools, tool_paths, missing_required, resolve_indexer_enabled)


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


def _filter_pending_scan_items(
    scanned_items: list[tuple[str, Path, Path]],
    filter_cat: Optional[str],
    filter_folder: str,
) -> list[tuple[str, Path, Path]]:
    """Narrow scanned items down to the requested category/folder, if any."""
    if filter_cat:
        scanned_items = [item for item in scanned_items if item[0] == filter_cat]
    if filter_folder:
        normalized_folder = str(Path(filter_folder))
        scanned_items = [
            item
            for item in scanned_items
            if str(item[1]) == normalized_folder or item[1].name == filter_folder
        ]
    return scanned_items


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
        if not args.json:
            print(f"WARNING: proceeding with empty upload snapshot due to DB error: {exc}")
        uploaded_names = set()

    # Determine which categories/folders to show.
    filter_cat = args.category.lower() if hasattr(args, "category") and args.category else None
    filter_folder = str(getattr(args, "folder", "") or "").strip()
    result_limit = getattr(args, "limit", None)

    scanned_items = _filter_pending_scan_items(collect_configured_scan_items(conf), filter_cat, filter_folder)

    if not scanned_items:
        if args.json:
            payload = {
                "status": "empty",
                "message": "No pending items matched the requested category.",
                "categories": {},
                "total": 0,
                "pending": 0,
            }
            print(json.dumps(payload, indent=2, default=str))
        else:
            print("No pending items matched the requested category.")
        return 1

    grand_total = 0
    grand_pending = 0

    grouped: dict[str, list[tuple[Path, Path, str]]] = {}
    for cat, folder, item in scanned_items:
        rel = relative_key(item, folder)
        grouped.setdefault(cat, []).append((folder, item, rel))

    categories_payload: dict[str, Any] = {}
    for cat in sorted(grouped.keys()):
        total, pending = _emit_pending_category(
            cat, grouped[cat], uploaded_names, result_limit, args, categories_payload
        )
        grand_total += total
        grand_pending += pending

    if args.json:
        payload = {
            "categories": categories_payload,
            "total": grand_total,
            "pending": grand_pending,
            "folder": filter_folder or None,
            "limit": result_limit,
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(f"\n  Total: {grand_pending} pending / {grand_total} items")
    return 0


def _config_path_segments(path: str) -> list[str]:
    """Normalize a dotted path and reject ambiguous empty segments."""
    segments = path.split(".")
    if not path or any(not segment.strip() for segment in segments):
        raise ValueError("configuration keys must contain non-empty dotted path segments")
    return [segment.strip() for segment in segments]


def _config_value_at_path(data: Any, path: str) -> Any:
    """Read a dotted config path without exposing a second config model."""
    current = data
    for segment in _config_path_segments(path):
        if not isinstance(current, dict) or segment not in current:
            raise KeyError(path)
        current = current[segment]
    return current


def _set_config_value_at_path(data: dict[str, Any], path: str, value: Any) -> None:
    """Set a dotted config path, requiring all parent mappings to exist."""
    segments = _config_path_segments(path)
    current: dict[str, Any] = data
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, dict):
            raise KeyError(path)
        current = child
    current[segments[-1]] = value


def cmd_config(args: argparse.Namespace) -> int:
    """Read or update configuration through core.config's synchronized writer."""
    from core.config import get_config, save_config

    command = getattr(args, "config_command", None)
    path = str(getattr(args, "key", "") or "").strip()
    try:
        data = get_config().model_dump(mode="json", by_alias=True)
    except Exception as exc:
        return _emit_result(
            args,
            {"status": "error", "message": f"Unable to load configuration: {exc}"},
            human=f"Error: unable to load configuration: {exc}",
            rc=1,
        )

    if command == "get":
        try:
            value = _config_value_at_path(data, path)
        except ValueError as exc:
            return _emit_result(
                args,
                {"status": "error", "message": str(exc)},
                human=f"Error: {exc}",
                rc=1,
            )
        except KeyError:
            return _emit_result(
                args,
                {"status": "error", "message": f"Configuration key '{path}' was not found."},
                human=f"Error: configuration key '{path}' was not found.",
                rc=1,
            )
        return _emit_result(args, {"key": path, "value": value}, human=str(value))

    if command == "set":
        import yaml

        try:
            value = yaml.safe_load(args.value)
            _set_config_value_at_path(data, path, value)
        except (KeyError, ValueError, yaml.YAMLError) as exc:
            return _emit_result(
                args,
                {"status": "error", "message": f"Invalid configuration update: {exc}"},
                human=f"Error: invalid configuration update: {exc}",
                rc=1,
            )
        if not save_config(data):
            return _emit_result(
                args,
                {"status": "error", "message": "Failed to save configuration."},
                human="Error: failed to save configuration.",
                rc=1,
            )
        return _emit_result(
            args,
            {"status": "updated", "key": path, "value": value, "restart_required": True},
            human=f"Updated {path}. Restart the long-lived NZBPostarr process to apply all watcher changes.",
        )

    return _emit_result(
        args,
        {"status": "error", "message": "No config command given."},
        human="Error: use 'config get' or 'config set'.",
        rc=1,
    )


def _run_launcher_control(flag: str) -> tuple[int, str]:
    """Run one safe launcher lifecycle action in a separate process."""
    import subprocess

    from core.config import APP_ROOT

    completed = subprocess.run(
        [sys.executable, str(APP_ROOT / "main.py"), flag],
        cwd=str(APP_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    detail = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode, detail


def _managed_daemon_is_running() -> bool:
    return _run_launcher_control("--status")[0] == 0


def _restart_managed_daemon(delay_seconds: float = 0.0) -> tuple[bool, str]:
    stop_rc, stop_detail = _run_launcher_control("--stop")
    if stop_rc != 0:
        return False, stop_detail or "The managed daemon could not be stopped."
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    start_rc, start_detail = _run_launcher_control("--daemon")
    if start_rc != 0:
        return False, start_detail or "The managed daemon could not be started."
    return True, start_detail or "NZBPostarr daemon restarted."


def cmd_system(args: argparse.Namespace) -> int:
    """Run deployment controls through the same updater and queue services as the API."""
    from logic import updater
    from logic.services import get_upload_service

    command = getattr(args, "system_command", None)
    update_command = getattr(args, "update_command", None)
    try:
        if command == "update" and update_command == "check":
            payload = updater.check_for_updates()
            if payload.get("check_error"):
                payload["status"] = "error"
                payload["message"] = f"Update check failed: {payload['check_error']}"
                return _emit_result(args, payload, rc=1)
            payload.setdefault("status", "ok")
            return _emit_result(args, payload)
        if command == "update" and update_command == "install":
            payload = updater.install_from_github(version=args.version, restart=False)
            payload["restart_required"] = not args.no_restart
            payload["message"] = (
                "Update installed. Restart NZBPostarr with 'system restart' or your process supervisor."
                if not args.no_restart
                else "Update installed without requesting a restart."
            )
            return _emit_result(args, payload)
        if command == "update" and update_command == "rollback":
            payload = updater.rollback_to_backup(args.backup_id, restart=False)
            payload["restart_required"] = not args.no_restart
            payload["message"] = (
                "Rollback applied. Restart NZBPostarr with 'system restart' or your process supervisor."
                if not args.no_restart
                else "Rollback applied without requesting a restart."
            )
            return _emit_result(args, payload)
        if command == "restart":
            stopped = None
            if not args.no_stop:
                stopped = get_upload_service().stop_all_jobs_and_wait(
                    clear_staged_items=not args.keep_staged_items,
                    wait_timeout_s=args.wait_timeout,
                )
                if stopped.get("timed_out") and not args.force:
                    return _emit_result(
                        args,
                        {
                            "status": "partial",
                            "message": "Work did not stop before the timeout; restart was not attempted. Use --force to proceed.",
                            "stop": stopped,
                            "restart_required": True,
                        },
                        rc=1,
                    )
            if not _managed_daemon_is_running():
                return _emit_result(
                    args,
                    {
                        "status": "restart_required",
                        "message": "No managed daemon is running. Restart NZBPostarr with your process supervisor or operator workflow.",
                        "stop": stopped,
                        "restart_required": True,
                    },
                    rc=1,
                )
            restarted, detail = _restart_managed_daemon(delay_seconds=args.delay)
            return _emit_result(
                args,
                {
                    "status": "restarted" if restarted else "error",
                    "message": detail,
                    "stop": stopped,
                    "restart_required": not restarted,
                },
                rc=0 if restarted else 1,
            )
        if command == "stop-all":
            result = get_upload_service().stop_all_jobs_and_wait(
                clear_staged_items=not args.keep_staged_items,
                wait_timeout_s=args.wait_timeout,
            )
            status = "partial" if result.get("timed_out") else "stopped"
            message = (
                "Stop requested, but some work was still shutting down when the timeout expired."
                if result.get("timed_out")
                else "All uploads stopped and waiting work cleared."
            )
            return _emit_result(
                args,
                {"status": status, "message": message, "stop": result},
                rc=1 if result.get("timed_out") else 0,
            )
    except updater.UpdateError as exc:
        return _emit_result(args, {"status": "error", "message": str(exc)}, human=f"Error: {exc}", rc=1)
    except Exception as exc:
        message = f"System command failed: {exc}"
        return _emit_result(args, {"status": "error", "message": message}, human=f"Error: {message}", rc=1)

    return _emit_result(
        args,
        {"status": "error", "message": "No system command given."},
        human="Error: use 'system --help' to see available commands.",
        rc=1,
    )


def cmd_history(args: argparse.Namespace) -> int:
    """Show recent job history from the database."""
    from core import database

    jobs = database.get_job_history(limit=args.limit)

    if args.json:
        print(json.dumps({"jobs": jobs}, indent=2, default=str))
        return 0

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
            dur_str = "-"
        started = j.get("started_at", "?")
        if isinstance(started, str) and len(started) > 19:
            started = started[:19]

        print(f"  {jid:<12} {cat:<10} {status:<12} {processed:<12} {dur_str:<12} {started}")

    print("━" * 80)
    return 0


def cmd_issues(args: argparse.Namespace) -> int:
    """Show failed indexer submissions grouped into a "known issues" list.

    WHY: with 6+ indexers each free to fail differently, `history` alone shows
    one row per attempt. This groups failures by (indexer, error signature) so
    a recurring problem shows up as one counted row instead of a scroll.
    """
    from core import database

    result = database.get_grouped_upload_errors(
        indexer_id=args.destination,
        limit=args.limit,
        since_days=args.since_days,
    )
    issues = result.get("issues", [])

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0

    if not issues:
        print("No known upload issues found.")
        return 0

    print("━" * 100)
    print(f"  {'Indexer':<14} {'Count':<7} {'Items':<7} {'Last Seen':<20} {'Error'}")
    print("━" * 100)

    for issue in issues:
        indexer = issue.get("indexer_id", "?")
        count = issue.get("count", 0)
        affected = issue.get("affected_item_count", 0)
        last_seen = str(issue.get("last_seen") or "?")[:19]
        sample = str(issue.get("sample_error") or "")[:50]

        print(f"  {indexer:<14} {count:<7} {affected:<7} {last_seen:<20} {sample}")

    print("━" * 100)
    return 0


def cmd_indexers(args: argparse.Namespace) -> int:
    """Show detailed indexer information.

    Exit codes: 0 when at least one indexer is enabled, 1 when none are.
    """
    from core import database
    from core.config import get_config
    from core.registry import get_registry, resolve_indexer_enabled

    conf = get_config()
    registry = get_registry()
    stats = database.get_detailed_stats()
    by_dest = stats.get("uploads", {}).get("by_destination", {})

    rows = []
    enabled_count = 0
    for idx in registry.all():
        is_on = resolve_indexer_enabled(idx, conf)
        if is_on:
            enabled_count += 1
        dest_stats = by_dest.get(idx.id, {})
        cats = idx.categories.supported_categories() if idx.categories else []
        rows.append(
            {
                "id": idx.id,
                "name": idx.name,
                "enabled": is_on,
                "website": idx.website or None,
                "submit_url": idx.submit_url or None,
                "success": dest_stats.get("success", 0),
                "failed": dest_stats.get("failed", 0),
                "categories": cats,
            }
        )

    if args.json:
        payload = {"indexers": rows, "enabled_count": enabled_count}
        print(json.dumps(payload, indent=2, default=str))
        return 1 if enabled_count == 0 else 0

    print("━" * 60)
    print("  NZBPostarr - Indexer Details")
    print("━" * 60)

    for row in rows:
        status = "ENABLED" if row["enabled"] else "DISABLED"
        print(f"\n  {row['name']} ({row['id']}) - {status}")
        print(f"    Website:    {row['website'] or '-'}")
        print(f"    Submit URL: {row['submit_url'] or '-'}")
        print(f"    Uploads:    {row['success']} success, {row['failed']} failed")
        print(f"    Categories: {', '.join(row['categories']) if row['categories'] else '-'}")

    print()
    return 1 if enabled_count == 0 else 0


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
    print("  NZBPostarr - Stream Monitors")
    print("━" * 80)
    for monitor in monitors:
        last_job = monitor.get("last_job") or {}
        print(f"  ID:            {monitor.get('id', '-')}")
        print(f"  Folder:        {monitor.get('folder_path', '-')}")
        print(f"  Category:      {monitor.get('category', 'misc')}")
        print(f"  Posting:       {monitor.get('posting_server_name') or 'default'}")
        print(f"  Submit Mode:   {monitor.get('submit_mode', 'post_and_submit')}")
        print(f"  Indexer:       {monitor.get('indexer_id') or 'all enabled'}")
        print(f"  Test Mode:     {bool(monitor.get('test_mode', False))}")
        print(f"  Dup Check:     {bool(monitor.get('enable_duplicate_check', True))}")
        if last_job:
            print(f"  Last Job:      {last_job.get('job_id', '-')} [{last_job.get('status', 'unknown')}]")
        print("─" * 80)
    return 0


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


def _cmd_queue_status(args: argparse.Namespace, service: Any) -> int:
    """Show running/queued/finished jobs and the queue-pause state.

    Shares build_queue_snapshot with GET /queue so the CLI and the WebUI can
    never disagree about how a job is classified.
    """
    from logic.services import build_queue_snapshot

    payload = build_queue_snapshot(service)
    running = payload["running"]
    queued = payload["queued"]
    finished = payload["finished"]
    control = payload["control"]

    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2, default=str))
        return 0

    print("━" * 60)
    print("  NZBPostarr - Queue Status")
    print("━" * 60)
    print(f"  Queue paused: {bool(control.get('paused'))}")
    active = control.get("active")
    if active:
        print(f"  Active job:   {active.get('job_id')} [{active.get('status')}] ({active.get('category')})")

    for label, jobs in (("Running", running), ("Queued", queued), ("Finished", finished)):
        print(f"\n  {label}: {len(jobs)}")
        for job in jobs:
            print(f"    - {job.get('job_id')} [{job.get('status')}] {job.get('category')} - {job.get('progress') or ''}")

    print("━" * 60)
    return 0


def cmd_queue_job(args: argparse.Namespace, service: Any) -> int:
    """Control a single job by ID. Mirrors the per-job /jobs/{job_id}/* routes."""
    job_command = getattr(args, "job_command", None)
    job_id = getattr(args, "job_id", None)

    if job_command == "pause":
        if not service.pause_job(job_id):
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found or not running"},
                human="Error: job not found or not running",
                rc=1,
            )
        job = service.get_job(job_id) or {}
        pause_pending = bool(job.get("pause_requested")) and job.get("status") == "running"
        return _emit_result(
            args,
            {
                "status": job.get("status", "paused"),
                "job_id": job_id,
                "message": (
                    "Pause requested; waiting for the current safe checkpoint."
                    if pause_pending
                    else "Job paused; the scheduler lane is available."
                ),
            },
        )

    if job_command == "resume":
        if not service.resume_job(job_id):
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found or not paused"},
                human="Error: job not found or not paused",
                rc=1,
            )
        job = service.get_job(job_id) or {}
        queued = bool(job.get("resume_requested"))
        return _emit_result(
            args,
            {
                "status": job.get("status", "running"),
                "job_id": job_id,
                "message": "Job queued to resume when the scheduler lane is available." if queued else "Job resumed.",
            },
        )

    if job_command == "stop":
        clear = getattr(args, "clear", False)
        ok = service.stop_and_clear_job(job_id) if clear else service.stop_job(job_id)
        if not ok:
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found"},
                human="Error: job not found",
                rc=1,
            )
        return _emit_result(
            args,
            {
                "status": "clearing" if clear else "stopping",
                "job_id": job_id,
                "message": "Job stopping and clearing from the queue." if clear else "Termination signal sent to job.",
            },
        )

    if job_command == "retry":
        ok, new_job_id, reason = service.retry_job(job_id)
        if not ok:
            message = "Job not found" if reason == "not-found" else "Job is not eligible for retry"
            return _emit_result(
                args,
                {"status": "error", "message": message},
                human=f"Error: {message}",
                rc=1,
            )
        return _emit_result(
            args,
            {
                "status": "queued",
                "job_id": new_job_id,
                "retry_of": job_id,
                "message": f"Retry queued as {new_job_id}",
            },
        )

    if job_command == "promote":
        if not service.promote_job(job_id):
            return _emit_result(
                args,
                {"status": "error", "message": "Job not found or not queued"},
                human="Error: job not found or not queued",
                rc=1,
            )
        return _emit_result(args, {"status": "promoted", "job_id": job_id})

    print("Error: no per-job command given. Use '--headless queue job --help' to see available commands.")
    return 1


def cmd_queue(args: argparse.Namespace) -> int:
    """Inspect or control the shared upload queue (mirrors the /queue routes).

    Every action here calls the same UploadService/QueueServiceMixin methods
    that app.py's /queue and /jobs routes call - no queue logic lives here.
    """
    from logic.services import get_upload_service

    service = get_upload_service()
    action = getattr(args, "queue_command", None) or "status"

    if action == "status":
        return _cmd_queue_status(args, service)

    if action == "pause":
        service.pause_queue(pause_active=True)
        return _emit_result(
            args,
            {
                "status": "paused",
                "message": "Queue processing paused. New jobs remain queued until resumed.",
                "control": service.get_queue_control_state(),
            },
        )

    if action == "resume":
        service.resume_queue()
        return _emit_result(
            args,
            {
                "status": "running",
                "message": "Queue processing resumed.",
                "control": service.get_queue_control_state(),
            },
        )

    if action == "stop":
        if getattr(args, "clear", False):
            result = service.stop_queue_and_clear()
            payload = {"status": "clearing", "message": "Active job stopping and queue clearing.", **result}
        else:
            service.stop_queue()
            payload = {
                "status": "stopped",
                "message": "Active job stopping. Queue processing is paused.",
                "control": service.get_queue_control_state(),
            }
        return _emit_result(args, payload)

    if action == "clear":
        cleared = service.clear_queued_jobs()
        return _emit_result(args, {"cleared": cleared}, human=f"Cleared {cleared} queued job(s).")

    if action == "revalidate":
        result = service.revalidate_queued_jobs(include_paused=not getattr(args, "skip_paused", False))
        payload = {"status": "success", **result}
        human = f"Inspected {result['inspected']}, updated {result['updated']}, cancelled {result['cancelled']}."
        return _emit_result(args, payload, human=human)

    if action == "job":
        return cmd_queue_job(args, service)

    print("Error: no queue command given. Use '--headless queue --help' to see available commands.")
    return 1


def cmd_logs(args: argparse.Namespace) -> int:
    """Show recent console log lines from the shared in-memory buffer."""
    from logic.services import console

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


def _render_stats_summary(info: dict[str, Any]) -> str:
    from logic.stats_engine import format_seconds

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


def build_headless_parser() -> argparse.ArgumentParser:
    """Build the argument parser for headless mode."""
    parser = argparse.ArgumentParser(
        prog="nzbpostarr --headless",
        description="NZBPostarr Headless CLI - Upload automation without the WebUI.",
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
        help="Test mode - prepare and submit but skip actual NNTP upload",
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
    p_upload.add_argument(
        "--json",
        action="store_true",
        help="Output the job result as JSON",
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
    p_status = sub.add_parser(
        "status",
        help="Show system status (config, tools, indexers). Exit 1 if a required tool is missing",
    )
    p_status.add_argument(
        "--json",
        action="store_true",
        help="Output the status snapshot as JSON",
    )

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
    p_pending.add_argument(
        "--folder",
        default=None,
        help="Restrict results to a configured folder path or folder name",
    )
    p_pending.add_argument(
        "--limit",
        type=_non_negative_int,
        default=None,
        help="Maximum pending items listed per category when using --verbose",
    )
    p_pending.add_argument(
        "--json",
        action="store_true",
        help="Output the pending summary as JSON",
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
    p_history.add_argument(
        "--json",
        action="store_true",
        help="Output job history as JSON",
    )

    # ── issues ──
    p_issues = sub.add_parser(
        "issues",
        help="Show failed indexer submissions grouped into a known-issues list",
    )
    p_issues.add_argument(
        "--destination",
        default="all",
        help="Limit to one indexer ID, or 'all' (default)",
    )
    p_issues.add_argument(
        "--limit",
        "-l",
        type=int,
        default=50,
        help="Maximum number of issue groups to show (default: 50)",
    )
    p_issues.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Only count failures from the last N days (default: all history)",
    )
    p_issues.add_argument(
        "--json",
        action="store_true",
        help="Output grouped issues as JSON",
    )

    # ── indexers ──
    p_indexers = sub.add_parser(
        "indexers",
        help="Show indexer details and stats. Exit 1 if no indexer is enabled",
    )
    p_indexers.add_argument(
        "--json",
        action="store_true",
        help="Output indexer details as JSON",
    )

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

    # ── queue ──
    p_queue = sub.add_parser(
        "queue",
        help="Inspect or control the shared upload queue (same actions as the WebUI)",
    )
    queue_sub = p_queue.add_subparsers(dest="queue_command", help="Queue commands")

    p_queue_status = queue_sub.add_parser("status", help="Show running, queued, and finished jobs (default)")
    p_queue_status.add_argument("--json", action="store_true", help="Output the queue snapshot as JSON")

    p_queue_pause = queue_sub.add_parser("pause", help="Pause queue processing and the active job")
    p_queue_pause.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_resume = queue_sub.add_parser("resume", help="Resume queue processing")
    p_queue_resume.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_stop = queue_sub.add_parser("stop", help="Stop the active job and pause the queue")
    p_queue_stop.add_argument(
        "--clear",
        action="store_true",
        help="Also clear queued jobs and hide the active row while it stops",
    )
    p_queue_stop.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_clear = queue_sub.add_parser("clear", help="Cancel all queued (not yet running) jobs")
    p_queue_clear.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_revalidate = queue_sub.add_parser(
        "revalidate",
        help="Re-scan queued/paused jobs against the current classification rules",
    )
    p_queue_revalidate.add_argument(
        "--skip-paused",
        action="store_true",
        help="Only revalidate queued jobs; leave paused jobs untouched",
    )
    p_queue_revalidate.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_queue_job = queue_sub.add_parser(
        "job",
        help="Control a single job by ID (same actions as the per-job WebUI buttons)",
    )
    job_sub = p_queue_job.add_subparsers(dest="job_command", help="Per-job commands")

    p_job_pause = job_sub.add_parser("pause", help="Pause a running job")
    p_job_pause.add_argument("job_id", help="Job ID")
    p_job_pause.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_resume = job_sub.add_parser("resume", help="Resume a paused job")
    p_job_resume.add_argument("job_id", help="Job ID")
    p_job_resume.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_stop = job_sub.add_parser("stop", help="Stop a job")
    p_job_stop.add_argument("job_id", help="Job ID")
    p_job_stop.add_argument(
        "--clear",
        action="store_true",
        help="Also remove the job from the queue once stopped",
    )
    p_job_stop.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_retry = job_sub.add_parser("retry", help="Queue a new attempt from a failed job's saved request")
    p_job_retry.add_argument("job_id", help="Job ID")
    p_job_retry.add_argument("--json", action="store_true", help="Output the result as JSON")

    p_job_promote = job_sub.add_parser("promote", help="Move a queued job to the front of the queue")
    p_job_promote.add_argument("job_id", help="Job ID")
    p_job_promote.add_argument("--json", action="store_true", help="Output the result as JSON")

    # ── config ──
    p_config = sub.add_parser("config", help="Read or update a configuration value")
    config_sub = p_config.add_subparsers(dest="config_command", help="Configuration commands")
    p_config_get = config_sub.add_parser("get", help="Read a dotted configuration key")
    p_config_get.add_argument("key", help="Configuration key, for example host or api_keys.geek")
    p_config_get.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_config_set = config_sub.add_parser("set", help="Update a dotted configuration key")
    p_config_set.add_argument("key", help="Configuration key, for example debug or api_keys.geek")
    p_config_set.add_argument("value", help="YAML scalar, list, or mapping value")
    p_config_set.add_argument("--json", action="store_true", help="Output the result as JSON")

    # ── system ──
    p_system = sub.add_parser("system", help="Run update and service lifecycle controls")
    system_sub = p_system.add_subparsers(dest="system_command", help="System commands")
    p_system_update = system_sub.add_parser("update", help="Check, install, or roll back application updates")
    update_sub = p_system_update.add_subparsers(dest="update_command", help="Update commands")
    p_update_check = update_sub.add_parser("check", help="Check GitHub for an update now")
    p_update_check.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_update_install = update_sub.add_parser("install", help="Install the latest or selected GitHub release")
    p_update_install.add_argument("--version", default=None, help="Release version or tag to install")
    p_update_install.add_argument("--no-restart", action="store_true", help="Do not restart after installation")
    p_update_install.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_update_rollback = update_sub.add_parser("rollback", help="Restore a local updater backup")
    p_update_rollback.add_argument("backup_id", help="Backup ID returned by the updater")
    p_update_rollback.add_argument("--no-restart", action="store_true", help="Do not restart after rollback")
    p_update_rollback.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_system_restart = system_sub.add_parser("restart", help="Stop work safely and schedule a restart")
    p_system_restart.add_argument(
        "--delay",
        type=_restart_delay,
        default=2.0,
        help="Seconds to wait between stopping and starting a managed daemon (default: 2)",
    )
    p_system_restart.add_argument("--no-stop", action="store_true", help="Do not stop active or queued jobs first")
    p_system_restart.add_argument(
        "--force",
        action="store_true",
        help="Restart a managed daemon even when graceful job shutdown times out",
    )
    p_system_restart.add_argument("--keep-staged-items", action="store_true", help="Keep staged temporary items")
    p_system_restart.add_argument("--wait-timeout", type=float, default=30.0, help="Seconds to wait for jobs to stop")
    p_system_restart.add_argument("--json", action="store_true", help="Output the result as JSON")
    p_system_stop = system_sub.add_parser("stop-all", help="Stop active jobs, clear waiting work, and wait until quiet")
    p_system_stop.add_argument("--keep-staged-items", action="store_true", help="Keep staged temporary items")
    p_system_stop.add_argument("--wait-timeout", type=float, default=30.0, help="Seconds to wait for jobs to stop")
    p_system_stop.add_argument("--json", action="store_true", help="Output the result as JSON")

    # ── logs ──
    p_logs = sub.add_parser("logs", help="Show recent console log lines from the shared in-memory buffer")
    p_logs.add_argument(
        "--lines",
        "-n",
        type=int,
        default=100,
        help="Number of recent lines to show (default: 100)",
    )
    p_logs.add_argument(
        "--json",
        action="store_true",
        help="Output log entries as JSON",
    )

    # ── version ──
    from version import __version__

    parser.add_argument(
        "--version",
        action="version",
        version=f"NZBPostarr {__version__}",
        help="Show the NZBPostarr version and exit",
    )

    return parser


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


def run_headless(argv: List[str]) -> int:
    """Entry point for headless mode. Called from main.py."""
    _ensure_utf8_console()
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
