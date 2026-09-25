"""Path identity: one way to resolve a path and compare two of them.

Comparisons normalize both sides; persisted path strings are never rewritten through these
helpers (``checkpoint_key`` is the documented exception that never touches the filesystem).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# The host's concrete class, fixed at import: ``Path()`` picks its flavour from ``os.name`` at
# every call, so anything that swaps ``os.name`` later would otherwise break every resolve.
_HostPath = type(Path())


def resolve_path(path: Any) -> Path:
    """Absolute, symlink-resolved form of ``path`` (``~`` expanded; missing parts allowed)."""
    candidate = _HostPath(str(path or "").strip()).expanduser()
    try:
        return candidate.resolve(strict=False)
    except OSError:
        return candidate.absolute()


def path_key(path: Any) -> str:
    """Comparison key for a filesystem path: resolved, and case-folded on Windows only."""
    if not str(path or "").strip():
        return ""
    resolved = str(resolve_path(path))
    return resolved.casefold() if os.name == "nt" else resolved


def is_at_or_below(path: Any, root: Any) -> bool:
    """True when ``path`` is ``root`` or lies inside it (both sides resolved)."""
    candidate = path_key(path)
    parent = path_key(root)
    if not candidate or not parent:
        return False
    if candidate == parent:
        return True
    prefix = parent if parent.endswith(os.sep) else parent + os.sep
    return candidate.startswith(prefix)


def checkpoint_key(path: Any) -> str:
    """Key for runtime checkpoint paths: normalized text with forward slashes.

    Deliberately non-resolving: checkpoints are compared against the strings they persisted,
    whether or not the path still exists or is reachable through the same link.
    """
    text = str(path or "").strip()
    return os.path.normpath(text).replace("\\", "/") if text else ""
