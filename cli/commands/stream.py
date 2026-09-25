"""`stream` and `stream-monitors`: direct NZB repost jobs and saved monitors."""

from __future__ import annotations

import argparse
import json

from cli.output import _print_stream_jobs_queued, _print_stream_monitor_saved


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
            _print_stream_monitor_saved(args, payload)
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
        _print_stream_jobs_queued(args, payload, source, submit_mode)
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
