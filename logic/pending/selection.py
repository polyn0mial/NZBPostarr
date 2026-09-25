"""The one bulk-selectable decision: ignored reasons and selection state for every row."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Set

from logic.pending.rules import _SOURCE_EXEMPT_NAME_RE
from core.media import default_itype
from core.paths import path_key


def upload_itype(node: Dict[str, Any]) -> str:
    """Return the server's upload itype verdict for one pending row (the browser never derives it)."""
    itype = str(node.get("itype") or "").strip()
    if itype and itype != "External":
        return itype
    category = str(node.get("detected_category") or node.get("category") or "")
    return default_itype(category, is_dir=bool(node.get("is_dir")))


def stamp_upload_itype(node: Any) -> None:
    """Stamp the additive ``upload_itype`` field on one pending row."""
    if isinstance(node, dict):
        node["upload_itype"] = upload_itype(node)


def _row_source_exempt(node: Dict[str, Any]) -> bool:
    category = str(node.get("detected_category") or node.get("category") or "").strip().lower()
    itype = str(node.get("itype") or "").strip().lower()
    name_text = f"{node.get('name') or ''} {node.get('path') or ''}".strip()
    if _SOURCE_EXEMPT_NAME_RE.search(name_text):
        return True
    return category in {"music", "books", "ebooks", "audiobooks", "disc"} or itype in {
        "music",
        "ebook",
        "audiobook",
        "disc",
    }

def _resolve_node_ignored_reason(node: Dict[str, Any], ignored_reason: Optional[str]) -> str:
    if (
        ignored_reason
        and _row_source_exempt(node)
        and ignored_reason.lower().startswith("missing media source")
    ):
        return ""
    return ignored_reason or ""

def _annotate_selection_node(
    node: Dict[str, Any],
    node_identity: str,
    selectable: Set[str],
    ignored: Dict[str, str],
    child_selected: bool,
) -> bool:
    """Set auto-select fields on one tree node; returns whether it (or a descendant) is selected."""
    ignored_reason = _resolve_node_ignored_reason(node, ignored.get(node_identity))
    node_selected = node_identity in selectable
    # A pack/folder is valid (selectable) if it has ≥1 valid episode child
    is_dir = bool(node.get("is_dir"))
    children = [c for c in node.get("children", []) or [] if isinstance(c, dict)]
    is_leaf_file = not is_dir and not children
    leaf_fallback_selected = is_leaf_file and not ignored_reason
    if is_dir and child_selected and ignored_reason:
        ignored_reason = ""
    effectively_selected = node_selected or leaf_fallback_selected or (is_dir and child_selected)

    node["auto_selectable"] = bool(effectively_selected)
    node["auto_select_ignored"] = bool(ignored_reason)
    # Track whether this directory became selectable via its children
    # (pack folder) vs being directly in queue_paths (movie folder).
    if not node_selected and is_dir and child_selected:
        node["_pack_via_children"] = True
    if ignored_reason:
        node["auto_select_reason"] = ignored_reason
    elif child_selected and not node_selected and not is_dir:
        node["auto_select_reason"] = "Selectable descendants only"
    else:
        node.pop("auto_select_reason", None)

    return node_selected or child_selected or leaf_fallback_selected

def _stamp_tree_selection_state(item: Dict[str, Any], resolution: Any) -> bool:
    """Annotate tree nodes with auto-select metadata from explicit-path resolution."""
    selectable = {path_key(path) for path in getattr(resolution, "queue_paths", ())}
    ignored = {path_key(entry.path): entry.reason for entry in getattr(resolution, "ignored_paths", ())}

    def visit(node: Dict[str, Any]) -> bool:
        child_selected = False
        built_children = node.get("children", []) or []
        for child in built_children:
            child_selected = visit(child) or child_selected
        node_identity = path_key(node.get("path", ""))
        if node.get("is_dir") and not built_children:
            # Lazy tree: children are not built yet, so look for selectable
            # descendants among the resolved queue paths instead.
            prefix = node_identity.rstrip("\\/") + os.sep
            child_selected = any(path.startswith(prefix) for path in selectable)
        return _annotate_selection_node(node, node_identity, selectable, ignored, child_selected)

    has_selectable = visit(item)
    if not has_selectable and ignored:
        item["auto_selectable"] = False
        item["auto_select_ignored"] = True
        item["auto_select_reason"] = next(iter(ignored.values()))
    return has_selectable

def _stamp_lazy_children_selection(children: List[Dict[str, Any]], resolution: Any) -> None:
    """Stamp auto-select state on one lazily loaded level from its parent's resolution."""
    selectable = {path_key(path) for path in getattr(resolution, "queue_paths", ()) or ()}
    ignored = {
        path_key(entry.path): entry.reason for entry in getattr(resolution, "ignored_paths", ()) or ()
    }
    for child in children:
        identity = path_key(child.get("path", ""))
        prefix = identity.rstrip("\\/") + os.sep
        descendant_selected = bool(child.get("is_dir")) and any(path.startswith(prefix) for path in selectable)
        _annotate_selection_node(child, identity, selectable, ignored, descendant_selected)
