"""FFmpeg utilities and encoder selection for video processing.

Centralized video/audio encoder selection, filter string escaping,
and resolution/duration probing.
"""

from __future__ import annotations

import os
import subprocess
import threading
from fractions import Fraction
from pathlib import Path
from typing import Sequence

# Quality tiers
QUALITY = "quality"            # -preset medium -crf 18
QUALITY_FAST = "quality_fast"  # -preset fast -crf 18
DELIVERY = "delivery"          # -preset fast -crf 22
INTERMEDIATE = "intermediate"  # -preset fast -crf 18

_X264_ARGS: dict[str, list[str]] = {
    QUALITY: [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-level", "4.2",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    ],
    QUALITY_FAST: [
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-profile:v", "high", "-level", "4.2",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    ],
    DELIVERY: [
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-profile:v", "high", "-level", "4.2",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    ],
    INTERMEDIATE: ["-c:v", "libx264", "-preset", "fast", "-crf", "18"],
}

_NVENC_ARGS: dict[str, list[str]] = {
    QUALITY: [
        "-c:v", "h264_nvenc", "-preset", "p3", "-tune", "hq",
        "-rc", "vbr", "-cq", "25", "-b:v", "0",
        "-spatial-aq", "1", "-temporal-aq", "1", "-pix_fmt", "yuv420p",
    ],
    QUALITY_FAST: [
        "-c:v", "h264_nvenc", "-preset", "p3", "-tune", "hq",
        "-rc", "vbr", "-cq", "25", "-b:v", "0", "-spatial-aq", "1",
        "-pix_fmt", "yuv420p",
    ],
    DELIVERY: [
        "-c:v", "h264_nvenc", "-preset", "p3",
        "-rc", "vbr", "-cq", "29", "-b:v", "0", "-spatial-aq", "1",
        "-pix_fmt", "yuv420p",
    ],
}

METADATA_SCRUB = [
    "-map_metadata", "-1", "-map_chapters", "-1",
    "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1",
]

LOUDNORM_FILTER = "loudnorm=I=-14:TP=-2.0:LRA=11"
AI_DISCLOSURE = "AI-generated content produced with LiveClipper"

_nvenc_tested = False
_nvenc_works = False
_nvenc_lock = threading.Lock()


def nvenc_available() -> bool:
    """Check if h264_nvenc is available and functional on this system."""
    global _nvenc_tested, _nvenc_works
    with _nvenc_lock:
        if _nvenc_tested:
            return _nvenc_works
        _nvenc_tested = True
        try:
            res = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
                    "-c:v", "h264_nvenc", "-f", "null", "-",
                ],
                capture_output=True,
                timeout=5.0,
            )
            _nvenc_works = (res.returncode == 0)
        except Exception:
            _nvenc_works = False
        return _nvenc_works


def video_encode_args(
    tier: str = QUALITY,
    crf: int | str | None = None,
    bitrate: str | None = None,
    preset: str | None = None,
) -> list[str]:
    """Return FFmpeg video encoder arguments honoring FFMPEG_ENCODER env setting."""
    mode = os.environ.get("FFMPEG_ENCODER", "x264").strip().lower()
    use_nvenc = False
    if mode in ("nvenc", "auto"):
        use_nvenc = nvenc_available()

    if use_nvenc:
        base = list(_NVENC_ARGS.get(tier, _NVENC_ARGS[QUALITY]))
        if bitrate:
            try:
                b_idx = base.index("-b:v")
                base[b_idx + 1] = str(bitrate)
            except ValueError:
                base.extend(["-b:v", str(bitrate)])
        return base

    base_x264 = list(_X264_ARGS.get(tier, _X264_ARGS[QUALITY]))
    if preset:
        try:
            p_idx = base_x264.index("-preset")
            base_x264[p_idx + 1] = str(preset)
        except ValueError:
            base_x264.extend(["-preset", str(preset)])
    if crf is not None:
        try:
            c_idx = base_x264.index("-crf")
            base_x264[c_idx + 1] = str(crf)
        except ValueError:
            base_x264.extend(["-crf", str(crf)])
    if bitrate:
        base_x264.extend(["-b:v", str(bitrate)])

    return base_x264


def audio_encode_args(normalize: bool = False, bitrate: str = "256k") -> list[str]:
    """Return standard audio encoding arguments."""
    ab = os.environ.get("YOUTUBE_UPLOAD_AUDIO_BITRATE", bitrate)
    args = ["-c:a", "aac", "-b:a", ab]
    if normalize:
        args.extend(["-af", LOUDNORM_FILTER])
    return args


def escape_filter_value(value: str | Path) -> str:
    r"""Escape a path or string for use inside a quoted FFmpeg filter argument.

    Handles Windows colon delimiters and single quotes.
    """
    s = str(value)
    return s.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def blurred_backdrop(out_w: int, out_h: int, sigma: float = 12.0) -> str:
    """Filter chain (without labels) generating a blurred background.

    Runs gblur on a quarter-sized frame and scales up for high performance.
    """
    small_w = max(2, out_w // 4 - (out_w // 4) % 2)
    small_h = max(2, out_h // 4 - (out_h // 4) % 2)
    return (
        f"scale=-2:{small_h},crop=w=min(iw\\,{small_w}):h={small_h},"
        f"gblur=sigma={sigma / 4:g},scale={out_w}:{out_h}"
    )


def probe_video_resolution(video_path: str | Path) -> tuple[int, int]:
    """Probe video (width, height) using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x",
        str(video_path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10.0, text=True).strip()
        parts = out.split("x")
        return int(parts[0]), int(parts[1])
    except Exception as exc:
        raise RuntimeError(f"Could not probe video resolution for {video_path}: {exc}") from exc


def probe_video_duration(video_path: str | Path) -> float:
    """Probe video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10.0, text=True).strip()
        return float(out)
    except Exception as exc:
        raise RuntimeError(f"Could not probe video duration for {video_path}: {exc}") from exc


def finished_before_crash(result: subprocess.CompletedProcess, outputs: Sequence[str | Path]) -> bool:
    """Check if FFmpeg completed writing all output files before exiting."""
    if result.returncode >= 0:
        return False
    stderr_bytes = result.stderr if isinstance(result.stderr, bytes) else str(result.stderr or "").encode()
    if stderr_bytes.count(b"muxing overhead") < len(outputs):
        return False
    return all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in outputs)
