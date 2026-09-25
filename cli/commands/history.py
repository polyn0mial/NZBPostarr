"""`history`, `issues` and `indexers`: read-only job and indexer reports."""

from __future__ import annotations

import argparse
import json


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
