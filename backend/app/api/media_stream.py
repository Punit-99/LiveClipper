"""HTTP 206 Partial Content range-streaming helper for browser video playback.

Ensures Safari, Chrome, and iOS HTML5 <video> elements can seek and stream
smoothly via byte range requests.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import AsyncGenerator

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK_SIZE = 128 * 1024  # 128 KB chunks


async def _file_chunk_generator(
    file_path: Path,
    start: int,
    length: int,
    chunk_size: int = CHUNK_SIZE,
) -> AsyncGenerator[bytes, None]:
    """Asynchronously yield byte chunks from a specific file offset."""
    bytes_remaining = length
    with open(file_path, "rb") as fh:
        fh.seek(start)
        while bytes_remaining > 0:
            read_size = min(chunk_size, bytes_remaining)
            chunk = fh.read(read_size)
            if not chunk:
                break
            bytes_remaining -= len(chunk)
            yield chunk


def range_stream_response(
    file_path: str | Path,
    request: Request,
    media_type: str = "video/mp4",
) -> StreamingResponse:
    """Return an HTTP response supporting standard HTTP 206 partial content streaming.

    Args:
        file_path: Absolute path to video file on disk.
        request: FastAPI Request instance (to read Range header).
        media_type: MIME type of the file.

    Returns:
        StreamingResponse (206 Partial Content or 200 OK).
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video file not found: {path.name}",
        )

    file_size = path.stat().st_size
    range_header = request.headers.get("Range")

    if not range_header:
        # Full content response
        headers = {
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
            "Content-Type": media_type,
        }
        if request.method == "HEAD":
            from fastapi.responses import Response
            return Response(status_code=status.HTTP_200_OK, headers=headers, media_type=media_type)

        return StreamingResponse(
            _file_chunk_generator(path, 0, file_size),
            status_code=status.HTTP_200_OK,
            headers=headers,
            media_type=media_type,
        )

    match = RANGE_RE.match(range_header.strip())
    if not match:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=f"Invalid Range header format: {range_header}",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    start_str, end_str = match.groups()

    if start_str and end_str:
        start = int(start_str)
        end = int(end_str)
    elif start_str:
        start = int(start_str)
        end = file_size - 1
    elif end_str:
        # e.g. bytes=-500 (last 500 bytes)
        end = file_size - 1
        start = max(0, file_size - int(end_str))
    else:
        start = 0
        end = file_size - 1

    if start < 0 or start >= file_size or end < start:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail=f"Requested range out of bounds (start={start}, end={end}, size={file_size})",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    # Cap end at last byte
    end = min(end, file_size - 1)
    content_length = (end - start) + 1

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": media_type,
    }

    if request.method == "HEAD":
        from fastapi.responses import Response
        return Response(
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            headers=headers,
            media_type=media_type,
        )

    return StreamingResponse(
        _file_chunk_generator(path, start, content_length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers=headers,
        media_type=media_type,
    )
