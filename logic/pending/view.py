"""Pending payload shaping: compaction, sorting, summaries and filters."""

from __future__ import annotations

import json
import zlib
from typing import Any, Dict, List, Optional, Set

from core.config import get_config
from core.paths import path_key
from logic.queue_metrics import (
    count_pending_indexer_slots,
    incomplete_pending_indexer_ids,
    log_queue_update,
    required_pending_indexer_ids,
)


def _external_group_order_key(group: Dict[str, Any]) -> str:
    raw_key = str(
        group.get("key")
        or group.get("folder_path")
        or group.get("folder_name")
        or group.get("id")
        or ""
    ).strip()
    if not raw_key:
        return ""
    normalized_key = raw_key.replace("\\", "/").rstrip("/").casefold()
    return f"external:{normalized_key}"

def _sort_external_groups(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    conf = get_config()
    raw_order = getattr(conf, "pending_external_group_order", []) or []
    if not isinstance(raw_order, list):
        raw_order = []
    order_map = {str(key): idx for idx, key in enumerate(raw_order) if str(key).strip()}
    if not order_map:
        return groups

    def sort_key(group: Dict[str, Any]) -> tuple[int, str]:
        key = _external_group_order_key(group)
        order_index = order_map.get(key, len(order_map))
        label = str(group.get("folder_name") or group.get("key") or key or "").casefold()
        return order_index, label

    return sorted(groups, key=sort_key)

def _strip_external_helper_fields(node: Dict[str, Any]) -> None:
    node.pop("_direct_indexers", None)
    node.pop("_pack_via_children", None)
    node.pop("_deferred_children_done", None)
    node.pop("_deferred_child_indexers", None)
    for child in node.get("children", []) or []:
        if isinstance(child, dict):
            _strip_external_helper_fields(child)

def _compact_external_tree_item(
    item: Dict[str, Any],
    metadata_by_path: Optional[Dict[str, Dict[str, Any]]] = None,
) -> str:
    """Drop retained descendants while preserving a compact search document.

    Full descendants are needed transiently while classification and completion
    state are rolled up. The Queue page fetches them lazily, so retaining every
    descendant dictionary after the scan duplicates filesystem state and costs
    substantially more memory than the searchable names themselves.
    """
    searchable_names: list[str] = []
    stack: list[Dict[str, Any]] = [item]
    while stack:
        node = stack.pop()
        name = str(node.get("name") or "").strip()
        if name:
            searchable_names.append(name)
        children = node.get("children")
        if isinstance(children, list):
            stack.extend(child for child in children if isinstance(child, dict))
        if metadata_by_path is not None and node is not item:
            path_identity = path_key(str(node.get("path") or ""))
            if path_identity:
                metadata_by_path[path_identity] = {
                    key: value
                    for key, value in node.items()
                    if key not in {"children", "files"}
                }

    if isinstance(item.get("children"), list) or isinstance(item.get("files"), list):
        item["children"] = []
        item["files"] = []
    return "\n".join(searchable_names)

def _compact_external_groups(groups: List[Dict[str, Any]]) -> tuple[Dict[str, str], bytes]:
    """Compact external rows and return private search and metadata indexes."""
    search_index: Dict[str, str] = {}
    metadata_by_path: Dict[str, Dict[str, Any]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        for item in group.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            search_text = _compact_external_tree_item(item, metadata_by_path)
            item_key = str(item.get("key") or "")
            if item_key and search_text:
                search_index[item_key] = search_text
    metadata_json = json.dumps(
        metadata_by_path,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return search_index, zlib.compress(metadata_json, level=6)

def _iter_pending_summary_rows(items: Dict[str, Any]) -> List[tuple[str, Dict[str, Any]]]:
    rows: List[tuple[str, Dict[str, Any]]] = []
    for category_key, category_items in items.items():
        if category_key in ("tv", "external") or not isinstance(category_items, list):
            continue
        for item in category_items:
            if isinstance(item, dict):
                rows.append((category_key, item))
    for group in items.get("external", []) if isinstance(items.get("external"), list) else []:
        if not isinstance(group, dict):
            continue
        for item in group.get("items", []) or []:
            if isinstance(item, dict):
                rows.append(("external", item))
    return rows

def empty_pending_summary() -> Dict[str, int]:
    """Return the stable pending-summary response shape with zero counts."""
    return {
        "tv_shows": 0,
        "tv_episodes": 0,
        "movies": 0,
        "misc": 0,
        "external": 0,
        "item_total": 0,
        "task_total": 0,
        "red_indexers": 0,
        "total": 0,
    }

def build_pending_summary(items: Dict[str, Any], indexers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    """Build flat pending counters for external and dynamic categories."""
    movies_items = items.get("movies", []) if isinstance(items.get("movies"), list) else []
    misc_items = items.get("misc", []) if isinstance(items.get("misc"), list) else []
    external_groups = items.get("external", []) if isinstance(items.get("external"), list) else []

    movies_pending = len(movies_items)
    misc_pending = len(misc_items)
    external_pending = sum(len(group.get("items", [])) for group in external_groups if isinstance(group, dict))

    flat_total = 0
    flat_summary: Dict[str, int] = {}
    for category_key, category_items in items.items():
        if category_key in ("tv", "external"):
            continue
        if isinstance(category_items, list):
            count = len(category_items)
            flat_summary[category_key] = count
            flat_total += count

    item_total = flat_total + external_pending
    required_ids = required_pending_indexer_ids(indexers or [])
    task_total = 0
    red_indexer_ids: Set[str] = set()
    for _category_key, row in _iter_pending_summary_rows(items):
        row_status = row.get("indexers") if isinstance(row, dict) else None
        task_total += count_pending_indexer_slots(row_status, required_ids)
        red_indexer_ids.update(incomplete_pending_indexer_ids(row_status, required_ids))

    log_queue_update(red_indexers=len(red_indexer_ids), total_tasks=task_total)
    return {
        **empty_pending_summary(),
        "movies": movies_pending,
        "misc": misc_pending,
        "external": external_pending,
        "item_total": item_total,
        "task_total": task_total,
        "red_indexers": len(red_indexer_ids),
        "total": task_total,
        **{key: value for key, value in flat_summary.items() if key not in ("movies", "misc")},
    }

def _resolve_filter_categories(category: str, source: Dict[str, List[Any]]) -> List[str]:
    if category == "all":
        return list(source.keys())
    if category == "tv":
        return [c for c in ["tv", "external"] if c in source]
    if category in source:
        return [category]
    return []

def _is_match(raw_query: Optional[str], target: str, literal: bool) -> bool:
    """Shared search-match rule for every category branch below.

    Kept as a plain module-level helper (not a nested closure) so its own
    branches are what get measured, rather than folding into whichever
    category function calls it.
    """
    if not raw_query:
        return True
    if literal:
        return raw_query.lower() in target.lower()
    target_clean = target.lower()
    query_clean = raw_query.lower()
    for token in "._-[]()":
        target_clean = target_clean.replace(token, " ")
        query_clean = query_clean.replace(token, " ")
    words = [word for word in query_clean.split() if word]
    return not words or all(word in target_clean for word in words)

def _filter_tv_season(
    season: Dict[str, Any], query: Optional[str], literal: bool, indexer_ids: List[str]
) -> Optional[tuple]:
    """Return (filtered_season, size, episode_count, indexer_status), or None if the
    season has no matching items."""
    season_items = season.get("items") or []
    keep_items = [item for item in season_items if _is_match(query, item.get("name", ""), literal)]
    if not keep_items:
        return None

    season_status: Dict[str, bool] = {idx_id: True for idx_id in indexer_ids}
    for item in keep_items:
        item_status = item.get("indexers") or {}
        for idx_id in indexer_ids:
            if not item_status.get(idx_id, False):
                season_status[idx_id] = False

    season_size = sum(int(item.get("size") or 0) for item in keep_items)
    season_episodes = sum(1 for item in keep_items if item.get("itype") == "TV Episode")
    filtered_season = {
        **season,
        "items": keep_items,
        "indexers": season_status,
        "size": season_size,
        "episode_count": season_episodes,
        "expanded": False,
    }
    return filtered_season, season_size, season_episodes, season_status

def _filter_tv_show(
    show: Dict[str, Any], query: Optional[str], literal: bool, indexer_ids: List[str]
) -> Optional[Dict[str, Any]]:
    if not query or _is_match(query, show.get("name", ""), literal):
        return show

    keep_seasons = []
    show_size = 0
    show_episodes = 0
    show_status: Dict[str, bool] = {idx_id: True for idx_id in indexer_ids}
    for season in show.get("seasons") or []:
        result = _filter_tv_season(season, query, literal, indexer_ids)
        if result is None:
            continue
        filtered_season, season_size, season_episodes, season_status = result
        keep_seasons.append(filtered_season)
        show_size += season_size
        show_episodes += season_episodes
        for idx_id in indexer_ids:
            if not season_status[idx_id]:
                show_status[idx_id] = False

    if not keep_seasons:
        return None
    return {
        **show,
        "seasons": keep_seasons,
        "indexers": show_status,
        "size": show_size,
        "episode_count": show_episodes,
    }

def _filter_tv_category(
    shows: List[Dict[str, Any]], query: Optional[str], literal: bool, indexer_ids: List[str]
) -> List[Dict[str, Any]]:
    out = []
    for show in shows:
        filtered_show = _filter_tv_show(show, query, literal, indexer_ids)
        if filtered_show is not None:
            out.append(filtered_show)
    return out

def _filter_external_category(
    groups: List[Dict[str, Any]],
    category: str,
    query: Optional[str],
    literal: bool,
    external_search_index: Dict[str, Any],
) -> List[Dict[str, Any]]:
    detected_filter = category if category not in ("all", "external") else None
    out = []
    for group in groups:
        items_pool = group.get("items", [])
        if detected_filter:
            if group.get("source_category") == detected_filter:
                items_pool = list(items_pool)
            else:
                items_pool = [i for i in items_pool if i.get("detected_category") == detected_filter]
        if not items_pool:
            continue
        if not query:
            out.append({**group, "items": items_pool})
            continue
        keep_items = [
            item
            for item in items_pool
            if _is_match(
                query,
                str(external_search_index.get(str(item.get("key") or "")) or item.get("name", "")),
                literal,
            )
        ]
        if keep_items:
            out.append({**group, "items": keep_items})
    return out

def _filter_generic_category(
    items: List[Dict[str, Any]], query: Optional[str], literal: bool
) -> List[Dict[str, Any]]:
    return [item for item in items if not query or _is_match(query, item["name"], literal)]

def filter_pending_snapshot(
    data: Dict[str, Any], search: Optional[str], category: str, literal: bool = False
) -> Dict[str, Any]:
    """Apply search and category filters to a cached pending snapshot."""
    if not data:
        return {
            "items": {"tv": [], "movies": [], "misc": [], "external": []},
            "indexers": [],
            "summary": empty_pending_summary(),
            "categories": data.get("categories", []) if data else [],
            "indexer_status_available": data.get("indexer_status_available", True) if data else True,
            "db_error": data.get("db_error") if data else None,
        }

    source = data["items"]
    all_categories = list(source.keys())
    categories = _resolve_filter_categories(category, source)
    filtered: Dict[str, List[Any]] = {category_key: [] for category_key in all_categories}
    query = search.strip() if search else None
    indexer_ids: List[str] = [
        str(indexer.get("id"))
        for indexer in (data.get("indexers") or [])
        if isinstance(indexer, dict) and indexer.get("id")
    ]
    external_search_index = data.get("_external_search_index")
    if not isinstance(external_search_index, dict):
        external_search_index = {}

    for category_key in categories:
        if category_key not in source:
            continue
        if category_key == "tv":
            filtered["tv"] = _filter_tv_category(source[category_key], query, literal, indexer_ids)
        elif category_key == "external":
            filtered["external"] = _filter_external_category(
                source.get(category_key, []), category, query, literal, external_search_index
            )
        else:
            filtered[category_key] = _filter_generic_category(source[category_key], query, literal)

    return {
        "items": filtered,
        "indexers": data["indexers"],
        "summary": build_pending_summary(
            filtered,
            (data.get("indexers") or []) if data.get("indexer_status_available", True) else [],
        ),
        "cached_at": data.get("cached_at"),
        "skip_files": data.get("skip_files", {"enabled": False, "display_mode": "disabled"}),
        "categories": data.get("categories", []),
        "indexer_status_available": data.get("indexer_status_available", True),
        "db_error": data.get("db_error"),
    }


def empty_dashboard_pending() -> dict[str, Any]:
    return {
        "movies": 0,
        "tv": 0,
        "misc": 0,
        "tv_episodes_pending": 0,
        "tv_episodes_complete": 0,
        "movies_complete": 0,
        "tv_complete": 0,
        "misc_complete": 0,
        "total_tasks": 0,
        "red_indexers": 0,
    }

def iter_dashboard_snapshot_rows(snapshot: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items = snapshot.get("items") if isinstance(snapshot, dict) else {}
    if not isinstance(items, dict):
        return []

    rows: list[tuple[str, dict[str, Any]]] = []
    for show in items.get("tv", []) if isinstance(items.get("tv"), list) else []:
        if isinstance(show, dict):
            rows.append(("tv", show))

    for category, cat_items in items.items():
        if category in {"tv", "external"} or not isinstance(cat_items, list):
            continue
        for item in cat_items:
            if isinstance(item, dict):
                rows.append((category, item))

    for group in items.get("external", []) if isinstance(items.get("external"), list) else []:
        if not isinstance(group, dict):
            continue
        for item in group.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            effective_category = str(item.get("detected_category") or "").strip().lower()
            if not effective_category:
                continue
            rows.append((effective_category, item))

    return rows

def build_dashboard_pending_from_snapshot(
    snapshot: dict[str, Any],
    indexers: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    pending = empty_dashboard_pending()
    breakdown: dict[str, dict[str, dict[str, int]]] = {}
    active_ids = [
        str(indexer.get("id") or "").strip()
        for indexer in indexers
        if isinstance(indexer, dict) and str(indexer.get("id") or "").strip()
    ]
    required_ids = required_pending_indexer_ids(indexers)
    task_totals: dict[str, int] = {}
    red_indexer_ids: set[str] = set()

    for category, row in iter_dashboard_snapshot_rows(snapshot):
        status_map = row.get("indexers") if isinstance(row.get("indexers"), dict) else {}
        row_completed = bool(row.get("completed", False))

        if category not in pending:
            pending[category] = 0
            pending[f"{category}_complete"] = 0

        if row_completed:
            pending[f"{category}_complete"] = pending.get(f"{category}_complete", 0) + 1
        else:
            pending[category] = pending.get(category, 0) + 1

        if category == "tv":
            for season in row.get("seasons") or []:
                if not isinstance(season, dict):
                    continue
                for item in season.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("itype") not in ("TV Episode", "Anime"):
                        continue
                    if bool(item.get("completed", False)):
                        pending["tv_episodes_complete"] += 1
                    else:
                        pending["tv_episodes_pending"] += 1

        task_totals[category] = task_totals.get(category, 0) + count_pending_indexer_slots(status_map, required_ids)
        red_indexer_ids.update(incomplete_pending_indexer_ids(status_map, required_ids))

        category_breakdown = breakdown.setdefault(
            category,
            {idx_id: {"complete": 0, "pending": 0} for idx_id in active_ids},
        )
        for idx_id in active_ids:
            if status_map.get(idx_id) is True:
                category_breakdown[idx_id]["complete"] += 1
            else:
                category_breakdown[idx_id]["pending"] += 1

    for category, total in task_totals.items():
        pending[f"{category}_tasks"] = total
    pending["total_tasks"] = sum(task_totals.values())
    pending["red_indexers"] = len(red_indexer_ids)
    return pending, breakdown


def build_dashboard_summary(conf: Any, stats: dict[str, Any], pending_state: Any) -> dict[str, Any]:
    """Shape the dashboard summary from live stats and the pending-index state."""
    snapshot = pending_state.get("snapshot") if isinstance(pending_state, dict) else None
    upload_stats = stats.get("uploads", {})
    by_dest = upload_stats.get("by_destination", {}) if isinstance(upload_stats, dict) else {}
    indexers = list(snapshot.get("indexers") or []) if isinstance(snapshot, dict) else []
    pending, breakdown = build_dashboard_pending_from_snapshot(snapshot or {}, indexers)

    indexer_list = []
    for indexer in indexers:
        if not isinstance(indexer, dict):
            continue
        idx_id = str(indexer.get("id") or "").strip()
        if not idx_id:
            continue
        indexer_list.append({**indexer, "stats": by_dest.get(idx_id, {"success": 0, "failed": 0})})

    return {
        "uploads": upload_stats,
        "performance": stats.get("performance", {}),
        "pending": pending,
        "breakdown": breakdown,
        "poster_name": conf.poster_name,
        "ui_refresh_seconds": conf.ui_refresh_seconds,
        "nntp_status": "Online" if conf.nntp_servers else "Offline",
        "indexers": indexer_list,
        "summary_ready": snapshot is not None and bool(pending_state.get("ready", False)),
        "db_error": snapshot.get("db_error") if isinstance(snapshot, dict) else None,
    }
