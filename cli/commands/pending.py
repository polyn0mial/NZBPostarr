"""`pending`: items waiting to be uploaded."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from cli.output import _emit_pending_category


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


def cmd_pending(args: argparse.Namespace) -> int:
    """Show items pending upload across all categories."""
    from core.db import engine as db_engine
    from core.db import ledger as db_ledger
    from core.config import get_config
    from core.registry import get_registry
    from logic.pending.roots import collect_configured_scan_items, relative_key

    conf = get_config()
    registry = get_registry()
    active_ids = [idx.id for idx in registry.enabled(conf)]
    try:
        uploaded_names, _, _, _ = db_ledger.completion_index(active_ids)
    except db_engine.DatabaseOperationalError as exc:
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
