"""Duration adapter for YouTube Shorts.

Ensures video clips conform to YouTube Shorts duration constraints.
Clips <= 60s are preserved as-is without re-encoding.
Clips > 60s are trimmed to a 55s window centered on the highlight's
target_pts (or peak chat reaction), clamped to video boundaries.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_SHORTS_MAX_DURATION = 60.0
DEFAULT_SHORTS_TARGET_DURATION = 55.0


def probe_video_duration(video_path: Path | str) -> float:
    """Probe exact duration in seconds using ffprobe."""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"Video file not found: {path}")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10.0)
        output = proc.stdout.strip()
        return round(float(output), 3)
    except (subprocess.SubprocessError, ValueError) as exc:
        log.warning("ffprobe failed on %s: %s", path, exc)
        raise RuntimeError(f"Could not probe duration of {path}: {exc}") from exc


def calculate_trim_window(
    duration: float,
    target_pts: float | None = None,
    target_duration: float = DEFAULT_SHORTS_TARGET_DURATION,
    clip_start_wall: float | None = None,
) -> tuple[float, float]:
    """Calculate the [start_sec, end_sec] trim window centered on target_pts.

    Clamps the window so it strictly fits within [0.0, duration].
    """
    if duration <= 0.0:
        return 0.0, 0.0

    target_duration = min(target_duration, duration)

    # Determine relative target second inside the clip
    if target_pts is not None:
        # If target_pts is an absolute wall-clock timestamp (Unix epoch)
        if clip_start_wall is not None and target_pts > duration:
            rel_target = target_pts - clip_start_wall
        else:
            rel_target = target_pts
        rel_target = max(0.0, min(duration, rel_target))
    else:
        # Default to clip midpoint
        rel_target = duration / 2.0

    half = target_duration / 2.0
    start = rel_target - half
    end = rel_target + half

    # Clamp start and shift end if needed
    if start < 0.0:
        start = 0.0
        end = min(duration, target_duration)

    # Clamp end and shift start if needed
    if end > duration:
        end = duration
        start = max(0.0, duration - target_duration)

    return round(start, 3), round(end, 3)


def adapt_clip_duration(
    clip_path: Path | str,
    sidecar_path_or_data: Path | str | dict[str, Any] | None = None,
    max_duration: float = DEFAULT_SHORTS_MAX_DURATION,
    target_duration: float = DEFAULT_SHORTS_TARGET_DURATION,
    output_dir: Path | str | None = None,
) -> tuple[Path, bool, dict[str, Any]]:
    """Check clip duration and trim if exceeding max_duration.

    Returns:
        (resulting_path, was_trimmed, metadata_dict)
    """
    path = Path(clip_path)
    if not path.is_file():
        raise FileNotFoundError(f"Clip not found: {path}")

    duration = probe_video_duration(path)

    # If duration is within allowable threshold, keep as-is
    if duration <= max_duration:
        log.info(
            "Clip duration %.1fs <= max %.1fs; keeping original file without trimming.",
            duration,
            max_duration,
        )
        return path, False, {
            "original_duration": duration,
            "final_duration": duration,
            "was_trimmed": False,
        }

    # Extract target_pts and clip_start_wall from sidecar if available
    sidecar = _load_sidecar_data(sidecar_path_or_data)
    clip_plan = sidecar.get("clip_plan") or {}

    target_pts = clip_plan.get("target_pts")
    if target_pts is None:
        target_pts = clip_plan.get("peak_chat_timestamp")

    clip_start_wall = (
        clip_plan.get("requested_clip_start")
        or clip_plan.get("event_start_timestamp")
    )

    start_sec, end_sec = calculate_trim_window(
        duration=duration,
        target_pts=target_pts,
        target_duration=target_duration,
        clip_start_wall=clip_start_wall,
    )
    trim_span = round(end_sec - start_sec, 3)

    out_folder = Path(output_dir) if output_dir else path.parent
    out_folder.mkdir(parents=True, exist_ok=True)
    trimmed_path = out_folder / f"{path.stem}_trimmed{path.suffix}"

    log.info(
        "Trimming clip %.1fs -> %.1fs (window=[%.1fs - %.1fs]) to: %s",
        duration,
        trim_span,
        start_sec,
        end_sec,
        trimmed_path.name,
    )

    # Stream copy first for speed (no re-encoding)
    copy_cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_sec),
        "-i",
        str(path),
        "-t",
        str(trim_span),
        "-c",
        "copy",
        str(trimmed_path),
    ]

    try:
        proc = subprocess.run(copy_cmd, capture_output=True, text=True, timeout=30.0)
        if proc.returncode != 0:
            raise subprocess.SubprocessError(proc.stderr)
    except Exception as exc:
        log.warning("Stream-copy trim failed (%s); retrying with re-encode fallback...", exc)
        reencode_cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_sec),
            "-i",
            str(path),
            "-t",
            str(trim_span),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(trimmed_path),
        ]
        proc = subprocess.run(reencode_cmd, capture_output=True, text=True, check=True, timeout=60.0)

    final_duration = probe_video_duration(trimmed_path)

    metadata = {
        "original_duration": duration,
        "final_duration": final_duration,
        "trim_start": start_sec,
        "trim_end": end_sec,
        "target_pts": target_pts,
        "was_trimmed": True,
    }
    return trimmed_path, True, metadata


def _load_sidecar_data(sidecar: Path | str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(sidecar, dict):
        return sidecar
    if sidecar:
        p = Path(sidecar)
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                log.warning("Could not read sidecar at %s: %s", p, exc)
    return {}
