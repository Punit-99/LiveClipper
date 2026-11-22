"""HTTP 206 Partial Content range-streaming helper for browser video playback.

Ensures Safari, Chrome, and iOS HTML5 <video> elements can seek and stream
smoothly via byte range requests.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse


def range_stream_response(
    file_path: str | Path,
    request: Request,
    media_type: str = "video/mp4",
) -> FileResponse:
    """Return an HTTP response supporting standard HTTP 206 partial content streaming.

    Uses Starlette's high-performance, async-threaded FileResponse which natively
    handles Range headers, single/multi-ranges, If-Range, ETags, and HEAD requests.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video file not found: {path.name}",
        )

    return FileResponse(
        path=path,
        media_type=media_type,
        content_disposition_type="inline",
    )
