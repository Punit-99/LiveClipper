"""Clip extraction and sidecar generation for YouTube Video Clipper."""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.clipper.models import ImportClip, MomentCandidate, VideoMetadata

log = logging.getLogger(__name__)


def extract_clip(
    source_video_path: Path,
    moment: MomentCandidate,
    metadata: VideoMetadata,
    output_dir: Path,
    clip_index: int,
) -> ImportClip:
    """Extract a single clip and thumbnail snapshot using FFmpeg."""
    output_dir.mkdir(parents=True, exist_ok=True)
    clean_vid = metadata.video_id.replace("-", "_")
    clip_id = f"imp_{clean_vid}_{clip_index:02d}_{int(moment.start_time)}"
    clip_path = output_dir / f"{clip_id}.mp4"
    thumb_path = output_dir / f"{clip_id}.jpg"
    sidecar_path = output_dir / f"{clip_id}.json"

    # FFmpeg accurate clip cut with re-encode for clean keyframes
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{moment.start_time:.2f}",
        "-i", str(source_video_path),
        "-t", f"{moment.duration:.2f}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(clip_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as err:
        log.error("FFmpeg clip extraction failed for %s: %s", clip_id, err.stderr.decode("utf-8", "ignore"))
        raise RuntimeError(f"FFmpeg extraction failed: {err}") from err

    # Thumbnail snapshot at 20% into the clip
    thumb_sec = min(max(1.0, moment.duration * 0.20), moment.duration - 0.5)
    thumb_cmd = [
        "ffmpeg", "-y",
        "-ss", f"{thumb_sec:.2f}",
        "-i", str(clip_path),
        "-vframes", "1",
        "-q:v", "2",
        str(thumb_path),
    ]
    try:
        subprocess.run(thumb_cmd, check=True, capture_output=True, timeout=30)
    except Exception as exc:
        log.warning("Snapshot generation failed for %s: %s", clip_id, exc)

    created_iso = datetime.now(timezone.utc).isoformat()

    clip_record = ImportClip(
        clip_id=clip_id,
        job_id=clean_vid,
        video_id=metadata.video_id,
        video_title=metadata.title,
        channel=metadata.channel,
        start_time=moment.start_time,
        end_time=moment.end_time,
        duration=moment.duration,
        score=moment.score,
        reason=moment.reason,
        suggested_title=moment.suggested_title,
        transcript=moment.transcript_excerpt,
        file_path=str(clip_path),
        thumbnail_path=str(thumb_path) if thumb_path.is_file() else "",
        created_at=created_iso,
        is_imported=True,
        source_type="imported",
        source_url=metadata.url,
    )

    # Save JSON sidecar for Shorts pipeline compatibility
    sidecar_data = {
        "clip_id": clip_id,
        "streamer_name": metadata.channel,
        "channel": metadata.channel,
        "stream_id": metadata.video_id,
        "source_video_id": metadata.video_id,
        "start_timestamp": moment.start_time,
        "end_timestamp": moment.end_time,
        "duration": moment.duration,
        "score": moment.score,
        "analysis_reason": moment.reason,
        "suggested_title": moment.suggested_title,
        "transcript": moment.transcript_excerpt,
        "source_type": "imported",
        "is_imported": True,
        "source_url": metadata.url,
        "source_title": metadata.title,
        "video_path": str(clip_path),
        "thumbnail_path": str(thumb_path) if thumb_path.is_file() else "",
        "created_at": created_iso,
        "signal_scores": moment.signal_scores,
    }
    sidecar_path.write_text(json.dumps(sidecar_data, indent=2), encoding="utf-8")

    return clip_record


def extract_all_clips(
    source_video_path: Path,
    moments: list[MomentCandidate],
    metadata: VideoMetadata,
    output_dir: Path,
    progress_cb: Callable[[int, int, ImportClip | None], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[ImportClip]:
    """Extract all selected moments into MP4 clips with sidecars."""
    clips: list[ImportClip] = []
    total = len(moments)
    for idx, moment in enumerate(moments, 1):
        if cancel_check and cancel_check():
            log.info("Clip extraction halted early due to cancellation.")
            break
        clip = extract_clip(
            source_video_path=source_video_path,
            moment=moment,
            metadata=metadata,
            output_dir=output_dir,
            clip_index=idx,
        )
        clips.append(clip)
        if progress_cb:
            try:
                progress_cb(idx, total, clip)
            except TypeError:
                progress_cb(idx, total)
    return clips
    return clips
