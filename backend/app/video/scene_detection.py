"""Scene detection for video processing.

Detects shot boundaries to allow per-scene framing decisions.
Gracefully falls back to a single continuous scene if PySceneDetect
is unavailable or if detection fails.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.logging_setup import get_logger
from app.video.ffmpeg_utils import probe_video_duration

log = get_logger(__name__)


def probe_fps(video_path: str | Path) -> float:
    """Probe video frames per second using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=10.0, text=True).strip()
        if "/" in out:
            num, den = out.split("/")
            return float(num) / float(den) if float(den) > 0 else 30.0
        return float(out) if out else 30.0
    except Exception:
        return 30.0


def detect_scenes(video_path: str | Path) -> tuple[list[tuple[int, int]], float]:
    """Detect scenes in video.

    Returns:
        (scene_list, fps) where scene_list is a list of (start_frame, end_frame) bounds.
    """
    fps = probe_fps(video_path)
    path_str = str(video_path)

    # Attempt PySceneDetect ContentDetector
    try:
        from scenedetect import ContentDetector, SceneManager, open_video

        video = open_video(path_str)
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=27.0))
        sm.detect_scenes(video=video)
        raw_scenes = sm.get_scene_list()
        fps = float(video.frame_rate) if video.frame_rate else fps

        if raw_scenes:
            scenes = [(s.get_frames(), e.get_frames()) for s, e in raw_scenes]
            log.info("Detected %d scene(s) via PySceneDetect in %s", len(scenes), Path(video_path).name)
            return scenes, fps
    except Exception as exc:
        log.debug("PySceneDetect not available or detection error (%s); using full video", exc)

    # Fallback: Treat whole video as one scene
    try:
        duration = probe_video_duration(video_path)
        total_frames = max(1, int(round(duration * fps)))
    except Exception:
        total_frames = int(30 * 60)

    log.debug("Using single scene [(0, %d)] at %.2f fps", total_frames, fps)
    return [(0, total_frames)], fps
