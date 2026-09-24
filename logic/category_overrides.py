"""Persisted manual category overrides for pending queue items.

The queue page lets a user override an item's auto-detected category
(e.g. Movies -> TV) via a per-item dropdown. That choice previously lived
only in the browser's localStorage, so it never survived a different
browser, device, or cleared site data. This module persists the same
{item_key: category} map to a small JSON file on the server instead, so
it's available from any browser that opens the queue page.

The file lives under the app's data directory (``data/category_overrides.json``);
an older ``category_overrides.json`` in the app root is read as a fallback.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

_lock = threading.Lock()
_store: Dict[str, str] = {}
_loaded = False
_path: Optional[Path] = None


def _get_path() -> Path:
    global _path
    if _path is None:
        from core.config import APP_ROOT

        _path = APP_ROOT / "data" / "category_overrides.json"
    return _path


def _legacy_path() -> Path:
    from core.config import APP_ROOT

    return APP_ROOT / "category_overrides.json"


def _load() -> None:
    global _store, _loaded
    if _loaded:
        return
    path = _get_path()
    load_path = path if path.exists() else _legacy_path()
    if load_path.exists():
        try:
            with open(load_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                _store = {k: str(v) for k, v in raw.items() if isinstance(k, str) and v}
                logger.debug(f"Category overrides loaded: {len(_store)} entries from {load_path}")
        except Exception as e:
            logger.warning(f"Failed to load category overrides: {e}")
            _store = {}
    _loaded = True


def _save() -> None:
    path = _get_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".category_overrides.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(_store, f, indent=2)
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    except Exception as e:
        logger.warning(f"Failed to save category overrides: {e}")


def get_all() -> Dict[str, str]:
    """Return the full {item_key: category} override map."""
    with _lock:
        _load()
        return dict(_store)


def set_override(key: str, category: Optional[str]) -> None:
    """Persist a category override, or clear it when category is falsy."""
    with _lock:
        _load()
        if category:
            _store[key] = category
        else:
            _store.pop(key, None)
        _save()
