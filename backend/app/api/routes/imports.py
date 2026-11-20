"""API routes for YouTube Video Clipper (Import feature)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.media_stream import range_stream_response
from app.api.state import get_shorts_pipeline
from app.clipper.downloader import extract_youtube_id, fetch_video_info, is_valid_youtube_url
from app.clipper.service import get_clipper_service
from app.config import get_settings
from app.shorts.queue_manager import get_processing_queue

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/import", tags=["Import"])


class AnalyzeRequest(BaseModel):
    url: str
    num_clips: int = Field(default=5, ge=1, le=10)
    clip_duration: int = Field(default=55, ge=15, le=90)
    skip_intro: int = Field(default=0, ge=0)
    skip_outro: int = Field(default=0, ge=0)
    quality: int = Field(default=1080)


@router.get("/info")
@router.post("/info")
def get_video_info(
    url: str | None = Query(None, description="YouTube URL to preview"),
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch video metadata and thumbnail without downloading media."""
    target_url = url or (payload.get("url") if payload else None)
    if not target_url or not is_valid_youtube_url(target_url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid YouTube video URL.",
        )
    try:
        meta = fetch_video_info(target_url)
        return meta.to_dict()
    except Exception as exc:
        log.warning("Could not fetch YouTube info for %s: %s", target_url, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to retrieve video information: {exc}",
        )


@router.post("/analyze", status_code=status.HTTP_202_ACCEPTED)
def start_import_analysis(payload: AnalyzeRequest) -> dict[str, Any]:
    """Start asynchronous download, moment detection, and clip extraction."""
    if not is_valid_youtube_url(payload.url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid YouTube video URL.",
        )

    service = get_clipper_service()
    options = {
        "max_clips": payload.num_clips,
        "clip_duration": payload.clip_duration,
        "skip_intro": payload.skip_intro,
        "skip_outro": payload.skip_outro,
        "quality": payload.quality,
    }

    try:
        job = service.submit_job(url=payload.url, options=options)
        return job.to_dict()
    except Exception as exc:
        log.exception("Failed to submit import job: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not start import analysis: {exc}",
        )


@router.get("/jobs")
def list_import_jobs() -> list[dict[str, Any]]:
    """List all import jobs and their current status."""
    service = get_clipper_service()
    return [j.to_dict() for j in service.list_jobs()]


@router.get("/jobs/{job_id}")
def get_import_job(job_id: str) -> dict[str, Any]:
    """Get status, progress, and extracted clips for an import job."""
    service = get_clipper_service()
    job = service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Import job '{job_id}' not found.",
        )
    return job.to_dict()


@router.get("/clips")
def list_imported_clips() -> list[dict[str, Any]]:
    """List all extracted clips from imported YouTube videos."""
    service = get_clipper_service()
    return [c.to_dict() for c in service.list_clips()]


@router.get("/clips/{clip_id}/preview")
async def preview_imported_clip(clip_id: str, request: Request):
    """Stream imported clip video with HTTP Range support."""
    service = get_clipper_service()
    clip = service.get_clip(clip_id)
    if not clip or not Path(clip.file_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Imported clip video '{clip_id}' not found.",
        )

    return await range_stream_response(Path(clip.file_path), request)


@router.get("/clips/{clip_id}/thumbnail")
def get_imported_clip_thumbnail(clip_id: str):
    """Retrieve JPEG snapshot thumbnail for an imported clip."""
    service = get_clipper_service()
    clip = service.get_clip(clip_id)
    if not clip or not clip.thumbnail_path or not Path(clip.thumbnail_path).is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thumbnail for clip '{clip_id}' not found.",
        )

    return FileResponse(
        path=clip.thumbnail_path,
        media_type="image/jpeg",
        filename=f"{clip_id}.jpg",
    )


@router.delete("/clips/{clip_id}")
def delete_imported_clip(clip_id: str) -> dict[str, Any]:
    """Delete an imported clip and its media files."""
    service = get_clipper_service()
    success = service.delete_clip(clip_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Imported clip '{clip_id}' not found.",
        )
    return {"status": "deleted", "clip_id": clip_id}


@router.post("/clips/{clip_id}/process")
def process_imported_clip(clip_id: str) -> dict[str, Any]:
    """Enqueue an imported clip for processing into a 9:16 vertical Short."""
    service = get_clipper_service()
    clip = service.get_clip(clip_id)
    if not clip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Imported clip '{clip_id}' not found.",
        )

    settings = get_settings()
    pipeline = get_shorts_pipeline(settings=settings)
    pipeline.scan_clips()  # Refresh manifest with imported clip

    queue = get_processing_queue(settings.max_concurrent_processing)

    def _worker():
        pipeline.process_pending_shorts(
            clip_id_or_file=clip_id,
            force=True,
            progress_callback=lambda cid, stg, pct: queue.report_progress(cid, stg, pct),
        )

    item = queue.enqueue(
        clip_id=clip_id,
        title=clip.suggested_title or f"Imported Clip {clip_id}",
        worker_fn=_worker,
    )

    return {
        "status": item.status,
        "clip_id": item.clip_id,
        "position": item.position,
        "message": "Clip queued for Shorts processing.",
    }
