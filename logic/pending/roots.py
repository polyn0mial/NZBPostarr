"""Configured upload roots and the scan items found under them."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List, Optional, Set, Tuple

from core.utils import VIDEO_EXTENSIONS
from logic.classify.content import detect_auto_category
from logic.classify.hints import infer_folder_category_hint
from logic.classify.tv_packs import _collect_tv_episode_paths


_AUTO_ROOT_CATEGORIES = {"", "external", "auto"}

@dataclass(frozen=True)
class PendingScanItem:
    """Canonical top-level scan result shared by queueing, monitoring, and dashboard flows."""

    category: str
    configured_category: str
    folder: Path
    path: Path
    name: str
    rel_key: str
    is_dir: bool
    episode_paths: Tuple[Path, ...] = ()
    episode_rel_keys: Tuple[str, ...] = ()

def _folder_path_entry_categories(
    folder_entries: Any,
    *,
    wanted: Optional[str],
    include_external: bool,
    must_exist: bool,
    seen: Set[tuple[str, str]],
) -> List[Tuple[str, Path]]:
    """Scan roots from the modern `folder_paths` entries."""
    categories: List[Tuple[str, Path]] = []

    for fp in folder_entries:
        if isinstance(fp, str):
            fp = {"path": fp}
        if not isinstance(fp, dict):
            continue

        raw_category = str(fp.get("category", "") or "").strip().lower()
        category = "external" if raw_category in _AUTO_ROOT_CATEGORIES else raw_category
        path_str = str(fp.get("path", "") or "").strip()
        if not category or not path_str:
            continue
        if not include_external and category == "external":
            continue
        if wanted and category != wanted:
            continue

        folder = Path(path_str)
        if must_exist and not folder.exists():
            continue
        key = (category, str(folder))
        if key in seen:
            continue
        seen.add(key)
        categories.append((category, folder))

    return categories

def get_configured_category_folders(
    conf: Any,
    *,
    filter_category: Optional[str] = None,
    include_external: bool = False,
    must_exist: bool = False,
) -> List[Tuple[str, Path]]:
    """Return configured folders as generic external scan roots."""
    seen: Set[tuple[str, str]] = set()
    wanted = str(filter_category or "").strip().lower() or None
    if wanted == "auto":
        wanted = "external"

    get_folder_path_entries = getattr(conf, "get_folder_path_entries", None)
    folder_entries = (
        get_folder_path_entries() if callable(get_folder_path_entries) else getattr(conf, "folder_paths", [])
    )

    return _folder_path_entry_categories(
        folder_entries,
        wanted=wanted,
        include_external=include_external,
        must_exist=must_exist,
        seen=seen,
    )

def find_configured_root(path: Path, folders: Iterable[Path]) -> Optional[Path]:
    """Return the deepest configured folder that contains the given path."""
    best_match: Optional[Path] = None
    best_len = -1
    for folder in folders:
        try:
            path.relative_to(folder)
        except ValueError:
            continue
        folder_len = len(str(folder))
        if folder_len > best_len:
            best_match = folder
            best_len = folder_len
    return best_match

def relative_key(path: Path, folder: Path) -> str:
    """Build a stable slash-normalized key relative to the category folder."""
    try:
        return str(path.relative_to(folder)).replace("\\", "/")
    except ValueError:
        return path.name

def iter_visible_entries(folder: Path, *, sort_entries: bool = False) -> List[Path]:
    """Return non-hidden direct children of a folder."""
    entries: List[Path] = []
    try:
        with os.scandir(str(folder)) as it:
            for entry in it:
                if entry.name.startswith("."):
                    continue
                entries.append(Path(entry.path))
    except OSError:
        return []

    if sort_entries:
        entries.sort(key=lambda p: p.name.lower())
    return entries

def get_configured_folders(conf: Any, *, must_exist: bool = False) -> List[Path]:
    """Return all configured folder paths regardless of their legacy category."""
    folders: List[Path] = []
    seen: Set[str] = set()
    for _category, folder in get_configured_category_folders(conf, include_external=True, must_exist=must_exist):
        key = str(folder)
        if key in seen:
            continue
        seen.add(key)
        folders.append(folder)

    return folders

def _resolve_configured_scan_category(configured_category: str, entry: Path, folder_category_hint: str = "") -> str:
    """Return the effective queue category for a configured folder entry."""
    normalized = str(configured_category or "").strip().lower()
    if normalized in _AUTO_ROOT_CATEGORIES:
        return detect_auto_category(entry, "external")
    return normalized

def build_pending_scan_item(
    folder: Path,
    configured_category: str,
    entry: Path,
    *,
    video_extensions: Optional[Set[str]] = None,
) -> PendingScanItem:
    """Build a canonical top-level scan item for a configured folder entry."""
    folder_category_hint = infer_folder_category_hint(folder)
    effective_category = _resolve_configured_scan_category(configured_category, entry, folder_category_hint)
    video_exts = video_extensions or VIDEO_EXTENSIONS
    episode_paths: Tuple[Path, ...] = ()
    episode_rel_keys: Tuple[str, ...] = ()

    if effective_category == "tv":
        episode_paths = _collect_tv_episode_paths(entry, video_exts)
        episode_rel_keys = tuple(relative_key(video, folder) for video in episode_paths)

    return PendingScanItem(
        category=effective_category,
        configured_category=str(configured_category or "").strip().lower() or "external",
        folder=folder,
        path=entry,
        name=entry.name,
        rel_key=relative_key(entry, folder),
        is_dir=entry.is_dir(),
        episode_paths=episode_paths,
        episode_rel_keys=episode_rel_keys,
    )

def scan_folder_items(
    folder: Path,
    configured_category: str,
    *,
    sort_entries: bool = True,
    video_extensions: Optional[Set[str]] = None,
) -> List[PendingScanItem]:
    """Scan one configured folder and return normalized top-level items."""
    return [
        build_pending_scan_item(folder, configured_category, entry, video_extensions=video_extensions)
        for entry in iter_visible_entries(folder, sort_entries=sort_entries)
    ]

def scan_configured_items(
    conf: Any,
    *,
    filter_category: Optional[str] = None,
    include_external: bool = True,
    must_exist: bool = False,
    sort_entries: bool = True,
    video_extensions: Optional[Set[str]] = None,
) -> List[PendingScanItem]:
    """Return canonical top-level scan items across all configured folders."""
    wanted = str(filter_category or "").strip().lower() or None
    items: List[PendingScanItem] = []

    for configured_category, folder in get_configured_category_folders(
        conf,
        filter_category=None,
        include_external=include_external,
        must_exist=must_exist,
    ):
        for item in scan_folder_items(
            folder,
            configured_category,
            sort_entries=sort_entries,
            video_extensions=video_extensions,
        ):
            if wanted and item.category != wanted:
                continue
            items.append(item)

    return items

def collect_configured_scan_items(conf: Any, *, must_exist: bool = False) -> List[Tuple[str, Path, Path]]:
    """Collect direct child entries from configured folders using assigned categories."""
    return [(item.category, item.folder, item.path) for item in scan_configured_items(conf, must_exist=must_exist)]
