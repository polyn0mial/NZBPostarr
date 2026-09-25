"""Indexer API: /api/indexers."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from core.config import get_config


router = APIRouter(prefix="/api/indexers", tags=["indexers"])

@router.get("")
@router.get("/")
async def get_all_indexers_route() -> List[Dict[str, Any]]:
    """Retrieve all loaded indexer definitions."""
    from core.registry import get_all_indexers

    conf = get_config()
    return [idx.to_ui_dict(conf) for idx in get_all_indexers()]

@router.get("/{indexer_id}")
async def get_indexer_route(indexer_id: str) -> Dict[str, Any]:
    """Get a specific indexer definition."""
    from core.registry import get_indexer

    idx = get_indexer(indexer_id)
    if not idx:
        raise HTTPException(status_code=404, detail=f"Indexer '{indexer_id}' not found")

    conf = get_config()
    data = idx.to_ui_dict(conf)

    # Add extra fields only needed for detail view
    data.update(
        {
            "categories": idx.categories.model_dump(),
            "submit_url": idx.submit_url,
            "method": idx.method,
        }
    )
    return data

@router.post("/reload")
async def reload_indexers_route() -> Dict[str, Any]:
    """Reload all indexer definitions from YAML files."""
    from core.registry import get_all_indexers, reload_indexers
    from logic.pending.completion import invalidate_pending_indexer_context

    reload_indexers()
    invalidate_pending_indexer_context()
    return {"status": "success", "count": len(get_all_indexers())}
