"""Revalidation of queued/paused processing jobs against the current classification rules."""

from __future__ import annotations

from typing import Any, Iterable

from logic.jobs.models import job_target_paths, normalize_job_path_identity

_HINT_KEYS_DROPPED = frozenset({"auto_select_ignored", "skipped", "completed", "indexers", "indexer_errors"})


def revalidation_targets(jobs: Iterable[dict[str, Any]], include_paused: bool) -> list[dict[str, Any]]:
    """Snapshots of queued/paused processing jobs; a paused job restored after a restart is skipped."""
    snapshots: list[dict[str, Any]] = []
    for job in jobs:
        status = str(job.get("status") or "")
        if status not in ("queued", "paused"):
            continue
        if not include_paused and status == "paused":
            continue
        # A paused job restored after a restart is left alone until the user resumes it.
        if status == "paused" and str(job.get("progress") or "").startswith("Recovered after restart"):
            continue
        if str(job.get("job_type") or "processing") != "processing":
            continue
        snapshots.append(
            {
                "job_id": str(job.get("job_id") or ""),
                "status": status,
                "category": str(job.get("category") or "misc"),
                "display_name": job.get("display_name"),
                "paths": job_target_paths(job),
                "kwargs": dict(job.get("_kwargs") or {}),
            }
        )
    return snapshots


def _revalidation_hint_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    hint_map: dict[str, dict[str, Any]] = {}
    for hint in snapshot.get("kwargs", {}).get("item_hints") or []:
        if not isinstance(hint, dict):
            continue
        identity = normalize_job_path_identity(hint.get("path"))
        if not identity:
            continue
        hint_map[identity] = {k: v for k, v in dict(hint).items() if k not in _HINT_KEYS_DROPPED}
    return hint_map


def revalidation_candidates(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Staging-shaped rows for one job snapshot, carrying its stored item hints."""
    current_paths = [p for p in snapshot.get("paths", []) if str(p or "").strip()]
    hint_map = _revalidation_hint_map(snapshot)
    candidates: list[dict[str, Any]] = []
    for path_text in current_paths:
        identity = normalize_job_path_identity(path_text)
        hint = dict(hint_map.get(identity or "", {}))
        hint["path"] = path_text
        if "category" not in hint and snapshot.get("category"):
            hint["category"] = snapshot["category"]
        candidates.append(hint)
    return candidates


def revalidated_plan(prepared: Any) -> tuple[list[str], list[dict[str, Any]], str]:
    """The kept paths, their hints and the job category ("mixed" when several) from a prepared start."""
    new_paths = [
        str(item.get("path") or "").strip()
        for item in prepared.runnable_items
        if str(item.get("path") or "").strip()
    ]
    new_hints = [dict(item) for item in prepared.runnable_items if isinstance(item, dict)]
    new_categories = sorted(
        {
            str(item.get("category") or "").strip().lower()
            for item in new_hints
            if str(item.get("category") or "").strip()
        }
    )
    new_category = new_categories[0] if len(new_categories) == 1 else ("mixed" if new_categories else "")
    return new_paths, new_hints, new_category
