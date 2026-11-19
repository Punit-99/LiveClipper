"""Animated subtitle generation and burning for vertical video.

Generates word-level karaoke ASS subtitles and burns them via FFmpeg / libass.
Supports modern social presets (Hormozi, Pill, Bold, Clean, etc.).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.video.ffmpeg_utils import (
    DELIVERY,
    METADATA_SCRUB,
    QUALITY,
    escape_filter_value,
    video_encode_args,
)

log = get_logger(__name__)

_HEX_COLOR_RE = re.compile(r"^[0-9a-fA-F]{6}$")
SAFE_MARGIN_V = 43

# Font directory pointing to bundled fonts
_FONT_DIR = Path(__file__).resolve().parent / "fonts"

AUTO_CAPTION_STYLE: dict[str, Any] = {
    "style": "karaoke",
    "alignment": "bottom",
    "font_name": "Anton",
    "font_size": 40,
    "font_color": "#FFFFFF",
    "highlight_color": "#FFE500",
    "border_color": "#000000",
    "border_width": 2,
    "effect": "pop",
    "base_opacity": 1.0,
    "uppercase": True,
    "max_chars": 18,
    "max_duration": 1.4,
}

_PRESET_BASE: dict[str, Any] = {
    "style": "karaoke",
    "font_color": "#FFFFFF",
    "bg_opacity": 0.0,
    "base_opacity": 1.0,
    "reveal": False,
    "shadow": 0,
    "max_duration": 1.4,
}

CAPTION_PRESETS: dict[str, dict[str, Any]] = {
    "default": {
        **_PRESET_BASE,
        "font_name": "Anton",
        "font_size": 40,
        "highlight_color": "#FFE500",
        "border_width": 2,
        "effect": "pop",
        "uppercase": True,
        "max_chars": 18,
    },
    "bold": {
        **_PRESET_BASE,
        "font_name": "Anton",
        "font_size": 40,
        "highlight_color": "#FFE500",
        "border_width": 2,
        "effect": "pop",
        "uppercase": True,
        "max_chars": 18,
    },
    "hormozi": {
        **_PRESET_BASE,
        "font_name": "Montserrat ExtraBold",
        "font_size": 44,
        "highlight_color": "#FFE500",
        "border_width": 4,
        "shadow": 2,
        "effect": "pop",
        "uppercase": True,
        "reveal": True,
        "max_chars": 9,
    },
    "pill": {
        **_PRESET_BASE,
        "font_name": "Montserrat ExtraBold",
        "font_size": 44,
        "highlight_color": "#7C3AED",
        "border_width": 3,
        "effect": "highlight",
        "uppercase": True,
        "max_chars": 9,
    },
    "lime": {
        **_PRESET_BASE,
        "font_name": "Montserrat ExtraBold",
        "font_size": 44,
        "highlight_color": "#A3FF12",
        "border_width": 3,
        "effect": "highlight",
        "uppercase": True,
        "max_chars": 9,
    },
    "oneword": {
        **_PRESET_BASE,
        "font_name": "Anton",
        "font_size": 70,
        "highlight_color": "#FFFFFF",
        "border_width": 5,
        "effect": "pop",
        "uppercase": True,
        "max_chars": 1,
    },
    "cine": {
        **_PRESET_BASE,
        "font_name": "Montserrat ExtraBold",
        "font_size": 36,
        "highlight_color": "#FF5A1F",
        "border_width": 0,
        "shadow": 2,
        "effect": "none",
        "uppercase": False,
        "base_opacity": 0.8,
        "max_chars": 14,
    },
    "clean": {
        **_PRESET_BASE,
        "font_name": "Montserrat ExtraBold",
        "font_size": 34,
        "highlight_color": "#FFFFFF",
        "border_width": 0,
        "shadow": 2,
        "effect": "none",
        "uppercase": False,
        "base_opacity": 0.7,
        "max_chars": 12,
    },
    "none": {
        "style": "none",
    },
}


def merge_continuation_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge faster-whisper continuation fragments into their base word."""
    merged: list[dict[str, Any]] = []
    for word in words:
        text = word.get("word", "")
        if merged and isinstance(text, str) and text and not text.startswith(" "):
            prev = merged[-1]
            prev["word"] = f"{prev.get('word', '')}{text}"
            if word.get("end") is not None:
                prev["end"] = word["end"]
        else:
            merged.append(dict(word))
    return merged


def _clamp(val: float, low: float, high: float, default: float) -> float:
    try:
        f = float(val)
        return max(low, min(high, f))
    except (ValueError, TypeError):
        return default


def _format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp H:MM:SS.cc."""
    sec = max(0.0, float(seconds))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    c = int(round((sec - int(sec)) * 100))
    if c >= 100:
        c = 99
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _format_srt_time(seconds: float) -> str:
    """Format seconds into SRT timestamp HH:MM:SS,mmm."""
    sec = max(0.0, float(seconds))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        ms = 999
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _hex_to_ass_color(hex_color: str, opacity: float = 1.0, fallback: str = "FFFFFF") -> str:
    """Convert #RRGGBB into &HAABBGGRR&."""
    digits = str(hex_color or "").lstrip("#")
    if not _HEX_COLOR_RE.match(digits):
        digits = fallback
    r = digits[0:2]
    g = digits[2:4]
    b = digits[4:6]
    alpha = int(round((1.0 - _clamp(opacity, 0.0, 1.0, 1.0)) * 255))
    return f"&H{alpha:02X}{b}{g}{r}&".upper()


def _hex_to_inline_c(hex_color: str, fallback: str = "FFFFFF") -> str:
    """Convert #RRGGBB into &HBBGGRR& for inline \\c tags."""
    digits = str(hex_color or "").lstrip("#")
    if not _HEX_COLOR_RE.match(digits):
        digits = fallback
    r = digits[0:2]
    g = digits[2:4]
    b = digits[4:6]
    return f"&H{b}{g}{r}&".upper()


def _dim_color(hex_color: str, opacity: float) -> str:
    digits = str(hex_color or "").lstrip("#")
    if not _HEX_COLOR_RE.match(digits):
        digits = "FFFFFF"
    factor = 0.5 + 0.5 * _clamp(opacity, 0.05, 1.0, 1.0)
    r = min(255, round(int(digits[0:2], 16) * factor))
    g = min(255, round(int(digits[2:4], 16) * factor))
    b = min(255, round(int(digits[4:6], 16) * factor))
    return f"{r:02X}{g:02X}{b:02X}"


def _escape_ass(text: str) -> str:
    return str(text).replace("\\", "/").replace("{", "(").replace("}", ")").replace("\r", " ").replace("\n", " ")


def _collect_blocks(
    transcript: dict[str, Any],
    clip_start: float,
    clip_end: float,
    max_chars: int = 18,
    max_duration: float = 1.4,
) -> list[list[dict[str, Any]]]:
    flat_words: list[dict[str, Any]] = []
    for segment in transcript.get("segments", []):
        flat_words.extend(segment.get("words", []))
    flat_words = merge_continuation_words(flat_words)

    words: list[dict[str, Any]] = []
    for w in flat_words:
        w_start = float(w.get("start", 0.0))
        w_end = float(w.get("end", 0.0))
        if w_end > clip_start and w_start < clip_end:
            txt = " ".join(str(w.get("word", "")).split())
            if txt:
                words.append({
                    "word": txt,
                    "start": max(0.0, w_start - clip_start),
                    "end": max(0.0, w_end - clip_start),
                })

    blocks: list[list[dict[str, Any]]] = []
    curr: list[dict[str, Any]] = []
    block_start: float | None = None

    for w in words:
        if not curr:
            curr = [w]
            block_start = w["start"]
            continue

        curr_len = sum(len(x["word"]) + 1 for x in curr)
        dur = w["end"] - (block_start or 0.0)
        if curr_len + len(w["word"]) > max_chars or dur > max_duration:
            blocks.append(curr)
            curr = [w]
            block_start = w["start"]
        else:
            curr.append(w)

    if curr:
        blocks.append(curr)
    return blocks


def generate_ass(
    transcript: dict[str, Any],
    clip_start: float,
    clip_end: float,
    output_path: str | Path,
    max_chars: int = 18,
    max_duration: float = 1.4,
    alignment: str = "bottom",
    fontsize: int = 40,
    font_name: str = "Anton",
    font_color: str = "#FFFFFF",
    border_color: str = "#000000",
    border_width: int = 2,
    highlight_color: str = "#FFE500",
    effect: str = "pop",
    base_opacity: float = 1.0,
    uppercase: bool = True,
    margin_v: int = SAFE_MARGIN_V,
    split_ranges: list[tuple[float, float]] | None = None,
    reveal: bool = False,
    shadow: int = 0,
) -> bool:
    """Generate modern karaoke ASS subtitles from word timestamps."""
    blocks = _collect_blocks(transcript, clip_start, clip_end, max_chars, max_duration)
    if not blocks:
        return False

    final_font_size = max(10, int(_clamp(fontsize, 10, 200, 40) * 0.85))
    align_val = 8 if alignment == "top" else (5 if alignment == "middle" else 2)

    primary = _hex_to_ass_color(_dim_color(font_color, base_opacity))
    secondary = _hex_to_ass_color(highlight_color)
    outline = _hex_to_ass_color(border_color)
    back = "&H80000000&" if shadow else "&H00000000&"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 162
PlayResY: 288
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{final_font_size},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,0,0,1,{border_width},{shadow},{align_val},10,10,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    hl_tag = _hex_to_inline_c(highlight_color)
    base_tag = _hex_to_inline_c(_dim_color(font_color, base_opacity))
    seams = [(float(a), float(b)) for a, b in (split_ranges or [])]

    dialogues: list[str] = []

    for block in blocks:
        for active_idx, target_word in enumerate(block):
            t_start = target_word["start"]
            t_end = target_word["end"]
            start_str = _format_ass_time(t_start)
            end_str = _format_ass_time(t_end)

            seam_prefix = "{\\an5}" if any(sa <= t_start < sb for sa, sb in seams) else ""

            rendered_tokens: list[str] = []
            for idx, w in enumerate(block):
                text_clean = _escape_ass(w["word"].upper() if uppercase else w["word"])
                if idx == active_idx:
                    if effect == "pop":
                        rendered_tokens.append(f"{{\\c{hl_tag}\\fscx110\\fscy110}}{text_clean}{{\\fscx100\\fscy100\\c{base_tag}}}")
                    elif effect == "highlight":
                        rendered_tokens.append(f"{{\\c{hl_tag}\\bord{border_width + 1}}}{text_clean}{{\\bord{border_width}\\c{base_tag}}}")
                    else:
                        rendered_tokens.append(f"{{\\c{hl_tag}}}{text_clean}{{\\c{base_tag}}}")
                else:
                    if reveal and idx > active_idx:
                        rendered_tokens.append(f"{{\\alpha&HFF&}}{text_clean}{{\\alpha&H00&}}")
                    else:
                        rendered_tokens.append(text_clean)

            line_text = f"{seam_prefix}{' '.join(rendered_tokens)}"
            dialogues.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line_text}")

    full_ass = header + "\n".join(dialogues) + "\n"
    Path(output_path).write_text(full_ass, encoding="utf-8-sig")
    return True


def generate_srt(
    transcript: dict[str, Any],
    clip_start: float,
    clip_end: float,
    output_path: str | Path,
    max_chars: int = 18,
    max_duration: float = 1.4,
) -> bool:
    """Generate standard SRT subtitles."""
    blocks = _collect_blocks(transcript, clip_start, clip_end, max_chars, max_duration)
    if not blocks:
        return False

    lines: list[str] = []
    for idx, block in enumerate(blocks, 1):
        s_time = _format_srt_time(block[0]["start"])
        e_time = _format_srt_time(block[-1]["end"])
        txt = " ".join(w["word"] for w in block)
        lines.append(f"{idx}\n{s_time} --> {e_time}\n{txt}\n")

    Path(output_path).write_text("\n".join(lines), encoding="utf-8-sig")
    return True


def subtitles_filter(ass_path: str | Path, fonts_dir: str | Path | None = None) -> str:
    """Return the FFmpeg -vf ass=... filter string with escaped font paths."""
    escaped_ass = escape_filter_value(ass_path)
    f_dir = Path(fonts_dir) if fonts_dir else _FONT_DIR
    if f_dir.is_dir():
        escaped_fonts = escape_filter_value(f_dir)
        return f"ass=filename='{escaped_ass}':fontsdir='{escaped_fonts}'"
    return f"ass=filename='{escaped_ass}'"


def burn_subtitles(
    clip_path: str | Path,
    ass_path: str | Path,
    out_path: str | Path,
    fonts_dir: str | Path | None = None,
    output_crf: int = 18,
    output_bitrate: str = "6M",
) -> None:
    """Burn ASS subtitles into video using native FFmpeg libass."""
    sub_filter = subtitles_filter(ass_path, fonts_dir)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(clip_path),
        "-vf", sub_filter,
        *video_encode_args(tier=QUALITY, crf=output_crf, bitrate=output_bitrate),
        "-c:a", "copy",
        *METADATA_SCRUB,
        "-movflags", "+faststart",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=300.0)
    if res.returncode != 0:
        err = (res.stderr or "").strip()[-300:]
        raise RuntimeError(f"FFmpeg subtitle burn failed (code {res.returncode}): {err}")
