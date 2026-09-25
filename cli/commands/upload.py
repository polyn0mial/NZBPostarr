"""`upload`: run an upload job in the foreground.

App modules are imported inside the functions that use them, so the CLI starts without loading the app.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from typing import Any


def cmd_upload(args: argparse.Namespace) -> int:
    """Run an upload job synchronously via the JobEngine (blocking until complete)."""
    from core.indexers.categories import get_available_categories
    from logic.runtime import ensure_engine_started

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

    # Handle Ctrl+C gracefully - the JobEngine sets the thread job for us,
    # so we hook SIGINT to set stop_requested on whatever job is active.
    service = ensure_engine_started()
    stop_event = threading.Event()

    def _sigint_handler(_sig: int, _frame: Any) -> None:
        if stop_event.is_set():
            print("\nForce quitting...")
            sys.exit(1)
        print("\nStop requested - finishing current item...")
        stop_event.set()
        # Signal the job via the thread-local job dict
        from logic.jobs.context import get_thread_job

        job = get_thread_job()
        if job:
            job["stop_requested"] = True
            job["status"] = "stopping"

    signal.signal(signal.SIGINT, _sigint_handler)

    paths = [args.path] if args.path else None

    # Delegate entirely to the JobEngine - single source of truth for
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
