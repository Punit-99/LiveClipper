"""YouTube video and metadata downloader using yt-dlp."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yt_dlp

from app.clipper.models import VideoMetadata

log = logging.getLogger(__name__)

YOUTUBE_URL_REGEX = re.compile(
    r"(?:https?:\/\/)?(?:www\.|m\.|music\.|gaming\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|v\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})",
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

    try:
        parsed = urlparse(clean if "://" in clean else f"https://{clean}")
        if "youtube" in parsed.netloc:
            qs = parse_qs(parsed.query)
            if "v" in qs and qs["v"]:
                cand = qs["v"][0]
                if len(cand) == 11 and re.match(r"^[\w-]{11}$", cand):
                    return cand
        elif "youtu.be" in parsed.netloc:
            cand = parsed.path.lstrip("/").split("/")[0]
            if len(cand) == 11 and re.match(r"^[\w-]{11}$", cand):
                return cand
    except Exception:
        pass
    return None


def is_valid_youtube_url(url: str) -> bool:
    """Check if given string is a valid YouTube URL."""
    return extract_youtube_id(url) is not None


def parse_timestamp_seconds(val: str | float | int | None) -> float | None:
    """Parse time string like 'HH:MM:SS', 'MM:SS', '2851s', '47m31s', '1h20m', or numeric seconds to float seconds."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return max(0.0, float(val))
    val_str = str(val).strip()
    if not val_str:
        return None

    # Handle pure numeric or trailing 's' like '2851s' or '2851'
    clean_numeric = val_str.rstrip("sS").strip()
    try:
        return max(0.0, float(clean_numeric))
    except ValueError:
        pass

    # Handle YouTube style '1h30m20s' or '47m31s'
    hms_match = re.match(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$", val_str, re.IGNORECASE)
    if hms_match and any(hms_match.groups()):
        h = float(hms_match.group(1) or 0)
        m = float(hms_match.group(2) or 0)
        s = float(hms_match.group(3) or 0)
        return max(0.0, h * 3600.0 + m * 60.0 + s)

    # Handle 'HH:MM:SS' or 'MM:SS'
    parts = val_str.split(":")
    try:
        parts_f = [float(p) for p in parts]
        if len(parts_f) == 3:  # HH:MM:SS
            return parts_f[0] * 3600.0 + parts_f[1] * 60.0 + parts_f[2]
        elif len(parts_f) == 2:  # MM:SS
            return parts_f[0] * 60.0 + parts_f[1]
        elif len(parts_f) == 1:
            return parts_f[0]
    except Exception:
        pass
    return None


def extract_timestamp_from_url(url: str) -> float | None:
    """Extract start timestamp in seconds from URL query parameters (e.g. ?t=2851s or &t=120)."""
    try:
        parsed = urlparse(url.strip() if "://" in url else f"https://{url.strip()}")
        qs = parse_qs(parsed.query)
        if "t" in qs and qs["t"]:
            return parse_timestamp_seconds(qs["t"][0])
        if "start" in qs and qs["start"]:
            return parse_timestamp_seconds(qs["start"][0])
    except Exception:
        pass
    return None


def classify_youtube_error(exc: Exception) -> str:
    """Map yt-dlp exceptions to user-friendly diagnostic messages."""
    msg = str(exc).lower()
    if "private video" in msg:
        return "This video is private and cannot be downloaded."
    if "sign in to confirm your age" in msg or "age-restricted" in msg or "confirm your age" in msg:
        return "This video is age-restricted and requires YouTube sign-in."
    if "video unavailable" in msg or "has been removed" in msg or "deleted" in msg or "not available" in msg:
        return "This video is unavailable or has been deleted from YouTube."
    if "not made this video available in your country" in msg or "region" in msg or "blocked in your country" in msg:
        return "This video is region-restricted and not available in your location."
    if "too many requests" in msg or "429" in msg:
        return "YouTube rate limit exceeded (HTTP 429). Please wait a few minutes before trying again."
    if "sign in to confirm you're not a bot" in msg or "bot" in msg:
        return "YouTube requires bot verification for this video."
    if "live event will begin" in msg or "upcoming" in msg:
        return "This video is an upcoming live stream that has not started yet."
    if "members-only" in msg or "join this channel" in msg:
        return "This video is for channel members only."
    return f"Failed to access or download YouTube video: {exc}"


def _get_cookiefile() -> str | None:
    """Find optional YouTube cookies file if configured or present."""
    cookie_candidates = [
        os.environ.get("YOUTUBE_COOKIES_PATH"),
        "youtube_cookies.txt",
        "cookies.txt",
        ".youtube_cookies.txt",
        "/app/youtube_cookies.txt",
        "/app/cookies.txt",
    ]
    for cand in cookie_candidates:
        if cand and Path(cand).is_file() and Path(cand).stat().st_size > 0:
            return str(Path(cand).resolve())
    return None


def _get_js_runtime_opts() -> dict[str, Any]:
    """Configure JS runtime for yt-dlp to solve modern YouTube JS challenges."""
    opts: dict[str, Any] = {}
    node_path = shutil.which("node") or shutil.which("nodejs")
    if node_path:
        opts["js_runtimes"] = {"node": {"path": node_path}}
        opts["remote_components"] = ["ejs:github"]
    return opts


def _ensure_mp4(input_path: Path, target_path: Path) -> Path:
    """Remux non-mp4 video container (webm/mkv) into standard MP4 for browser playback."""
    if input_path.resolve() == target_path.resolve() and target_path.is_file():
        return target_path
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(target_path)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        if target_path.is_file() and target_path.stat().st_size > 0:
            try:
                input_path.unlink()
            except Exception:
                pass
            return target_path
    except Exception as exc:
        log.warning("Fast remux of %s to mp4 failed (%s); trying transcoding...", input_path.name, exc)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path), "-c:v", "libx264", "-c:a", "aac", str(target_path)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        if target_path.is_file() and target_path.stat().st_size > 0:
            try:
                input_path.unlink()
            except Exception:
                pass
            return target_path
    except Exception as exc:
        log.warning("Transcode of %s to mp4 failed: %s", input_path.name, exc)
    return input_path


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
        "noplaylist": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        **_get_js_runtime_opts(),
    }
    cookiefile = _get_cookiefile()
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:
            clean_err = classify_youtube_error(exc)
            log.error("Failed to fetch YouTube info for %s: %s", url, exc)
            raise RuntimeError(clean_err) from exc

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
    start_time: str | float | int | None = None,
    end_time: str | float | int | None = None,
    progress_cb: Callable[[float, str], None] | None = None,
) -> tuple[Path, Path | None]:
    """Download best available MP4 video (up to max_quality) and subtitles.

    Supports optional time range slicing with `start_time` and `end_time`.

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
        "subtitleslangs": ["en.*", "en", "en-US", "en-orig", "en-GB", "all"],
        "subtitlesformat": "vtt/srt/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "progress_hooks": [_progress_hook],
        **_get_js_runtime_opts(),
    }
    cookiefile = _get_cookiefile()
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    start_sec = parse_timestamp_seconds(start_time)
    if start_sec is None:
        start_sec = extract_timestamp_from_url(url)
    end_sec = parse_timestamp_seconds(end_time)
    if start_sec is not None or end_sec is not None:
        s = start_sec if start_sec is not None else 0.0
        e = end_sec if end_sec is not None else float("inf")
        if hasattr(yt_dlp.utils, "download_range_func"):
            ydl_opts["download_ranges"] = yt_dlp.utils.download_range_func([], [[s, e]])
            ydl_opts["force_keyframes_at_cuts"] = True

    def _find_video_file() -> Path | None:
        target = output_dir / "source.mp4"
        if target.is_file() and target.stat().st_size > 0:
            return target
        candidates = list(output_dir.glob("source.*"))
        video_candidates = [
            c for c in candidates if c.suffix.lower() in (".mp4", ".mkv", ".webm") and c.stat().st_size > 0
        ]
        return video_candidates[0] if video_candidates else None

    # Step 1: Attempt download with subtitles
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as exc:
        err_msg = classify_youtube_error(exc)
        log.warning("yt-dlp initial download failed (%s); checking video or retrying without subtitles...", exc)
        # Check if video was downloaded despite the subtitle error (common on 429 subtitle rate limit)
        existing_video = _find_video_file()
        if not existing_video:
            lower_exc = str(exc).lower()
            if any(k in lower_exc for k in ("private", "unavailable", "removed", "age-restricted", "confirm your age", "not available in your country")):
                raise RuntimeError(err_msg) from exc

            if progress_cb:
                progress_cb(12.0, "Subtitles unavailable; downloading video stream...")
            ydl_opts_nosub = dict(ydl_opts)
            ydl_opts_nosub["writesubtitles"] = False
            ydl_opts_nosub["writeautomaticsub"] = False
            try:
                with yt_dlp.YoutubeDL(ydl_opts_nosub) as ydl_nosub:
                    ydl_nosub.download([url])
            except Exception as vid_exc:
                clean_err = classify_youtube_error(vid_exc)
                log.error("yt-dlp video download without subtitles also failed: %s", vid_exc)
                raise RuntimeError(clean_err) from vid_exc

    source_file = _find_video_file()
    if not source_file or not source_file.is_file():
        raise FileNotFoundError(f"Source video file not found in {output_dir}")

    # Ensure output is standard MP4
    if source_file.suffix.lower() != ".mp4":
        source_mp4 = _ensure_mp4(source_file, output_dir / "source.mp4")
    else:
        source_mp4 = source_file

    # Locate subtitle file if available
    sub_candidates = list(output_dir.glob("source*.vtt")) + list(output_dir.glob("source*.srt"))
    subtitle_path: Path | None = sub_candidates[0] if sub_candidates else None
    if not subtitle_path:
        log.warning("Subtitles unavailable for %s (will use Whisper transcription instead)", url)

    return source_mp4, subtitle_path

