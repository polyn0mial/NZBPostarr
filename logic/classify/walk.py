"""Filesystem walks for classification, memoised per scan."""

from __future__ import annotations

import contextvars
import os
from pathlib import Path
from typing import Iterable, Optional, Set, Tuple


_SCAN_CACHE_VAR: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_pending_scan_walk_cache",
    default=None,
)

def begin_scan_cache() -> contextvars.Token:
    """Start a fresh per-scan walk cache. Caller must release the token."""
    return _SCAN_CACHE_VAR.set({})

def end_scan_cache(token: contextvars.Token) -> None:
    """Tear down the per-scan walk cache."""
    try:
        _SCAN_CACHE_VAR.reset(token)
    except (LookupError, ValueError):
        pass

def _scan_cache_get(kind: str, entry: Path) -> Optional[Tuple[Path, ...]]:
    cache = _SCAN_CACHE_VAR.get()
    if cache is None:
        return None
    return cache.get((kind, str(entry)))

def _scan_cache_put(kind: str, entry: Path, value: Tuple[Path, ...]) -> Tuple[Path, ...]:
    cache = _SCAN_CACHE_VAR.get()
    if cache is not None:
        cache[(kind, str(entry))] = value
    return value

def _iter_video_candidates(entry: Path, video_extensions: Set[str]) -> Tuple[Path, ...]:
    if entry.is_file():
        return (entry,) if entry.suffix.lower() in video_extensions else ()

    cached = _scan_cache_get("video", entry)
    if cached is not None:
        return cached

    videos: list[Path] = []
    for root, dirs, files in os.walk(str(entry)):
        dirs[:] = sorted(dirname for dirname in dirs if not dirname.startswith("."))
        for filename in sorted(files):
            if filename.startswith("."):
                continue
            candidate = Path(root) / filename
            if candidate.suffix.lower() in video_extensions:
                videos.append(candidate)
    return _scan_cache_put("video", entry, tuple(videos))

def _iter_leaf_files(entry: Path) -> Tuple[Path, ...]:
    if entry.is_file():
        return (entry,)

    cached = _scan_cache_get("leaf", entry)
    if cached is not None:
        return cached

    files: list[Path] = []
    for root, dirs, filenames in os.walk(str(entry)):
        dirs[:] = sorted(dirname for dirname in dirs if not dirname.startswith("."))
        for filename in sorted(filenames):
            if filename.startswith("."):
                continue
            files.append(Path(root) / filename)
    return _scan_cache_put("leaf", entry, tuple(files))

def _iter_video_files(folder: Path, video_extensions: Set[str]) -> Iterable[Path]:
    for root, _dirs, files in os.walk(str(folder)):
        for fname in files:
            if fname.startswith("."):
                continue
            if os.path.splitext(fname)[1].lower() in video_extensions:
                yield Path(root) / fname
