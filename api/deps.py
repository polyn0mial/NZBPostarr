"""Request helpers shared by several API modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger

from fastapi import HTTPException

from core.config import StatsFeatures, get_config

# The 404 detail each disabled stats feature answers with.
_FEATURE_DISABLED_DETAIL: Dict[str, str] = {
    "stats_page": "Stats page is disabled",
    "dashboard": "Dashboard server stats are disabled",
    "history": "Stats history is disabled",
}


def stats_features(conf: Optional[Any] = None) -> StatsFeatures:
    return StatsFeatures.from_config(conf or get_config())

def feature_enabled(name: str, conf: Optional[Any] = None) -> bool:
    return bool(getattr(stats_features(conf), name))

def check_feature(name: str) -> None:
    """Answer 404 while the named stats feature is off."""
    if not feature_enabled(name):
        raise HTTPException(status_code=404, detail=_FEATURE_DISABLED_DETAIL[name])

def require_feature(name: str) -> Callable[[], None]:
    """check_feature as a FastAPI dependency."""
    if name not in _FEATURE_DISABLED_DETAIL:
        raise KeyError(f"Unknown stats feature: {name}")

    def _require() -> None:
        check_feature(name)

    return _require

def _stats_collector_required(conf: Optional[Any] = None) -> bool:
    return feature_enabled("history", conf)

async def _sync_stats_collector_state(conf: Optional[Any] = None) -> None:
    current = conf or get_config()
    from logic.stats.collector import (
        start_collector,
        stop_collector,
        sync_collector_schedule,
    )

    if _stats_collector_required(current):
        await start_collector()
    else:
        await stop_collector()
    sync_collector_schedule()

def _resolved_policy_path(value: Any) -> Path:
    path = Path(str(value or "").strip())
    try:
        return path.resolve()
    except OSError:
        return path.absolute()

def _normalize_request_strings(values: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: Set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return normalized

def _bulk_selection_excluded_roots(conf: Any) -> tuple[Path, ...]:
    """Return configured roots which must not participate in mass selection."""
    get_entries = getattr(conf, "get_folder_path_entries", None)
    entries = get_entries() if callable(get_entries) else getattr(conf, "folder_paths", [])
    roots: list[Path] = []
    for entry in entries or []:
        if not isinstance(entry, dict) or bool(entry.get("allow_bulk_selection", True)):
            continue
        raw_path = str(entry.get("path") or "").strip()
        if raw_path:
            roots.append(_resolved_policy_path(raw_path))
    return tuple(roots)

def _path_is_at_or_below(path_value: Any, root: Path) -> bool:
    candidate = _resolved_policy_path(path_value)
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True

def _log_selected_payload(prefix: str, items: List[Dict[str, Any]]) -> None:
    """Emit concise selection logs for queued/forced uploads."""
    for item in items:
        raw_path = str(item.get("path") or "").strip()
        if not raw_path:
            continue
        name = str(item.get("name") or Path(raw_path).name)
        category = str(item.get("category") or "").strip().lower() or "unknown"
        detected = str(item.get("detected_category") or category or "unknown").strip().lower()
        method = str(item.get("detection_method") or "UI selection").strip()
        reason = str(item.get("selection_reason") or "").strip()
        logger.info(
            f"[{prefix}] Selected '{name}' -> category={category} detected={detected} method={method}"
            f"{f' reason={reason}' if reason else ''}"
        )

def _filter_bulk_selectable_items(
    items: List[Dict[str, Any]],
    conf: Any,
) -> tuple[List[Dict[str, Any]], int]:
    """Apply per-root mass-selection policy without restricting manual actions."""
    excluded_roots = _bulk_selection_excluded_roots(conf)
    if not excluded_roots:
        return list(items), 0

    allowed: List[Dict[str, Any]] = []
    excluded_count = 0
    for item in items:
        raw_path = str(item.get("path") or "").strip()
        if raw_path and any(_path_is_at_or_below(raw_path, root) for root in excluded_roots):
            excluded_count += 1
            continue
        allowed.append(item)
    return allowed, excluded_count
