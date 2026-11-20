"""Hook banner text overlay generator and video compositor.

Renders viral hook banners (Pill, Banner, Minimal) and burns them
into the top-third / safe zone of vertical videos with optional subtitle chaining.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.shorts.text_overlay import create_hook_banner_bmp
from app.video.ffmpeg_utils import (
    METADATA_SCRUB,
    QUALITY,
    video_encode_args,
)

log = get_logger(__name__)

HOOK_STYLES = ("pill", "banner", "minimal", "bold")


def create_hook_banner(
    text: str,
    output_path: str | Path,
    width: int = 960,
    height: int = 200,
    style: str = "pill",
) -> Path:
    """Generate a hook banner image.

    Prefers Pillow with rounded corners/gradient if available; falls back to
    pure-Python BMP generator.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    try:
        from PIL import Image, ImageDraw, ImageFont

        # PIL rendering with rounded pill or banner
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        clean_text = text.strip()

        # Find font
        font_path = Path(__file__).resolve().parent / "fonts" / "Montserrat-ExtraBold.ttf"
        font_size = 46
        try:
            if font_path.is_file():
                font = ImageFont.truetype(str(font_path), font_size)
            else:
                font = ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        pad_x = 40
        pad_y = 24
        bbox = draw.textbbox((0, 0), clean_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        pill_w = min(width - 40, text_w + pad_x * 2)
        pill_h = min(height - 20, text_h + pad_y * 2)
        pill_x = (width - pill_w) // 2
        pill_y = (height - pill_h) // 2

        # No background box/pill - clean white text with subtle drop shadow
        t_x = (width - text_w) // 2 - bbox[0]
        t_y = (height - text_h) // 2 - bbox[1]

        # Draw subtle drop shadow
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3), (3, 0), (2, 2)):
            draw.text((t_x + dx, t_y + dy), clean_text, font=font, fill=(0, 0, 0, 220))

        # Center text in clean white
        draw.text((t_x, t_y), clean_text, font=font, fill=(255, 255, 255, 255))

        png_path = out.with_suffix(".png")
        img.save(png_path, "PNG")
        return png_path
    except Exception as exc:
        log.debug("PIL rendering skipped (%s); falling back to BMP banner", exc)
        bmp_path = out.with_suffix(".bmp")
        create_hook_banner_bmp(
            text=text,
            output_path=bmp_path,
            width=width,
            height=height,
            scale=4,
        )
        return bmp_path


def add_hook_to_video(
    clip_path: str | Path,
    text: str,
    out_path: str | Path,
    duration: float = 4.0,
    style: str = "pill",
    position_y: int = 220,
    also: tuple[str, str | Path] | None = None,
    output_crf: int = 18,
    output_bitrate: str = "6M",
) -> tuple[Path, dict[str, Any]]:
    """Burn hook text overlay into video.

    Args:
        clip_path: Input vertical video
        text: Hook text
        out_path: Output video destination
        duration: Seconds hook banner remains visible
        style: Hook style preset (pill, banner, minimal, bold)
        position_y: Vertical pixel offset from top of 1920-height frame
        also: Optional (subtitles_vf, final_captioned_path) to chain subtitle burning

    Returns:
        (rendered_video_path, metadata)
    """
    in_video = Path(clip_path)
    target_out = Path(out_path)
    target_out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="hook_") as tmpdir:
        banner_path = Path(tmpdir) / f"hook_{in_video.stem}.png"
        created_banner = create_hook_banner(text, banner_path, width=960, height=200, style=style)

        # Build filtergraph
        # [0:v][1:v]overlay=(W-w)/2:220:enable='between(t,0,4)'[v_out]
        overlay_filter = f"[0:v][1:v]overlay=(W-w)/2:{position_y}:enable='between(t,0,{duration})'"

        dest_file = target_out
        if also:
            sub_vf, captioned_dest = also
            # Chain subtitles right after hook overlay
            filter_complex = f"{overlay_filter},{sub_vf}[v]"
            dest_file = Path(captioned_dest)
            map_out = "[v]"
        else:
            filter_complex = f"{overlay_filter}[v]"
            map_out = "[v]"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(in_video),
            "-i", str(created_banner),
            "-filter_complex", filter_complex,
            "-map", map_out,
            "-map", "0:a?",
            *video_encode_args(tier=QUALITY, crf=output_crf, bitrate=output_bitrate),
            "-c:a", "copy",
            *METADATA_SCRUB,
            "-movflags", "+faststart",
            str(dest_file),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300.0)
        if res.returncode != 0:
            err = (res.stderr or "").strip()[-300:]
            raise RuntimeError(f"FFmpeg hook overlay failed (code {res.returncode}): {err}")

        config: dict[str, Any] = {
            "text": text,
            "style": style,
            "duration": duration,
            "y": position_y,
        }
        if also:
            config["_captioned"] = str(dest_file)

        return dest_file, config
