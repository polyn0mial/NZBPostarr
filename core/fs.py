"""Filesystem helpers: atomic text writes, live sizes, skip patterns and watchdog observers."""

from __future__ import annotations

import fnmatch
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from watchdog.observers import Observer


def atomic_write_text(path: Path, content: str) -> None:
    """Durably write a complete UTF-8 text file before replacing its destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass



def compute_size_uncached(p: Path) -> int:
    """Compute size recursively without caching for live filesystem views."""
    try:
        if p.is_file():
            return p.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(str(p)):
            for fname in files:
                try:
                    total += os.path.getsize(os.path.join(root, fname))
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def should_skip_file(filename: str, category: str, skip_config: Optional[Dict[str, Any]] = None) -> bool:
    """Check if a filename matches any skip pattern for the given category.

    Supports both glob (fnmatch) and regex patterns via the ``is_regex`` flag
    on each pattern entry.
    """
    if not skip_config or not skip_config.get("enabled"):
        return False

    for p in skip_config.get("patterns", []):
        pattern = p.get("pattern", "")
        cats = p.get("categories", [])
        if not pattern:
            continue
        # Empty categories list means the pattern applies to every category
        if cats and category not in cats:
            continue
        if p.get("is_regex"):
            try:
                if re.search(pattern, filename, re.IGNORECASE):
                    return True
            except re.error:
                continue  # invalid regex - skip silently
        else:
            if fnmatch.fnmatch(filename.lower(), pattern.lower()):
                return True
    return False


def start_watchdog_observer(
    watches: List[Tuple[Any, Path, bool]],
    *,
    observer_factory: Optional[Callable[[], Any]] = None,
) -> Tuple[Optional[Any], int]:
    """Start a watchdog observer for the provided handler/path specs."""
    factory = observer_factory or Observer
    observer = factory()
    scheduled = 0

    for handler, folder, recursive in watches:
        path = Path(folder)
        if not path.exists() or not path.is_dir():
            continue
        observer.schedule(handler, str(path), recursive=recursive)
        scheduled += 1

    if scheduled == 0:
        return None, 0

    observer.start()
    return observer, scheduled


def stop_watchdog_observer(observer: Any, *, join_timeout_s: float = 5.0) -> None:
    """Stop and join a watchdog observer when present."""
    if observer is None:
        return

    observer.stop()
    observer.join(timeout=join_timeout_s)

