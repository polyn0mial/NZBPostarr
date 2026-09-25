"""Streamed NZB reposts and stream monitors under /api/uploads."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from logic import usenet_stream
from logic.jobs.engine import JobEngine
from logic.runtime import ensure_engine_started


router = APIRouter(prefix="/api/uploads", tags=["uploads"])

class StreamStartResponse(BaseModel):
    """API response model for starting a streamed NZB repost job."""

    job_id: Optional[str] = None
    job_ids: List[str] = Field(default_factory=list)
    status: str
    mode: str = "job"
    message: Optional[str] = None
    monitor: Optional[Dict[str, Any]] = None

@router.get("/stream-monitors")
async def get_stream_monitors() -> Dict[str, Any]:
    """List configured stream folder monitors."""
    return {"monitors": usenet_stream.list_stream_monitors()}

@router.delete("/stream-monitors/{monitor_id}")
async def delete_stream_monitor(monitor_id: str) -> Dict[str, Any]:
    """Remove a configured stream folder monitor."""
    if not usenet_stream.remove_stream_monitor(monitor_id):
        raise HTTPException(status_code=404, detail="Stream monitor not found")
    await usenet_stream.restart_stream_monitors()
    return {"status": "deleted", "monitor_id": monitor_id}

@router.post("/stream-nzb", response_model=StreamStartResponse)
async def start_streamed_nzb_upload(
    file: Optional[UploadFile] = File(None),
    source_path: Optional[str] = Form(None),
    monitor_folder: bool = Form(False),
    category: Optional[str] = Form(None),
    release_name: Optional[str] = Form(None),
    submit_mode: str = Form("post_and_submit"),
    posting_server_name: Optional[str] = Form(None),
    test_mode: bool = Form(False),
    enable_duplicate_check: bool = Form(True),
    indexer_id: Optional[str] = Form(None),
    service: JobEngine = Depends(ensure_engine_started),
) -> StreamStartResponse:
    """Upload an NZB manifest and queue a direct Usenet-to-Usenet stream job."""
    try:
        request_options = usenet_stream.normalize_stream_request(
            upload_filename=file.filename if file else None,
            source_path=source_path,
            monitor_folder=monitor_folder,
            category=category,
            submit_mode=submit_mode,
        )
    except usenet_stream.StreamError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    source_path = request_options.source_path
    category = request_options.category
    normalized_submit_mode = request_options.submit_mode

    tmp_path: Optional[Path] = None
    try:
        if monitor_folder:
            monitor = usenet_stream.add_stream_monitor(
                folder_path=source_path,
                category=category,
                posting_server_name=posting_server_name,
                submit_mode=normalized_submit_mode,
                indexer_id=indexer_id,
                enable_duplicate_check=enable_duplicate_check,
                test_mode=test_mode,
            )
            await usenet_stream.restart_stream_monitors()
            return StreamStartResponse(
                status="monitoring",
                mode="monitor",
                message=f"Watching {monitor['folder_path']} for new NZB files",
                monitor=monitor,
            )

        if file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".nzb") as tmp:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)

            if not tmp_path.exists() or tmp_path.stat().st_size == 0:
                raise HTTPException(status_code=400, detail="Uploaded NZB was empty")

            resolved_category = usenet_stream.resolve_stream_category(tmp_path, category)
            job_id = service.start_usenet_stream_job(
                category=resolved_category,
                stream_source_path=str(tmp_path),
                stream_source_name=file.filename,
                release_name=release_name,
                test_mode=test_mode,
                enable_duplicate_check=enable_duplicate_check,
                indexer_id=indexer_id,
                posting_server_name=posting_server_name,
                submit_mode=normalized_submit_mode,
                cleanup_paths=[str(tmp_path)],
            )
            return StreamStartResponse(
                job_id=job_id,
                job_ids=[job_id],
                status="started",
                mode="job",
                message=f"Queued stream job for {file.filename}",
            )

        resolved_paths = usenet_stream.resolve_source_nzb_paths(source_path)
        if len(resolved_paths) > 1 and release_name:
            logger.info("Ignoring custom release name for multi-file server-path stream request")

        job_ids: list[str] = []
        for path in resolved_paths:
            resolved_category = usenet_stream.resolve_stream_category(path, category)
            job_ids.append(
                service.start_usenet_stream_job(
                    category=resolved_category,
                    stream_source_path=str(path),
                    stream_source_name=path.name,
                    release_name=release_name if len(resolved_paths) == 1 else None,
                    test_mode=test_mode,
                    enable_duplicate_check=enable_duplicate_check,
                    indexer_id=indexer_id,
                    posting_server_name=posting_server_name,
                    submit_mode=normalized_submit_mode,
                )
            )

        return StreamStartResponse(
            job_id=job_ids[0] if len(job_ids) == 1 else None,
            job_ids=job_ids,
            status="started",
            mode="batch" if len(job_ids) > 1 else "job",
            message=f"Queued {len(job_ids)} stream job(s)",
        )
    except usenet_stream.StreamError as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.exception(f"Failed to queue streamed NZB upload: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to queue streamed NZB upload: {exc}") from exc
    finally:
        if file is not None:
            await file.close()
