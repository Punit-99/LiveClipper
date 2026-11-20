"""API routes for YouTube Video Clipper (Import feature)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
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
    start_time: str | None = None
    end_time: str | None = None


class LibraryAnalyzeRequest(BaseModel):
    num_clips: int = Field(default=5, ge=1, le=10)
    clip_duration: int = Field(default=55, ge=15, le=90)
    skip_intro: int = Field(default=0, ge=0)
    skip_outro: int = Field(default=0, ge=0)
    start_time: str | None = None
    end_time: str | None = None


class DownloadRequest(BaseModel):
    url: str
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
    """Start asynchronous download, moment detection, and clip extraction (one-shot)."""
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
        "start_time": payload.start_time,
        "end_time": payload.end_time,
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


# ==========================================
# Two-Step Workflow & Library Endpoints
# ==========================================

@router.post("/download", status_code=status.HTTP_200_OK)
def download_to_library(payload: DownloadRequest) -> dict[str, Any]:
    """Download a YouTube video directly into the library without analyzing."""
    if not is_valid_youtube_url(payload.url):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid YouTube video URL.",
        )
    service = get_clipper_service()
    try:
        item = service.download_video_to_library(url=payload.url, quality=payload.quality)
        return item.to_dict()
    except Exception as exc:
        log.exception("Failed downloading video to library: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not download video to library: {exc}",
        )


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_file_to_library(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a local video file (.mp4, .mkv, .mov, .webm, .avi up to 5GB) to the library."""
    filename = file.filename or "uploaded_video.mp4"
    valid_exts = (".mp4", ".mkv", ".mov", ".webm", ".avi")
    if not filename.lower().endswith(valid_exts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported video format. Allowed formats: {', '.join(valid_exts)}",
        )

    service = get_clipper_service()
    try:
        item = service.upload_video_file_stream(stream=file.file, filename=filename)
        return item.to_dict()
    except Exception as exc:
        log.exception("Failed to stream upload video file: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload video file: {exc}",
        )


@router.get("/library")
def list_library_videos() -> list[dict[str, Any]]:
    """List all downloaded and uploaded source videos currently in the library."""
    service = get_clipper_service()
    return [item.to_dict() for item in service.list_library()]


@router.get("/library/{video_id}/thumbnail")
def get_library_thumbnail(video_id: str):
    """Serve thumbnail JPEG for a video in the library."""
    service = get_clipper_service()
    thumb_path = service.imports_dir / video_id / "thumbnail.jpg"
    if not thumb_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Thumbnail for video '{video_id}' not found.",
        )
    return FileResponse(path=thumb_path, media_type="image/jpeg", filename=f"{video_id}.jpg")


@router.delete("/library/{video_id}")
def delete_library_video(video_id: str) -> dict[str, Any]:
    """Delete a source video and its extracted clips from the library."""
    service = get_clipper_service()
    success = service.delete_library_item(video_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library video '{video_id}' not found.",
        )
    return {"status": "deleted", "video_id": video_id}


@router.post("/analyze/{video_id}", status_code=status.HTTP_202_ACCEPTED)
def start_library_video_analysis(video_id: str, payload: LibraryAnalyzeRequest) -> dict[str, Any]:
    """Analyze a previously downloaded/uploaded video from the library with custom parameters."""
    service = get_clipper_service()
    options = {
        "max_clips": payload.num_clips,
        "clip_duration": payload.clip_duration,
        "skip_intro": payload.skip_intro,
        "skip_outro": payload.skip_outro,
        "start_time": payload.start_time,
        "end_time": payload.end_time,
    }

    try:
        job = service.analyze_library_video(video_id=video_id, options=options)
        return job.to_dict()
    except FileNotFoundError as fnf:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(fnf))
    except Exception as exc:
        log.exception("Failed to launch library video analysis: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not start analysis for '{video_id}': {exc}",
        )


# ==========================================
# Job Cancellation & Management
# ==========================================

@router.post("/jobs/{job_id}/cancel")
def cancel_import_job(job_id: str) -> dict[str, Any]:
    """Cancel a running download or analysis job."""
    service = get_clipper_service()
    try:
        res = service.cancel_job(job_id)
        return res
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(val_err))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel job: {exc}",
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


# ==========================================
# Clips & Cleanup
# ==========================================

@router.get("/clips")
def list_imported_clips() -> list[dict[str, Any]]:
    """List all extracted clips from imported YouTube videos."""
    service = get_clipper_service()
    return [c.to_dict() for c in service.list_clips()]


@router.delete("/clips")
def clear_all_imported_clips(confirm: bool = Query(False, description="Confirm deletion of all imported data")) -> dict[str, Any]:
    """Delete ALL imported clips, jobs, and library videos to free disk space."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide confirm=true query parameter to clear all imports.",
        )
    service = get_clipper_service()
    return service.cleanup_all_imports()


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
    pipeline.scan_clips()

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
