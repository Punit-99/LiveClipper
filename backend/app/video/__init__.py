"""LiveClipper Video Processing Package.

Self-contained vertical 9:16 video reframing, faster-whisper transcription,
word-level ASS/SRT animated subtitles, and viral hook banner overlays.
"""

from __future__ import annotations

from app.video.ffmpeg_utils import (
    DELIVERY,
    METADATA_SCRUB,
    QUALITY,
    QUALITY_FAST,
    blurred_backdrop,
    escape_filter_value,
    nvenc_available,
    probe_video_duration,
    probe_video_resolution,
    video_encode_args,
)
from app.video.hooks import HOOK_STYLES, add_hook_to_video, create_hook_banner
from app.video.reframe import (
    ASPECT_RATIO,
    SmoothedCameraman,
    SpeakerTracker,
    delivery_size,
    read_layout_ranges,
    reframe_video,
    write_layout_ranges,
)
from app.video.scene_detection import detect_scenes
from app.video.subtitles import (
    AUTO_CAPTION_STYLE,
    CAPTION_PRESETS,
    burn_subtitles,
    generate_ass,
    generate_srt,
    merge_continuation_words,
    subtitles_filter,
)
from app.video.transcribe import TranscriptionError, transcribe_media

__all__ = [
    "ASPECT_RATIO",
    "AUTO_CAPTION_STYLE",
    "CAPTION_PRESETS",
    "DELIVERY",
    "HOOK_STYLES",
    "METADATA_SCRUB",
    "QUALITY",
    "QUALITY_FAST",
    "SmoothedCameraman",
    "SpeakerTracker",
    "TranscriptionError",
    "add_hook_to_video",
    "blurred_backdrop",
    "burn_subtitles",
    "create_hook_banner",
    "delivery_size",
    "detect_scenes",
    "escape_filter_value",
    "generate_ass",
    "generate_srt",
    "merge_continuation_words",
    "nvenc_available",
    "probe_video_duration",
    "probe_video_resolution",
    "read_layout_ranges",
    "reframe_video",
    "subtitles_filter",
    "transcribe_media",
    "video_encode_args",
    "write_layout_ranges",
]
