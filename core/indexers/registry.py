"""Indexer registry: loads indexers/*.yaml and resolves per-indexer config.

Singleton access via get_registry(); per-indexer credentials never leave this module unmasked.
"""

import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from loguru import logger

from core.logging import log_verbose
from core.indexers.models import (
    IndexerDefinition,
    _requires_username,
    resolve_indexer_api_key,
    resolve_indexer_enabled,
    resolve_indexer_username,
)

# Singleton instance and lock
_REGISTRY = None
_REGISTRY_LOCK = threading.RLock()


class IndexerRegistry:
    """
    Central registry for all loaded indexer definitions.
    Indexers are loaded from YAML files in the indexers/ directory.
    """

    _instance: Optional["IndexerRegistry"] = None

    def __new__(cls) -> "IndexerRegistry":
        if cls._instance is None:
            with _REGISTRY_LOCK:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        with _REGISTRY_LOCK:
            if getattr(self, "_initialized", False):
                return
            self._indexers: Dict[str, IndexerDefinition] = {}
            self._indexer_files: Dict[str, Path] = {}
            self._load_indexers()
            self._initialized = True

    @property
    def indexers_dir(self) -> Path:
        return Path(__file__).parents[2] / "indexers"

    def _load_indexers(self) -> None:
        """Load all YAML indexer definitions from the indexers directory."""
        self._indexers.clear()
        self._indexer_files.clear()

        for yaml_file in sorted(
            self.indexers_dir.glob("*.yaml"), key=lambda path: path.name.lower()
        ):
            stem_lower = yaml_file.stem.lower()
            if (
                yaml_file.name.startswith("_")
                or stem_lower.endswith(".example")
                or stem_lower.endswith(".template")
            ):
                continue  # Skip files starting with underscore, or *.example.yaml/*.template.yaml

            try:
                with open(yaml_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)

                if not data:
                    continue

                # Support multiple indexers in one file (or single)
                indexers_data = data if isinstance(data, list) else [data]

                for idx_data in indexers_data:
                    try:
                        indexer = IndexerDefinition(**idx_data)
                        self._indexers[indexer.id] = indexer
                        self._indexer_files[indexer.id] = yaml_file

                        # Only log individual indexers on first load (VERBOSE) or error
                        # Suppress during reloads unless VERBOSE is on to avoid clutter
                        if not getattr(self, "_initialized", False):
                            log_verbose(
                                f"Loaded indexer: {indexer.log_name} ({indexer.id})"
                            )
                        else:
                            log_verbose(
                                f"Reloaded indexer: {indexer.log_name} ({indexer.id})"
                            )
                    except Exception as e:
                        logger.warning(
                            f"Failed to parse indexer in {yaml_file.name}: {e}"
                        )

            except Exception as e:
                logger.warning(f"Failed to load indexer file {yaml_file.name}: {e}")

        # Summary Logging
        if not getattr(self, "_initialized", False):
            # First boot (Summary only, individual items were logged as VERBOSE above)
            log_verbose(
                f"Indexer Registry Initialized: {len(self._indexers)} indexer(s) ready"
            )
        else:
            # Subsequent reloads (Single line to avoid console clutter)
            log_verbose(
                f"Indexer Registry Reloaded: {len(self._indexers)} indexer(s) updated"
            )

    def reload(self) -> None:
        """Reload all indexer definitions."""
        start = time.time()
        self._load_indexers()
        elapsed = time.time() - start
        if elapsed > 0.1:
            logger.debug(f"Indexer Registry loaded in {elapsed:.3f}s")

    def get(self, indexer_id: str) -> Optional[IndexerDefinition]:
        """Get an indexer by ID."""
        return self._indexers.get(indexer_id)

    def all(self) -> List[IndexerDefinition]:
        """Get all loaded indexers in configured file order."""
        return list(self._indexers.values())

    def ids(self) -> List[str]:
        """Get all indexer IDs."""
        return list(self._indexers.keys())

    def enabled(self, config: Any) -> List[IndexerDefinition]:
        """Get indexers that are enabled and configured (YAML + config/env secrets)."""
        enabled_list = []
        for indexer in self.all():
            # Resolve enabled state from config (fallback to YAML default)
            is_enabled = resolve_indexer_enabled(indexer, config)

            if is_enabled:
                if indexer.auth.method == "none":
                    enabled_list.append(indexer)
                    continue

                # Check if API key is configured (YAML or config/env)
                if not resolve_indexer_api_key(indexer, config):
                    continue

                # Some indexers require a username as well (e.g. CURL templates)
                if _requires_username(indexer) and not resolve_indexer_username(
                    indexer, config
                ):
                    continue

                enabled_list.append(indexer)
        return enabled_list


def get_registry() -> IndexerRegistry:
    """Get the global indexer registry instance (singleton)."""
    global _REGISTRY
    if _REGISTRY is None:
        with _REGISTRY_LOCK:
            if _REGISTRY is None:
                _REGISTRY = IndexerRegistry()
    return _REGISTRY


def reload_indexers() -> None:
    """Reload all indexer definitions."""
    get_registry().reload()


def get_indexer(indexer_id: str) -> Optional[IndexerDefinition]:
    """Get an indexer by ID."""
    return get_registry().get(indexer_id)


def get_all_indexers() -> List[IndexerDefinition]:
    """Get all loaded indexers."""
    return get_registry().all()


def get_enabled_indexers(config: Any) -> List[IndexerDefinition]:
    """Get indexers that are enabled and configured."""
    return get_registry().enabled(config)
