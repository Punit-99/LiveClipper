"""Routes for YouTube Shorts uploading and daily quota management."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.events import ws_manager
from app.api.state import get_quota_tracker, get_shorts_pipeline
from app.config import load_settings
from app.logging_setup import get_logger
from app.shorts.pipeline import STATE_PROCESSED, STATE_UPLOADED

log = get_logger(__name__)

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


@router.post("/upload/verify-all")
def verify_all_uploads() -> dict[str, Any]:
    """Batch verify all uploaded Shorts against the YouTube Data API."""
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if not pipeline.uploader:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube uploader is not configured. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env or authenticate first.",
        )

    all_records = pipeline.manifest.all_records()
    uploaded_records: list[dict[str, Any]] = []
    for r in all_records:
        st = r.get("state")
        y_id = r.get("youtube_video_id") or r.get("youtube_id") or r.get("upload", {}).get("video_id")
        if (st == STATE_UPLOADED or y_id) and y_id:
            uploaded_records.append(r)

    tracker = pipeline.uploader.quota_tracker
    remaining_units = None
    if tracker:
        quota_status = tracker.get_status()
        remaining_units = max(0, tracker.max_units - quota_status.get("units_used", 0))

    if not uploaded_records:
        return {
            "verified_count": 0,
            "not_found_count": 0,
            "total_checked": 0,
            "results": {},
            "quota_units_used": 0,
            "remaining_units": remaining_units,
        }

    cid_to_vid = {}
    for r in uploaded_records:
        cid = r.get("clip_id")
        y_id = r.get("youtube_video_id") or r.get("youtube_id") or r.get("upload", {}).get("video_id")
        if cid and y_id:
            cid_to_vid[cid] = str(y_id).strip()

    batch_res = pipeline.uploader.verify_videos(list(cid_to_vid.values()))
    v_results = batch_res.get("results", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    verified_count = 0
    not_found_count = 0
    results_by_clip: dict[str, dict[str, Any]] = {}

    for cid, vid in cid_to_vid.items():
        v_res = v_results.get(vid, {"verified": False, "reason": "not_found", "video_id": vid})
        is_verified = bool(v_res.get("verified"))
        if is_verified:
            verified_count += 1
            pipeline.manifest.update_record(
                clip_id=cid,
                youtube_verified=True,
                youtube_verified_at=now_iso,
                youtube_verify_reason=None,
                youtube_status=v_res.get("status"),
            )
        else:
            not_found_count += 1
            pipeline.manifest.update_record(
                clip_id=cid,
                youtube_verified=False,
                youtube_verified_at=now_iso,
                youtube_verify_reason=v_res.get("reason", "not_found"),
            )
        results_by_clip[cid] = {**v_res, "clip_id": cid}

    if tracker:
        quota_status = tracker.get_status()
        remaining_units = max(0, tracker.max_units - quota_status.get("units_used", 0))

    return {
        "verified_count": verified_count,
        "not_found_count": not_found_count,
        "total_checked": len(cid_to_vid),
        "results": results_by_clip,
        "quota_units_used": batch_res.get("units_used", 0),
        "remaining_units": remaining_units,
    }


@router.get("/upload/{clip_id}/verify")
def verify_single_short(clip_id: str) -> dict[str, Any]:
    """Check if the YouTube video for a Short exists and is accessible."""
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if not pipeline.uploader:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube uploader is not configured. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env or authenticate first.",
        )

    # Resolve manifest record
    record = pipeline.manifest.get(clip_id)
    if not record:
        for r in pipeline.manifest.all_records():
            if (
                r.get("youtube_video_id") == clip_id
                or r.get("youtube_id") == clip_id
                or r.get("upload", {}).get("video_id") == clip_id
            ):
                record = r
                break

    video_id = ""
    if record:
        video_id = (
            record.get("youtube_video_id")
            or record.get("youtube_id")
            or record.get("upload", {}).get("video_id")
            or ""
        )
    elif len(clip_id) == 11 and not clip_id.startswith("clip"):
        video_id = clip_id

    if not video_id:
        return {
            "verified": False,
            "reason": "no_video_id",
            "clip_id": clip_id,
            "video_id": "",
        }

    result = pipeline.uploader.verify_video(video_id)
    now_iso = datetime.now(timezone.utc).isoformat()

    if record:
        cid = record.get("clip_id") or clip_id
        pipeline.manifest.update_record(
            clip_id=cid,
            youtube_verified=result.get("verified", False),
            youtube_verified_at=now_iso,
            youtube_verify_reason=result.get("reason"),
            youtube_status=result.get("status"),
        )

    tracker = pipeline.uploader.quota_tracker
    remaining_units = None
    if tracker:
        quota_status = tracker.get_status()
        remaining_units = max(0, tracker.max_units - quota_status.get("units_used", 0))

    return {
        **result,
        "clip_id": record.get("clip_id") if record else clip_id,
        "quota_units_used": 1,
        "remaining_units": remaining_units,
    }


@router.post("/upload/{clip_id}/reupload")
def reupload_single_short(clip_id: str, req: SingleUploadRequest | None = None) -> dict[str, Any]:
    """Reset status and re-upload a Short to YouTube."""
    payload = req or SingleUploadRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if not pipeline.uploader:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="YouTube uploader is not configured. Add YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET to .env or authenticate first.",
        )

    record = pipeline.manifest.get(clip_id)
    if not record:
        for r in pipeline.manifest.all_records():
            if (
                r.get("youtube_video_id") == clip_id
                or r.get("youtube_id") == clip_id
                or r.get("upload", {}).get("video_id") == clip_id
            ):
                record = r
                break

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Short manifest record not found for {clip_id}",
        )

    cid = record.get("clip_id") or clip_id
    old_video_id = (
        record.get("youtube_video_id")
        or record.get("youtube_id")
        or record.get("upload", {}).get("video_id")
    )

    # Check if old video still exists on YouTube and log warning
    if old_video_id:
        try:
            old_check = pipeline.uploader.verify_video(old_video_id)
            if old_check.get("verified"):
                log.warning("Previous upload (video_id: %s) still exists. New upload created.", old_video_id)
        except Exception as exc:
            log.debug("Verification check for previous video %s encountered: %s", old_video_id, exc)

    # Reset manifest entry from UPLOADED/FAILED back to PROCESSED
    pipeline.manifest.update_record(
        clip_id=cid,
        state=STATE_PROCESSED,
        youtube_verified=None,
        youtube_verify_reason=None,
    )

    if payload.selected_title:
        pipeline.manifest.update_record(clip_id=cid, selected_viral_title=payload.selected_title)

    privacy = payload.privacy_status or record.get("privacy_status") or settings.youtube_privacy_status

    ws_manager.broadcast_sync("upload_started", {"clip_id": cid, "is_reupload": True})

    try:
        summary = pipeline.upload_shorts(
            clip_id=cid,
            auto_upload=True,
            interactive=False,
            privacy_status=privacy,
        )

        items = summary.get("items", [])
        uploaded_item = items[0] if items else None

        if summary.get("uploaded", 0) > 0 and uploaded_item:
            new_yt_id = uploaded_item.get("youtube_id")
            new_yt_url = uploaded_item.get("youtube_url")
            pipeline.manifest.update_record(
                clip_id=cid,
                state=STATE_UPLOADED,
                youtube_verified=True,
                youtube_verified_at=datetime.now(timezone.utc).isoformat(),
                youtube_verify_reason=None,
                youtube_status=privacy,
            )
            ws_manager.broadcast_sync("upload_complete", {
                "clip_id": cid,
                "youtube_url": new_yt_url,
                "youtube_id": new_yt_id,
                "title": uploaded_item.get("title"),
                "is_reupload": True,
            })
            return {
                "status": "uploaded",
                "short": uploaded_item,
                "summary": summary,
                "previous_video_id": old_video_id,
            }

        error_msg = "Short could not be re-uploaded"
        if summary.get("rejected", 0) > 0:
            error_msg = "Re-upload was rejected by quota or score policy"
        elif summary.get("skipped", 0) > 0:
            error_msg = "Re-upload was skipped (video file missing)"
        elif summary.get("failed", 0) > 0:
            error_msg = "Re-upload failed during transmission"

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Re-upload failed: {exc}",
        ) from exc


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
