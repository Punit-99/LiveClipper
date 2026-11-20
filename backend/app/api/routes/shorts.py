"""Routes for querying, previewing, and processing vertical Shorts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.api.events import ws_manager
from app.api.media_stream import range_stream_response
from app.api.state import get_shorts_pipeline
from app.config import load_settings
from app.discovery.youtube_finder import resolve_channel_name
from app.shorts.pipeline import STATE_DISCOVERED, STATE_FAILED
from app.shorts.queue_manager import get_processing_queue

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

        # Resolve recorded_at_iso and processed_at_iso
        orig_recorded_iso = ""
        source_sc_str = rec.get("source_sidecar_path")
        if source_sc_str and Path(source_sc_str).is_file():
            try:
                with open(source_sc_str, "r", encoding="utf-8") as sfh:
                    orig_sc = json.load(sfh)
                    orig_recorded_iso = orig_sc.get("recorded_at_iso") or orig_sc.get("extracted_at_iso") or ""
            except Exception:
                pass

        if not orig_recorded_iso:
            src_clip_str = rec.get("source_clip_path")
            if src_clip_str and Path(src_clip_str).is_file():
                try:
                    orig_recorded_iso = datetime.fromtimestamp(Path(src_clip_str).stat().st_mtime, tz=timezone.utc).isoformat()
                except Exception:
                    pass

        processed_iso = rec.get("updated_at_iso") or ""
        if not processed_iso and short_video_path.is_file():
            try:
                processed_iso = datetime.fromtimestamp(short_video_path.stat().st_mtime, tz=timezone.utc).isoformat()
            except Exception:
                pass

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
            "recorded_at_iso": orig_recorded_iso or rec.get("updated_at_iso") or "",
            "processed_at_iso": processed_iso or rec.get("updated_at_iso") or "",
            "youtube_verified": rec.get("youtube_verified"),
            "youtube_verified_at": rec.get("youtube_verified_at"),
            "youtube_verify_reason": rec.get("youtube_verify_reason"),
            "youtube_status": rec.get("youtube_status"),
        })

    output.sort(key=lambda x: x.get("processed_at_iso") or x.get("updated_at", ""), reverse=True)
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


@router.get("/shorts/queue")
def get_shorts_queue() -> dict[str, Any]:
    """Return the current clip processing queue status."""
    queue = get_processing_queue()
    return queue.get_status()


@router.post("/shorts/{clip_id}/cancel")
def cancel_short_processing(clip_id: str) -> dict[str, Any]:
    """Cancel a queued short processing task before execution begins."""
    queue = get_processing_queue()
    success, msg = queue.cancel(clip_id)
    if not success:
        if "already" in msg:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg)
    return {"status": "cancelled", "clip_id": clip_id, "message": msg}


@router.post("/shorts/process")
def process_pending_shorts(
    background_tasks: BackgroundTasks,
    req: ProcessShortsRequest | None = None,
) -> dict[str, Any]:
    """Trigger batch processing of pending discovered clips into 9:16 Shorts via queue."""
    payload = req or ProcessShortsRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if payload.engine:
        pipeline.bridge.engine = payload.engine
    if payload.reframe_mode:
        pipeline.bridge.reframe_mode = payload.reframe_mode
    if payload.subtitle_style:
        pipeline.bridge.subtitle_style = payload.subtitle_style

    queue = get_processing_queue(settings.max_concurrent_processing)

    all_clips = pipeline.scan_clips(channel=payload.channel)
    candidates: list[dict[str, Any]] = []
    for c in all_clips:
        cid = c["clip_id"]
        st = c.get("state")
        if payload.force or st in (STATE_DISCOVERED, STATE_FAILED):
            candidates.append(c)

    if not candidates:
        return {
            "status": "queued",
            "total": 0,
            "processing": 0,
            "queued": 0,
            "items": [],
        }

    processing_count = 0
    queued_count = 0
    items_info: list[dict[str, Any]] = []

    for item in candidates:
        cid = item["clip_id"]
        title = item.get("title") or f"Clip {cid}"

        def make_worker(target_cid=cid, force_flag=payload.force):
            def worker():
                def on_progress(p_cid: str, stage: str, pct: int) -> None:
                    queue.report_progress(p_cid, stage, pct)

                summary = pipeline.process_pending_shorts(
                    clip_id_or_file=target_cid,
                    force=force_flag,
                    progress_callback=on_progress,
                )
                if summary.get("processed", 0) == 0:
                    err_msg = "Processing completed but no Short was produced."
                    if summary.get("items"):
                        err_msg = summary["items"][0].get("error", err_msg)
                    raise RuntimeError(err_msg)
                return summary

            return worker

        q_item, is_active = queue.enqueue(cid, title, make_worker())
        if is_active:
            processing_count += 1
            background_tasks.add_task(queue.run_item, cid)
        else:
            queued_count += 1

        items_info.append(q_item.to_dict())

    return {
        "status": "queued",
        "total": len(candidates),
        "processing": processing_count,
        "queued": queued_count,
        "items": items_info,
    }


@router.post("/shorts/{clip_id}/process")
def process_single_short(
    clip_id: str,
    background_tasks: BackgroundTasks,
    req: ProcessShortsRequest | None = None,
) -> dict[str, Any]:
    """Process a single specific clip into a vertical Short in background task."""
    payload = req or ProcessShortsRequest()
    settings = load_settings()
    pipeline = get_shorts_pipeline(settings=settings)

    if payload.engine:
        pipeline.bridge.engine = payload.engine
    if payload.reframe_mode:
        pipeline.bridge.reframe_mode = payload.reframe_mode
    if payload.subtitle_style:
        pipeline.bridge.subtitle_style = payload.subtitle_style

    # Resolve clip title for progress display
    record = pipeline.manifest.get(clip_id)
    clip_title = record.get("title") if record else None
    if not clip_title:
        for p in settings.clips_output_dir.rglob(f"*{clip_id}*.json"):
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        clip_title = data.get("analysis", {}).get("title") or data.get("title")
                except Exception:
                    pass
                if clip_title:
                    break
    if not clip_title:
        clip_title = f"Clip {clip_id}"

    queue = get_processing_queue(settings.max_concurrent_processing)

    def do_work():
        def on_single_progress(cid: str, stage: str, pct: int) -> None:
            queue.report_progress(cid, stage, pct)

        summary = pipeline.process_pending_shorts(
            clip_id_or_file=clip_id,
            force=True,  # Force re-render for single clip
            progress_callback=on_single_progress,
        )
        if summary.get("processed", 0) == 0:
            err_msg = "Processing completed but no Short was produced."
            if summary.get("items"):
                err_msg = summary["items"][0].get("error", err_msg)
            raise RuntimeError(err_msg)
        return summary

    item, is_active = queue.enqueue(clip_id, clip_title, do_work)

    if is_active:
        background_tasks.add_task(queue.run_item, clip_id)
        return {
            "status": "processing",
            "clip_id": clip_id,
            "title": clip_title,
        }
    else:
        return {
            "status": "queued",
            "clip_id": clip_id,
            "position": item.position,
            "title": clip_title,
        }


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
