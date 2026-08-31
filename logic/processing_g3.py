# Auto-split from processing.py - verbatim symbol bodies, synthesized imports.

from logic.processing_base import (
    Any, Optional, Path, log_info, update_job_progress,
)
from logic.processing_g1 import (QueueItemValidation)  # noqa: F401
from logic.processing_g2 import (_JobRunState, _complete_runtime_item_checkpoint, is_season_pack)  # noqa: F401

def _processing_db_type(path: Path, category: str) -> str:
    """Map queue routing categories to the DB-facing item label."""
    if category == "tv":
        return "TV Show" if is_season_pack(path) else "TV Episode"
    if category == "movies":
        return "Movies"
    if category == "anime":
        return "Anime"
    if category == "disc":
        return "DISC"
    if category == "music":
        return "Music"
    if category == "books":
        return "Books"
    if category == "apps":
        return "Apps"
    return "Misc"

def _handle_nonready_validation(
    validation: QueueItemValidation,
    checkpoint_path: Path,
    current_job: Optional[dict[str, Any]],
    run_state: _JobRunState,
) -> Optional[bool]:
    """Handle non-ready validation outcomes; None means the item is ready to upload."""
    if validation.outcome == "stopped":
        message = validation.message or "Job stopped during validation."
        requested_stop = bool(current_job and current_job.get("stop_requested"))
        log_info(message, "WARNING" if requested_stop else "ERROR")
        update_job_progress(
            msg=message,
            status="stopped" if requested_stop else "failed",
        )
        return False

    if validation.outcome == "skipped":
        log_info(validation.message)
        run_state.duplicate_count += 1
    elif validation.outcome == "failed":
        log_info(validation.message or f"Validation failed for {validation.path.name}", "ERROR")
        run_state.failure_count += 1
    else:
        return None

    run_state.completed_count += 1
    _complete_runtime_item_checkpoint(current_job, checkpoint_path)
    run_state.publish_counts()
    return True

