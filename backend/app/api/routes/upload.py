"""Routes for YouTube Shorts uploading and daily quota management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.events import ws_manager
from app.api.state import get_quota_tracker, get_shorts_pipeline
from app.config import load_settings

router = APIRouter(tags=["YouTube Upload"])


class SingleUploadRequest(BaseModel):
    privacy_status: str | None = Field(default=None, description="Privacy status: 'public', 'unlisted', or 'private' (default: public)")
    selected_title: str | None = Field(default=None, description="Optional chosen viral title")


class AutoUploadRequest(BaseModel):
    min_score: int | None = Field(default=None, description="Minimum highlight score threshold (0-100)")
    privacy_status: str | None = Field(default=None, description="Privacy status: 'public', 'unlisted', or 'private' (default: public)")
    channel: str | None = Field(default=None, description="Optional streamer channel filter")


@router.get("/upload/quota")
def get_upload_quota() -> dict[str, Any]:
    """Return the current YouTube daily quota usage and upload availability."""
    settings = load_settings()
    tracker = get_quota_tracker(settings=settings)
    quota_status = tracker.get_status()
    can_upload, reason = tracker.can_upload()

    return {
        **quota_status,
        "can_upload": can_upload,
        "reason": reason,
        "is_configured": bool(settings.youtube_client_id and settings.youtube_client_secret),
    }


@router.post("/upload/{clip_id}")
def upload_single_short(clip_id: str, req: SingleUploadRequest | None = None) -> dict[str, Any]:
    """Upload a specific processed Short to YouTube."""
    payload = req or SingleUploadRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if not pipeline.uploader:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube uploader is not configured. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env or authenticate first.",
        )

    # If title override is given, update record in manifest first
    if payload.selected_title:
        pipeline.manifest.update_record(clip_id=clip_id, selected_viral_title=payload.selected_title)

    privacy = payload.privacy_status or settings.youtube_privacy_status

    ws_manager.broadcast_sync("upload_started", {"clip_id": clip_id})

    try:
        summary = pipeline.upload_shorts(
            clip_id=clip_id,
            auto_upload=True,
            interactive=False,
            privacy_status=privacy,
        )

        items = summary.get("items", [])
        uploaded_item = items[0] if items else None

        if summary.get("uploaded", 0) > 0 and uploaded_item:
            ws_manager.broadcast_sync("upload_complete", {
                "clip_id": clip_id,
                "youtube_url": uploaded_item.get("youtube_url"),
                "youtube_id": uploaded_item.get("youtube_id"),
                "title": uploaded_item.get("title"),
            })
            return {"status": "uploaded", "short": uploaded_item, "summary": summary}

        error_msg = "Short could not be uploaded"
        if summary.get("rejected", 0) > 0:
            error_msg = "Short was rejected by quota or score policy"
        elif summary.get("skipped", 0) > 0:
            error_msg = "Short was skipped (missing video or not found)"
        elif summary.get("failed", 0) > 0:
            error_msg = "Upload failed during transmission"

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Upload failed: {exc}",
        ) from exc


@router.post("/upload/auto")
def auto_upload_shorts(req: AutoUploadRequest | None = None) -> dict[str, Any]:
    """Auto-upload all eligible Shorts meeting the highlight score threshold."""
    payload = req or AutoUploadRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if not pipeline.uploader:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube uploader is not configured. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env or authenticate first.",
        )

    min_score = payload.min_score if payload.min_score is not None else settings.youtube_min_score
    privacy = payload.privacy_status or settings.youtube_privacy_status

    ws_manager.broadcast_sync("batch_upload_started", {"min_score": min_score, "channel": payload.channel})

    try:
        summary = pipeline.upload_shorts(
            channel=payload.channel,
            min_score=min_score,
            auto_upload=True,
            interactive=False,
            privacy_status=privacy,
        )

        ws_manager.broadcast_sync("batch_upload_complete", summary)
        return summary
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Batch upload failed: {exc}",
        ) from exc
