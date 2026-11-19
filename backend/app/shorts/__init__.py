"""YouTube Shorts integration for LiveClipper (Stage 7).

Provides YouTube OAuth 2.0 authorization, resumable chunked video upload,
daily quota management, metadata formatting, duration adaptation,
dual-engine vertical video reframing, and pipeline orchestration for YouTube Shorts.
"""

from app.shorts.duration_adapter import (
    adapt_clip_duration,
    calculate_trim_window,
    probe_video_duration,
)
from app.shorts.metadata import format_youtube_snippet, sanitize_title
from app.shorts.openshorts_bridge import (
    EngineUnavailableError,
    OpenShortsBridge,
    OpenShortsBridgeError,
)
from app.shorts.pipeline import (
    STATE_APPROVED,
    STATE_DISCOVERED,
    STATE_FAILED,
    STATE_PROCESSED,
    STATE_REJECTED,
    STATE_UPLOADED,
    ShortsManifest,
    ShortsPipeline,
)
from app.shorts.viral_metadata import (
    ViralMetadata,
    ViralMetadataGenerator,
    ViralTitleOption,
    generate_fallback_description,
    generate_fallback_tags,
    generate_fallback_titles,
    sanitize_viral_description,
    sanitize_viral_tags,
    sanitize_viral_title,
    strip_emojis,
)
from app.shorts.youtube_auth import (
    ReauthRequired,
    TransientAuthError,
    YouTubeAuthError,
    YouTubeTokenManager,
)
from app.shorts.youtube_uploader import (
    DailyQuotaTracker,
    QuotaExceededError,
    TransientUploadError,
    UploadSessionExpiredError,
    YouTubeUploader,
    YouTubeUploadError,
)

__all__ = [
    "YouTubeTokenManager",
    "YouTubeAuthError",
    "TransientAuthError",
    "ReauthRequired",
    "YouTubeUploader",
    "YouTubeUploadError",
    "QuotaExceededError",
    "TransientUploadError",
    "UploadSessionExpiredError",
    "DailyQuotaTracker",
    "sanitize_title",
    "format_youtube_snippet",
    "OpenShortsBridge",
    "OpenShortsBridgeError",
    "EngineUnavailableError",
    "adapt_clip_duration",
    "calculate_trim_window",
    "probe_video_duration",
    "ShortsPipeline",
    "ShortsManifest",
    "STATE_DISCOVERED",
    "STATE_PROCESSED",
    "STATE_APPROVED",
    "STATE_REJECTED",
    "STATE_UPLOADED",
    "STATE_FAILED",
    "ViralMetadata",
    "ViralMetadataGenerator",
    "ViralTitleOption",
    "sanitize_viral_title",
    "sanitize_viral_description",
    "sanitize_viral_tags",
    "strip_emojis",
]
