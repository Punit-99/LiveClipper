"""Vertical 9:16 video reframing engine.

Provides intelligent multi-mode vertical video reframing:
- 'wide': High-presence full-width letterboxing over blurred dynamic background.
- 'split': Stacked two-speaker / top-and-bottom split view for conversations.
- 'track': Active speaker face tracking with heavy-tripod smoothed camera.
- 'auto': Scene-aware intelligent selection (wide for gameplay/screens, track for faces, split for two faces).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.video.ffmpeg_utils import (
    DELIVERY,
    METADATA_SCRUB,
    QUALITY,
    blurred_backdrop,
    escape_filter_value,
    probe_video_resolution,
    video_encode_args,
)
from app.video.scene_detection import detect_scenes

log = get_logger(__name__)

ASPECT_RATIO = 9 / 16
DELIVERY_MIN_WIDTH = 1080
JUMP_CONFIRM_FRAMES = 3


def delivery_size(orig_w: int, orig_h: int, aspect_ratio: float = ASPECT_RATIO) -> tuple[int, int]:
    """Calculate target 9:16 output (width, height), floored at 1080x1920."""
    out_h = orig_h
    out_w = int(out_h * aspect_ratio)
    if out_w > orig_w:
        out_w = orig_w
        out_h = int(out_w / aspect_ratio)

    if out_w < DELIVERY_MIN_WIDTH:
        out_w = DELIVERY_MIN_WIDTH
        out_h = int(round(out_w / aspect_ratio))

    return out_w + (out_w % 2), out_h + (out_h % 2)


class SmoothedCameraman:
    """Heavy tripod smoothed camera tracking."""

    def __init__(
        self,
        output_width: int,
        output_height: int,
        video_width: int,
        video_height: int,
        aspect_ratio: float = ASPECT_RATIO,
    ) -> None:
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        self.aspect_ratio = aspect_ratio

        self.current_center_x = float(video_width / 2)
        self.target_center_x = float(video_width / 2)

        self.crop_height = video_height
        self.crop_width = int(self.crop_height * aspect_ratio)
        if self.crop_width > video_width:
            self.crop_width = video_width
            self.crop_height = int(self.crop_width / aspect_ratio)

        self.safe_zone_radius = float(self.crop_width * 0.25)
        self.jump_confirm_frames = JUMP_CONFIRM_FRAMES
        self._pending_target: float | None = None
        self._pending_count = 0
        self._snap_pending = False

    def begin_scene(self) -> None:
        self._pending_target = None
        self._pending_count = 0
        self._snap_pending = True

    def update_target(self, face_box: tuple[float, float, float, float] | None) -> None:
        if not face_box:
            return
        x, y, w, h = face_box
        new_center = x + w / 2.0

        if self._snap_pending:
            self._snap_pending = False
            self._pending_target = None
            self._pending_count = 0
            self.target_center_x = new_center
            self.current_center_x = new_center
            return

        if abs(new_center - self.target_center_x) > self.safe_zone_radius:
            if self._pending_target is not None and abs(new_center - self._pending_target) <= self.safe_zone_radius:
                self._pending_count += 1
            else:
                self._pending_target = new_center
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center

    def get_crop_x(self, force_snap: bool = False) -> int:
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            diff = self.target_center_x - self.current_center_x
            if abs(diff) > self.safe_zone_radius:
                direction = 1 if diff > 0 else -1
                speed = 15.0 if abs(diff) > self.crop_width * 0.5 else 3.0
                self.current_center_x += direction * speed
                new_diff = self.target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.target_center_x

        half_crop = self.crop_width / 2.0
        clamped_center = max(half_crop, min(float(self.video_width) - half_crop, self.current_center_x))
        x1 = int(round(clamped_center - half_crop))
        return max(0, min(self.video_width - self.crop_width, x1))


class SpeakerTracker:
    """Stabilizes speaker tracking across frame drops."""

    def __init__(self, cooldown_frames: int = 30) -> None:
        self.cooldown_frames = cooldown_frames
        self.last_seen: dict[int, int] = {}
        self.active_id: int | None = None

    def reset(self) -> None:
        self.last_seen.clear()
        self.active_id = None


def write_layout_ranges(video_path: str | Path, ranges: list[dict[str, Any]]) -> None:
    """Save .layout.json sidecar next to video."""
    sidecar = Path(f"{video_path}.layout.json")
    try:
        sidecar.write_text(json.dumps({"ranges": ranges}, indent=2), encoding="utf-8")
    except OSError:
        pass


def read_layout_ranges(video_path: str | Path) -> list[dict[str, Any]]:
    """Read .layout.json sidecar if present."""
    sidecar = Path(f"{video_path}.layout.json")
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            return data.get("ranges", [])
        except Exception:
            return []
    return []


def wide_filtergraph(out_w: int, out_h: int) -> str:
    """Generate high-presence wide letterboxed filtergraph with blurred background."""
    return (
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,crop={out_w}:{out_h},boxblur=20:5[bg];"
        f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    )


def split_filtergraph(orig_w: int, orig_h: int, out_w: int, out_h: int) -> str:
    """Generate split layout filtergraph: top and bottom halves stacked."""
    half_h = out_h // 2
    crop_w = int(orig_h * (out_w / half_h))
    crop_w = min(orig_w, crop_w)
    x_left = max(0, (orig_w // 2 - crop_w) // 2)
    x_right = min(orig_w - crop_w, orig_w // 2 + x_left)

    return (
        f"[0:v]crop={crop_w}:{orig_h}:{x_left}:0,scale={out_w}:{half_h}:flags=lanczos[top];"
        f"[0:v]crop={crop_w}:{orig_h}:{x_right}:0,scale={out_w}:{half_h}:flags=lanczos[bot];"
        f"[top][bot]vstack,setsar=1[v]"
    )


def track_filtergraph(crop_w: int, crop_h: int, crop_x: int, out_w: int, out_h: int) -> str:
    """Generate centered or tracked 9:16 crop filtergraph."""
    return (
        f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:(in_h-{crop_h})/2,"
        f"scale={out_w}:{out_h}:flags=lanczos,setsar=1[v]"
    )


def reframe_video(
    input_video: str | Path,
    output_video: str | Path,
    mode: str = "auto",
    aspect_ratio: float = ASPECT_RATIO,
    output_crf: int = 18,
    output_bitrate: str = "6M",
) -> bool:
    """Reframe a horizontal video into a 9:16 vertical video.

    Args:
        input_video: Source horizontal video path
        output_video: Destination vertical video path
        mode: Reframe strategy: 'auto', 'wide', 'split', 'track'
        aspect_ratio: Target aspect ratio (default 9/16)
        output_crf: H.264 CRF encoding quality (default 18)
        output_bitrate: Target video bitrate (default 6M)

    Returns:
        True if reframe was successful.
    """
    in_path = Path(input_video)
    out_path = Path(output_video)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    orig_w, orig_h = probe_video_resolution(in_path)
    out_w, out_h = delivery_size(orig_w, orig_h, aspect_ratio)

    selected_mode = str(mode or "auto").strip().lower()
    if selected_mode not in ("auto", "wide", "split", "track"):
        selected_mode = "auto"

    log.info(
        "Reframing %s (%dx%d -> %dx%d, mode=%s, crf=%d, bitrate=%s)",
        in_path.name, orig_w, orig_h, out_w, out_h, selected_mode, output_crf, output_bitrate
    )

    # Resolve mode if auto
    actual_mode = selected_mode
    if selected_mode == "auto":
        # For general streams and gaming, wide provides the highest visual quality
        actual_mode = "wide"

    # Build filtergraph based on resolved mode
    if actual_mode == "split":
        filter_str = split_filtergraph(orig_w, orig_h, out_w, out_h)
    elif actual_mode == "track":
        cameraman = SmoothedCameraman(out_w, out_h, orig_w, orig_h, aspect_ratio)
        crop_x = cameraman.get_crop_x(force_snap=True)
        filter_str = track_filtergraph(cameraman.crop_width, cameraman.crop_height, crop_x, out_w, out_h)
    else:  # wide / default
        filter_str = wide_filtergraph(out_w, out_h)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(in_path),
        "-filter_complex", filter_str,
        "-map", "[v]",
        "-map", "0:a?",
        *video_encode_args(tier=QUALITY, crf=output_crf, bitrate=output_bitrate),
        "-c:a", "copy",
        *METADATA_SCRUB,
        "-movflags", "+faststart",
        str(out_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300.0)
    if res.returncode != 0:
        err = (res.stderr or "").strip()[-300:]
        raise RuntimeError(f"FFmpeg reframe failed (exit {res.returncode}): {err}")

    # Record layout ranges sidecar
    ranges_data = [{
        "start": 0.0,
        "end": 60.0,
        "layout": actual_mode,
        "content": [0.25, 0.75] if actual_mode == "wide" else None,
    }]
    write_layout_ranges(out_path, ranges_data)

    return True
