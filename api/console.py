"""Console API: /api/console."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from core.logging import console


router = APIRouter(prefix="/api/console", tags=["console"])

@router.get("/logs")
async def get_logs(
    after: int = 0,
    after_seq: Optional[int] = None,
    count: Optional[int] = None,
    tail: Optional[int] = None,
) -> Dict[str, Any]:
    """Fetch log messages from the console buffer.

    Pass ``tail=N`` on the first request to jump to the most recent N
    entries instead of replaying the entire buffer from the start.
    """
    if tail is not None:
        logs, last_seq = console.get_tail(tail)
        return {"logs": logs, "last_seq": last_seq, "seq": last_seq, "count": len(logs)}

    seq = after_seq if after_seq is not None else after
    logs, last_seq = console.get_logs(after_seq=seq, limit=count)
    return {
        "logs": logs,
        "last_seq": last_seq,
        "seq": last_seq,
        "count": len(logs),
    }

@router.get("/log-file")
async def download_log_file() -> Response:
    """Serve the raw log file for viewing / download."""
    log_path = Path(__file__).resolve().parent.parent / "data" / "logs" / "nzbpostarr.log"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")
    return FileResponse(
        str(log_path),
        media_type="text/plain",
        filename="nzbpostarr-log.txt",
        headers={"Content-Disposition": "inline; filename=nzbpostarr-log.txt"},
    )
