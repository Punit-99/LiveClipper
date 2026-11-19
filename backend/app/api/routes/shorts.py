"""Routes for querying, previewing, and processing vertical Shorts."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.events import ws_manager
from app.api.media_stream import range_stream_response
from app.api.state import get_shorts_pipeline
from app.config import load_settings
from app.discovery.youtube_finder import resolve_channel_name

log = logging.getLogger(__name__)

router = APIRouter(tags=["Shorts"])


class ProcessShortsRequest(BaseModel):
    engine: str | None = Field(default=None, description="Reframing engine: 'auto', 'openshorts', or 'ffmpeg_fallback'")
    reframe_mode: str | None = Field(default=None, description="Reframe mode: 'auto', 'split', 'track', or 'wide'")
    subtitle_style: str | None = Field(default=None, description="Subtitle style: 'bold', 'pill', 'hormozi', 'default', 'clean', or 'none'")
    channel: str | None = Field(default=None, description="Process only clips for a specific channel")
    force: bool = Field(default=False, description="Re-process even if previously generated")


@router.get("/shorts")
def list_shorts(
    channel: str | None = Query(default=None, description="Filter shorts by streamer channel"),
    state: str | None = Query(default=None, description="Filter shorts by state ('PROCESSED', 'APPROVED', 'UPLOADED')"),
) -> list[dict[str, Any]]:
    """List all processed vertical 9:16 YouTube Shorts with viral metadata."""
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)
    records = pipeline.manifest.all_records()

    output: list[dict[str, Any]] = []
    for rec in records:
        cid = rec.get("clip_id")
        raw_ch = rec.get("channel", "streamer")
        ch = resolve_channel_name(raw_ch)
        st = rec.get("state", "PROCESSED")

        short_video_str = rec.get("short_video_path")
        if not short_video_str:
            continue
        short_video_path = Path(short_video_str)
        if not short_video_path.is_file():
            cand = settings.shorts_output_dir / raw_ch / short_video_path.name
            if cand.is_file():
                short_video_path = cand
            else:
                cand2 = settings.shorts_output_dir / short_video_path.name
                if cand2.is_file():
                    short_video_path = cand2
                else:
                    continue

        if channel and ch.lower() != channel.lower() and raw_ch.lower() != channel.lower():
            continue
        if state and st.upper() != state.upper():
            continue

        # Read short sidecar for detailed viral metadata
        sidecar_path = rec.get("short_sidecar_path")
        sidecar_file = Path(sidecar_path) if sidecar_path else None
        if sidecar_file and not sidecar_file.is_file():
            cand_sc = settings.shorts_output_dir / ch / sidecar_file.name
            if cand_sc.is_file():
                sidecar_file = cand_sc
            else:
                cand_sc2 = settings.shorts_output_dir / sidecar_file.name
                sidecar_file = cand_sc2 if cand_sc2.is_file() else None

        sidecar_data: dict[str, Any] = {}
        if sidecar_file and sidecar_file.is_file():
            try:
                with open(sidecar_file, "r", encoding="utf-8") as fh:
                    sidecar_data = json.load(fh)
            except Exception:
                pass

        viral_meta = sidecar_data.get("viral_metadata") or {}
        viral_titles: list[dict[str, Any]] = []
        if isinstance(viral_meta.get("titles"), list):
            for t in viral_meta["titles"]:
                if isinstance(t, dict):
                    viral_titles.append(t)
                else:
                    viral_titles.append({"title": str(t), "style": "general", "predicted_impact": "High"})

        raw_titles = rec.get("viral_titles") or []
        if not viral_titles and raw_titles:
            viral_titles = [{"title": str(t), "style": "general", "predicted_impact": "High"} for t in raw_titles]

        selected_title = (
            rec.get("selected_viral_title")
            or viral_meta.get("selected_title")
            or (viral_titles[0]["title"] if viral_titles else rec.get("title"))
            or "Live Stream Highlight #Shorts"
        )

        upload_info = rec.get("upload") or {}
        youtube_url = rec.get("youtube_url") or upload_info.get("youtube_url")
        youtube_id = rec.get("youtube_id") or upload_info.get("video_id")

        output.append({
            "id": cid,
            "filename": short_video_path.name,
            "filepath": str(short_video_path),
            "channel": ch,
            "platform": rec.get("platform", "twitch"),
            "score": int(rec.get("highlight_score", 0)),
            "title": rec.get("title") or "Stream Highlight",
            "category": rec.get("category") or "highlight",
            "duration": float(rec.get("final_duration") or 0.0),
            "state": st,
            "processing_engine": rec.get("processing_engine") or "openshorts",
            "selected_title": selected_title,
            "viral_titles": viral_titles,
            "description": viral_meta.get("description") or rec.get("description") or "",
            "tags": viral_meta.get("tags") or rec.get("tags") or [],
            "youtube_url": youtube_url,
            "youtube_id": youtube_id,
            "updated_at": rec.get("updated_at_iso") or "",
        })

    output.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return output


@router.api_route("/shorts/{clip_id}/preview", methods=["GET", "HEAD"])
def preview_short(clip_id: str, request: Request) -> Any:
    """Serve the 9:16 vertical Short video file with byte-range streaming support."""
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)
    record = pipeline.manifest.get(clip_id)

    short_path = None
    if record and record.get("short_video_path"):
        cand = Path(record["short_video_path"])
        if cand.is_file():
            short_path = cand

    if not short_path:
        # Search by glob in shorts_dir
        for p in settings.shorts_output_dir.rglob(f"{clip_id}*_short.mp4"):
            if p.is_file():
                short_path = p
                break

    if not short_path or not short_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vertical short with ID '{clip_id}' was not found.",
        )

    return range_stream_response(short_path, request, media_type="video/mp4")


@router.post("/shorts/process")
def process_pending_shorts(req: ProcessShortsRequest | None = None) -> dict[str, Any]:
    """Trigger batch processing of pending discovered clips into 9:16 Shorts."""
    payload = req or ProcessShortsRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if payload.engine:
        pipeline.bridge.engine = payload.engine
    if payload.reframe_mode:
        pipeline.bridge.reframe_mode = payload.reframe_mode
    if payload.subtitle_style:
        pipeline.bridge.subtitle_style = payload.subtitle_style

    ws_manager.broadcast_sync("shorts_processing_started", {"channel": payload.channel})

    try:
        summary = pipeline.process_pending_shorts(
            channel=payload.channel,
            force=payload.force,
        )
        ws_manager.broadcast_sync("shorts_processing_complete", summary)
        return summary
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Shorts batch processing failed: {exc}",
        ) from exc


@router.post("/shorts/{clip_id}/process")
def process_single_short(clip_id: str, req: ProcessShortsRequest | None = None) -> dict[str, Any]:
    """Process a single specific clip into a vertical Short."""
    payload = req or ProcessShortsRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if payload.engine:
        pipeline.bridge.engine = payload.engine
    if payload.reframe_mode:
        pipeline.bridge.reframe_mode = payload.reframe_mode
    if payload.subtitle_style:
        pipeline.bridge.subtitle_style = payload.subtitle_style

    ws_manager.broadcast_sync("short_processing_started", {"clip_id": clip_id})

    try:
        summary = pipeline.process_pending_shorts(
            clip_id_or_file=clip_id,
            force=True,  # Force re-render for single clip
        )
        ws_manager.broadcast_sync("short_processing_complete", {"clip_id": clip_id, "summary": summary})
        return summary
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process clip '{clip_id}' into short: {exc}",
        ) from exc


def _find_short_files_by_id(shorts_dir: Path, clip_id: str, record: dict[str, Any] | None) -> list[Path]:
    """Locate all short video and sidecar files on disk for a given clip_id."""
    clean_id = clip_id.lower().strip()
    files: list[Path] = []

    if record:
        for key in ("short_video_path", "short_sidecar_path"):
            val = record.get(key)
            if val:
                p = Path(val)
                if p.is_file():
                    files.append(p)
                else:
                    ch = record.get("channel", "")
                    cand = shorts_dir / ch / p.name
                    if cand.is_file():
                        files.append(cand)
                    cand2 = shorts_dir / p.name
                    if cand2.is_file():
                        files.append(cand2)

    for p in shorts_dir.rglob(f"*{clean_id}*"):
        if p.is_file() and p.suffix.lower() in (".mp4", ".json") and p.name != "manifest.jsonl":
            files.append(p)

    return list(dict.fromkeys(files))


@router.delete("/shorts/{clip_id}")
def delete_short(clip_id: str) -> dict[str, Any]:
    """Delete a single vertical Short (MP4 + JSON sidecar + manifest entry)."""
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)
    shorts_dir = settings.shorts_output_dir
    record = pipeline.manifest.get(clip_id)

    files_to_delete = _find_short_files_by_id(shorts_dir, clip_id, record)

    if not record and not files_to_delete:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Vertical short with ID '{clip_id}' was not found.",
        )

    youtube_id = (record.get("youtube_id") if record else None) or (
        record.get("upload", {}).get("video_id") if record else None
    )
    if youtube_id or (record and record.get("state") == "UPLOADED"):
        log.warning(
            "Note: This Short was already uploaded to YouTube (video ID: %s). The YouTube copy is not affected.",
            youtube_id or "unknown",
        )

    for f in files_to_delete:
        try:
            if f.is_file():
                f.unlink()
        except OSError as exc:
            log.warning("Could not delete short file %s: %s", f, exc)

    pipeline.manifest.remove_record(clip_id)
    ws_manager.broadcast_sync("short_deleted", {"id": clip_id})
    return {"deleted": True, "id": clip_id}


@router.delete("/shorts")
def delete_all_shorts(
    confirm: bool = Query(default=False, description="Explicit confirmation required to delete all shorts"),
) -> dict[str, Any]:
    """Delete all vertical Shorts, sidecars, and clear manifest entries."""
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bulk delete requires confirm=true query parameter.",
        )

    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)
    shorts_dir = settings.shorts_output_dir

    all_records = pipeline.manifest.all_records()
    for rec in all_records:
        y_id = rec.get("youtube_id") or rec.get("upload", {}).get("video_id")
        if y_id or rec.get("state") == "UPLOADED":
            log.warning(
                "Note: This Short was already uploaded to YouTube (video ID: %s). The YouTube copy is not affected.",
                y_id or "unknown",
            )

    deleted_count = 0
    if shorts_dir.is_dir():
        for f in list(shorts_dir.rglob("*.mp4")) + list(shorts_dir.rglob("*.json")):
            if f.name == "manifest.jsonl":
                continue
            try:
                if f.is_file():
                    f.unlink()
                    deleted_count += 1
            except OSError as exc:
                log.warning("Could not delete short file %s: %s", f, exc)

    pipeline.manifest.clear_records()
    ws_manager.broadcast_sync("all_shorts_deleted", {"count": deleted_count})
    return {"deleted": True, "count": deleted_count}
