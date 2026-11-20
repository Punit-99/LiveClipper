"""YouTube video and metadata downloader using yt-dlp."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from app.clipper.models import VideoMetadata

log = logging.getLogger(__name__)

YOUTUBE_URL_REGEX = re.compile(
    r"(?:https?:\/\/)?(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|v\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})",
    re.IGNORECASE,
)


def extract_youtube_id(url: str) -> str | None:
    """Extract 11-character YouTube video ID from supported URL patterns or bare ID."""
    if not url:
        return None
    clean = url.strip()
    if len(clean) == 11 and re.match(r"^[\w-]{11}$", clean):
        return clean
    match = YOUTUBE_URL_REGEX.search(clean)
    if match:
        return match.group(1)
    return None


def is_valid_youtube_url(url: str) -> bool:
    """Check if given string is a valid YouTube URL."""
    return extract_youtube_id(url) is not None


def fetch_video_info(url: str) -> VideoMetadata:
    """Retrieve metadata for a YouTube URL without downloading media."""
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    ydl_opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            log.error("Failed to fetch YouTube info for %s: %s", url, exc)
            raise RuntimeError(f"Could not retrieve YouTube video info: {exc}") from exc

    if not info:
        raise ValueError(f"No video information returned for: {url}")

    chapters = info.get("chapters") or []
    heatmap = info.get("heatmap") or []

    # Fallback to thumbnail URL
    thumbnail_url = info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"

    return VideoMetadata(
        url=url,
        video_id=video_id,
        title=info.get("title") or f"YouTube Video {video_id}",
        channel=info.get("uploader") or info.get("channel") or "Unknown Channel",
        duration=float(info.get("duration") or 0.0),
        description=info.get("description") or "",
        view_count=int(info.get("view_count") or 0),
        like_count=int(info.get("like_count") or 0),
        thumbnail_url=thumbnail_url,
        chapters=chapters,
        heatmap=heatmap,
    )


def download_video(
    url: str,
    output_dir: Path,
    *,
    max_quality: int = 1080,
    progress_cb: Callable[[float, str], None] | None = None,
) -> tuple[Path, Path | None]:
    """Download best available MP4 video (up to max_quality) and subtitles.

    Returns:
        `(source_video_path, subtitles_path_or_None)`
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    video_id = extract_youtube_id(url) or "source"
    output_template = str(output_dir / "source.%(ext)s")

    def _progress_hook(d: dict[str, Any]) -> None:
        if not progress_cb:
            return
        status = d.get("status")
        if status == "downloading":
            total_bytes = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total_bytes > 0:
                pct = round((downloaded / total_bytes) * 100.0, 1)
                progress_cb(pct, f"Downloading video... {pct}%")
            else:
                progress_cb(10.0, "Downloading video stream...")
        elif status == "finished":
            progress_cb(100.0, "Download complete. Merging streams...")

    ydl_opts: dict[str, Any] = {
        "format": f"bestvideo[height<={max_quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={max_quality}]+bestaudio/best[height<={max_quality}]/best",
        "outtmpl": output_template,
        "merge_output_format": "mp4",
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-orig"],
        "subtitlesformat": "vtt/srt/best",
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_progress_hook],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        log.error("yt-dlp download failed for %s: %s", url, exc)
        raise RuntimeError(f"Failed to download video: {exc}") from exc

    source_mp4 = output_dir / "source.mp4"
    if not source_mp4.is_file():
        # Check if downloaded with different container and remux
        candidates = list(output_dir.glob("source.*"))
        video_candidates = [c for c in candidates if c.suffix.lower() in (".mp4", ".mkv", ".webm")]
        if video_candidates:
            source_mp4 = video_candidates[0]
        else:
            raise FileNotFoundError(f"Source video file not found in {output_dir}")

    # Locate subtitle file if available
    sub_candidates = list(output_dir.glob("source*.vtt")) + list(output_dir.glob("source*.srt"))
    subtitle_path: Path | None = sub_candidates[0] if sub_candidates else None

    return source_mp4, subtitle_path
