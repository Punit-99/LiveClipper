"""Routes for querying and previewing extracted video clips."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from app.api.events import ws_manager
from app.api.media_stream import range_stream_response
from app.api.state import get_shorts_pipeline
from app.config import load_settings
from app.discovery.youtube_finder import resolve_channel_name

log = logging.getLogger(__name__)

router = APIRouter(tags=["Clips"])


def _find_clip_file_by_id(clips_dir: Path, clip_id: str) -> tuple[Path | None, Path | None]:
    """Search clips directory for an mp4 file and matching json sidecar containing clip_id."""
    clean_id = clip_id.lower().strip()
    for mp4_path in clips_dir.rglob("*.mp4"):
        if clean_id in mp4_path.stem.lower():
            json_path = mp4_path.with_suffix(".json")
            return mp4_path, json_path if json_path.is_file() else None

    # Also search by reading JSON sidecars directly if stem didn't match
    for json_path in clips_dir.rglob("*.json"):
        try:
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            event_id = str(data.get("event_id") or "")
            if event_id.lower().startswith(clean_id) or data.get("clip_id") == clean_id:
                mp4_candidate = json_path.with_suffix(".mp4")
                if mp4_candidate.is_file():
                    return mp4_candidate, json_path
        except Exception:
            continue

    return None, None


@router.get("/clips")
def list_clips(
    channel: str | None = Query(default=None, description="Filter clips by streamer channel"),
    platform: str | None = Query(default=None, description="Filter clips by platform ('twitch' or 'youtube')"),
    min_score: int | None = Query(default=None, ge=0, le=100, description="Minimum highlight score"),
) -> list[dict[str, Any]]:
    """List all extracted video clips with metadata and processing status."""
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)
    records = pipeline.scan_clips(channel=channel)

    # Cross-reference with manifest to check if short exists
    manifest_records = pipeline.manifest.all_records()
    manifest_by_id = {r.get("clip_id"): r for r in manifest_records}

    output: list[dict[str, Any]] = []
    for rec in records:
        cid = rec.get("clip_id")
        src_path = Path(rec.get("source_clip_path", ""))
        sidecar_path = rec.get("source_sidecar_path")

        # Read sidecar for extra details (duration, timestamps, quotes)
        sidecar_data: dict[str, Any] = {}
        if sidecar_path and Path(sidecar_path).is_file():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as fh:
                    sidecar_data = json.load(fh)
            except Exception:
                pass

        analysis = sidecar_data.get("analysis") or {}
        score = int(rec.get("highlight_score") or analysis.get("highlight_score") or 0)

        # Filters
        if min_score is not None and score < min_score:
            continue

        c_platform = rec.get("platform") or sidecar_data.get("platform") or "twitch"
        if platform and c_platform.lower() != platform.lower():
            continue

        c_channel = resolve_channel_name(rec.get("channel") or sidecar_data.get("streamer_name") or "unknown")
        if channel and c_channel.lower() != channel.lower() and str(rec.get("channel", "")).lower() != channel.lower():
            continue

        # Duration
        selection = sidecar_data.get("selection") or {}
        duration = float(
            selection.get("target_duration_seconds")
            or selection.get("coverage", {}).get("total_media_duration")
            or 0.0
        )

        manifest_entry = manifest_by_id.get(cid)
        has_short = bool(
            manifest_entry
            and manifest_entry.get("short_video_path")
            and Path(manifest_entry["short_video_path"]).is_file()
        )

        output.append({
            "id": cid,
            "filename": src_path.name,
            "filepath": str(src_path),
            "channel": c_channel,
            "platform": c_platform,
            "score": score,
            "title": rec.get("title") or analysis.get("title") or "Stream Highlight",
            "category": rec.get("category") or analysis.get("category") or "highlight",
            "duration": round(duration, 1),
            "reasoning": analysis.get("reasoning") or "",
            "quotes": analysis.get("key_quotes") or [],
            "extracted_at": sidecar_data.get("extracted_at_iso") or "",
            "has_short": has_short,
            "short_state": manifest_entry.get("state") if manifest_entry else "DISCOVERED",
            "short_path": manifest_entry.get("short_video_path") if manifest_entry else None,
        })

    # Sort newest / highest score first
    output.sort(key=lambda x: (x["score"], x["extracted_at"]), reverse=True)
    return output


@router.api_route("/clips/{clip_id}/preview", methods=["GET", "HEAD"])
def preview_clip(clip_id: str, request: Request) -> Any:
    """Serve the clip's MP4 video file supporting HTTP 206 Partial Content byte ranges."""
    settings = load_settings()
    clips_dir = settings.clips_output_dir
    mp4_path, _ = _find_clip_file_by_id(clips_dir, clip_id)

    if not mp4_path or not mp4_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clip video file with ID '{clip_id}' was not found.",
        )

    return range_stream_response(mp4_path, request, media_type="video/mp4")


@router.delete("/clips/{clip_id}")
def delete_clip(clip_id: str) -> dict[str, Any]:
    """Delete a single clip (MP4 video + JSON sidecar metadata)."""
    settings = load_settings()
    clips_dir = settings.clips_output_dir
    mp4_path, json_path = _find_clip_file_by_id(clips_dir, clip_id)

    if not mp4_path and not json_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Clip video file with ID '{clip_id}' was not found.",
        )

    if mp4_path and mp4_path.is_file():
        try:
            mp4_path.unlink()
        except OSError as exc:
            log.warning("Could not delete mp4 file %s: %s", mp4_path, exc)

    if json_path and json_path.is_file():
        try:
            json_path.unlink()
        except OSError as exc:
            log.warning("Could not delete json sidecar %s: %s", json_path, exc)

    ws_manager.broadcast_sync("clip_deleted", {"id": clip_id})
    return {"deleted": True, "id": clip_id}


@router.delete("/clips")
def delete_all_clips(
    confirm: bool = Query(default=False, description="Explicit confirmation required to delete all clips"),
) -> dict[str, Any]:
    """Delete all extracted clips and their sidecars."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bulk delete requires confirm=true query parameter.",
        )

    settings = load_settings()
    clips_dir = settings.clips_output_dir
    deleted_count = 0

    if clips_dir.is_dir():
        for f in list(clips_dir.rglob("*.mp4")) + list(clips_dir.rglob("*.json")):
            try:
                if f.is_file():
                    f.unlink()
                    deleted_count += 1
            except Exception as exc:
                log.warning("Could not delete clip file %s: %s", f, exc)

    ws_manager.broadcast_sync("all_clips_deleted", {"count": deleted_count})
    return {"deleted": True, "count": deleted_count}
