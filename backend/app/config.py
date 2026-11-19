"""Load and validate configuration from environment variables (.env).

Why this file exists:
- ONE place that knows how to read settings, so the rest of the code just asks
  for `settings.client_id` instead of poking at os.environ everywhere.
- It validates early and fails with a clear, actionable message if something
  required is missing  so you never chase a confusing error deep in the code.

Secrets live in `.env` (git-ignored). This module loads that file if present,
but real values always come from the environment, never from source code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env that sits next to the backend/ folder (if it exists). Values already
# present in the real environment win over the file, which is standard practice.
_BACKEND_DIR = Path(__file__).resolve().parent.parent
_env_file = _BACKEND_DIR / ".env"
try:
    if _env_file.is_file():
        load_dotenv(_env_file)
except OSError:
    pass


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    """Typed, validated view of our configuration."""

    client_id: str
    client_secret: str
    redirect_uri: str
    category_name: str
    max_streams: int
    stream_language: str | None
    # --- chat spike detection thresholds ---
    spike_window_seconds: float
    spike_multiplier: float
    spike_min_messages: int
    spike_cooldown_seconds: float
    spike_warmup_seconds: float
    # --- candidate highlight event windows ---
    event_pre_seconds: float
    event_post_seconds: float
    emote_weight: float
    max_event_seconds: float
    # --- Gemini AI highlight analysis (Stage 4; optional for Twitch-only scripts) ---
    gemini_api_key: str | None = None
    gemini_enabled: bool = True
    gemini_model: str = "gemini-3.1-flash-lite"
    gemini_timeout_seconds: float = 30.0
    gemini_max_retries: int = 2
    gemini_queue_max_size: int = 20
    gemini_max_concurrency: int = 2
    gemini_max_calls_per_minute: int = 10
    gemini_max_messages: int = 120
    gemini_min_clip_score: int = 65
    gemini_min_confidence: float = 0.6
    highlights_output_path: Path = _BACKEND_DIR / "highlights.jsonl"
    # --- Stage 5: Live video capture, rolling buffer & clip planning (optional) ---
    recording_enabled: bool = False
    recording_quality: str = "1080p60,1080p,720p60,best"
    recording_low_latency: bool = True
    recording_segment_seconds: float = 4.0
    recording_buffer_seconds: float = 300.0
    recording_max_buffer_mb: float = 500.0
    recording_max_active_pins: int = 10
    recording_pin_max_ttl_seconds: float = 180.0
    recording_gap_tolerance_seconds: float = 1.5
    recording_buffer_dir: Path = _BACKEND_DIR / ".buffer"
    clip_reaction_offset_seconds: float = 6.0
    clip_pre_seconds: float = 12.0
    clip_post_seconds: float = 18.0
    clip_max_duration_seconds: float = 90.0
    clip_reencode: bool = True
    clips_output_dir: Path = _BACKEND_DIR / "clips"
    # --- Stage 6: Multi-stream orchestration (optional) ---
    max_concurrent_streams: int = 3
    max_total_buffer_mb: float = 1500.0
    max_concurrent_extractions: int = 2
    liveness_poll_seconds: float = 60.0
    target_channels: tuple[str, ...] = ()
    multi_stream_enabled: bool = False
    # --- Stage 7: Shorts processing & YouTube upload (optional) ---
    shorts_output_dir: Path = _BACKEND_DIR / "shorts"
    shorts_engine: str = "auto"
    shorts_max_duration: float = 59.0
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    youtube_privacy_status: str = "public"
    youtube_auto_upload: bool = False
    youtube_min_score: int = 75
    youtube_daily_limit: int = 5
    youtube_tokens_path: Path = _BACKEND_DIR / ".youtube_tokens.json"
    # --- Stage 8: Full OpenShorts Docker Integration ---
    openshorts_docker_image: str = "liveclipper/openshorts:latest"
    openshorts_docker_timeout: float = 600.0
    openshorts_subtitle_style: str = "bold"
    openshorts_reframe_mode: str = "auto"
    # --- Stage 9: YouTube Viral Metadata Optimization Engine ---
    viral_metadata_enabled: bool = True
    viral_metadata_model: str = "gemini-2.5-flash"
    viral_title_emoji: bool = True
    viral_title_language: str = "en"
    # --- Stage 10: Multi-Platform, Video Quality & Language Filtering ---
    monitor_platform: str = "twitch"  # "twitch" | "youtube" | "all"
    target_youtube_channels: tuple[str, ...] = (
        "UC7cs8q-gJRlGwj4A8OmCmXg",  # CarryMinati
        "UCvQ0C-sIKKlFaxyHYRE1YSg",  # Mortal
        "UCsiddxZ6V4Flv-s3nps49AQ",  # Techno Gamerz
        "UCG4eH77kG_X9F0w_dE7g1rg",  # Dynamo Gaming
        "UCjqF3HPUPi_1c64LpD2w0_g",  # Total Gaming
        "UC0pA8_YvGqQx-r4C7d2P6FA",  # Scout
        "UCXfJ48U5G1T5k-p8G9f5J9A",  # Jonathan Gaming
        "UClvgJrkf4k4Kq2R4H8_wB9g",  # Mythpat
    )
    excluded_youtube_channels: tuple[str, ...] = ()
    excluded_target_channels: tuple[str, ...] = ()
    language_filter_enabled: bool = True
    target_languages: tuple[str, ...] = ("en", "hi")
    shorts_output_bitrate: str = "6M"
    shorts_output_crf: int = 18
    youtube_chat_poll_interval: float = 2.0


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.endswith("_here"):
        raise ConfigError(
            f"Missing required setting '{name}'. "
            f"Copy backend/.env.example to backend/.env and fill it in."
        )
    return value


def _optional_secret(name: str) -> str | None:
    """Return a secret env var if present and not an untouched placeholder."""
    value = os.getenv(name, "").strip()
    if not value or value.endswith("_here"):
        return None
    return value


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got '{raw}'.")


def _path_env(name: str, default_relative: str) -> Path:
    raw = os.getenv(name, "").strip() or default_relative
    path = Path(raw)
    return path if path.is_absolute() else (_BACKEND_DIR / path)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got '{raw}'.")


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number, got '{raw}'.")


def _tuple_env(name: str) -> tuple[str, ...]:
    """Parse comma/whitespace separated channel names into a normalized tuple."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    items: list[str] = []
    for token in raw.replace(",", " ").split():
        clean = token.lstrip("#").strip().lower()
        if clean and clean not in items:
            items.append(clean)
    return tuple(items)


def load_settings() -> Settings:
    """Read, validate, and return all settings. Raises ConfigError on problems."""
    client_id = _require("TWITCH_CLIENT_ID")
    client_secret = _require("TWITCH_CLIENT_SECRET")
    redirect_uri = os.getenv("TWITCH_REDIRECT_URI", "http://localhost:3000").strip()
    category_name = os.getenv("GTA6_CATEGORY_NAME", "Grand Theft Auto VI").strip()

    raw_max = os.getenv("MAX_STREAMS", "20").strip()
    try:
        max_streams = max(1, min(100, int(raw_max)))
    except ValueError:
        raise ConfigError(f"MAX_STREAMS must be a whole number, got '{raw_max}'.")

    language = os.getenv("STREAM_LANGUAGE", "").strip() or None

    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite").strip() or "gemini-3.1-flash-lite"
    recording_quality = os.getenv("RECORDING_QUALITY", "1080p60,1080p,720p60,best").strip() or "1080p60,1080p,720p60,best"

    monitor_platform = os.getenv("MONITOR_PLATFORM", "twitch").strip().lower() or "all"
    if monitor_platform not in ("twitch", "youtube", "all"):
        raise ConfigError(f"MONITOR_PLATFORM must be 'twitch', 'youtube', or 'all', got '{monitor_platform}'.")

    target_yt_raw = os.getenv("TARGET_YOUTUBE_CHANNELS", "").strip()
    if target_yt_raw:
        yt_channels = tuple(
            t.strip() for t in target_yt_raw.replace(",", " ").split() if t.strip()
        )
    else:
        yt_channels = Settings.target_youtube_channels

    target_lang_raw = os.getenv("TARGET_LANGUAGES", "en,hi").strip()
    target_languages = tuple(
        t.strip().lower() for t in target_lang_raw.replace(",", " ").split() if t.strip()
    ) or ("en", "hi")

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        category_name=category_name,
        max_streams=max_streams,
        stream_language=language,
        spike_window_seconds=_float_env("SPIKE_WINDOW_SECONDS", 10.0),
        spike_multiplier=_float_env("SPIKE_MULTIPLIER", 3.0),
        spike_min_messages=_int_env("SPIKE_MIN_MESSAGES", 10),
        spike_cooldown_seconds=_float_env("SPIKE_COOLDOWN_SECONDS", 15.0),
        spike_warmup_seconds=_float_env("SPIKE_WARMUP_SECONDS", 30.0),
        event_pre_seconds=_float_env("EVENT_PRE_SECONDS", 10.0),
        event_post_seconds=_float_env("EVENT_POST_SECONDS", 20.0),
        emote_weight=_float_env("EMOTE_WEIGHT", 0.5),
        max_event_seconds=_float_env("MAX_EVENT_SECONDS", 120.0),
        gemini_api_key=_optional_secret("GEMINI_API_KEY"),
        gemini_enabled=_bool_env("GEMINI_ENABLED", True),
        gemini_model=gemini_model,
        gemini_timeout_seconds=_float_env("GEMINI_TIMEOUT_SECONDS", 30.0),
        gemini_max_retries=_int_env("GEMINI_MAX_RETRIES", 2),
        gemini_queue_max_size=_int_env("GEMINI_QUEUE_MAX_SIZE", 20),
        gemini_max_concurrency=_int_env("GEMINI_MAX_CONCURRENCY", 2),
        gemini_max_calls_per_minute=_int_env("GEMINI_MAX_CALLS_PER_MINUTE", 10),
        gemini_max_messages=_int_env("GEMINI_MAX_MESSAGES", 120),
        gemini_min_clip_score=_int_env("GEMINI_MIN_CLIP_SCORE", 65),
        gemini_min_confidence=_float_env("GEMINI_MIN_CONFIDENCE", 0.6),
        highlights_output_path=_path_env("HIGHLIGHTS_OUTPUT_PATH", "highlights.jsonl"),
        recording_enabled=_bool_env("RECORDING_ENABLED", False),
        recording_quality=recording_quality,
        recording_low_latency=_bool_env("RECORDING_LOW_LATENCY", True),
        recording_segment_seconds=_float_env("RECORDING_SEGMENT_SECONDS", 4.0),
        recording_buffer_seconds=_float_env("RECORDING_BUFFER_SECONDS", 300.0),
        recording_max_buffer_mb=_float_env("RECORDING_MAX_BUFFER_MB", 500.0),
        recording_max_active_pins=_int_env("RECORDING_MAX_ACTIVE_PINS", 10),
        recording_pin_max_ttl_seconds=_float_env("RECORDING_PIN_MAX_TTL_SECONDS", 180.0),
        recording_gap_tolerance_seconds=_float_env("RECORDING_GAP_TOLERANCE_SECONDS", 1.5),
        recording_buffer_dir=_path_env("RECORDING_BUFFER_DIR", ".buffer"),
        clip_reaction_offset_seconds=_float_env("CLIP_REACTION_OFFSET_SECONDS", 6.0),
        clip_pre_seconds=_float_env("CLIP_PRE_SECONDS", 12.0),
        clip_post_seconds=_float_env("CLIP_POST_SECONDS", 18.0),
        clip_max_duration_seconds=_float_env("CLIP_MAX_DURATION_SECONDS", 90.0),
        clip_reencode=_bool_env("CLIP_REENCODE", True),
        clips_output_dir=_path_env("CLIPS_OUTPUT_DIR", "clips"),
        max_concurrent_streams=_int_env("MAX_CONCURRENT_STREAMS", 3),
        max_total_buffer_mb=_float_env("MAX_TOTAL_BUFFER_MB", 1500.0),
        max_concurrent_extractions=_int_env("MAX_CONCURRENT_EXTRACTIONS", 2),
        liveness_poll_seconds=_float_env("LIVENESS_POLL_SECONDS", 60.0),
        target_channels=_tuple_env("TARGET_CHANNELS"),
        multi_stream_enabled=_bool_env("MULTI_STREAM_ENABLED", False),
        shorts_output_dir=_path_env("SHORTS_OUTPUT_DIR", "shorts"),
        shorts_engine=os.getenv("SHORTS_ENGINE", "auto").strip().lower() or "auto",
        shorts_max_duration=_float_env("SHORTS_MAX_DURATION", 59.0),
        youtube_client_id=_optional_secret("YOUTUBE_CLIENT_ID"),
        youtube_client_secret=_optional_secret("YOUTUBE_CLIENT_SECRET"),
        youtube_privacy_status=os.getenv("YOUTUBE_PRIVACY_STATUS", "public").strip().lower() or "public",
        youtube_auto_upload=_bool_env("YOUTUBE_AUTO_UPLOAD", False),
        youtube_min_score=_int_env("YOUTUBE_MIN_SCORE", 75),
        youtube_daily_limit=_int_env("YOUTUBE_DAILY_LIMIT", 5),
        youtube_tokens_path=_path_env("YOUTUBE_TOKENS_PATH", ".youtube_tokens.json"),
        openshorts_docker_image=os.getenv("OPENSHORTS_DOCKER_IMAGE", "liveclipper/openshorts:latest").strip() or "liveclipper/openshorts:latest",
        openshorts_docker_timeout=_float_env("OPENSHORTS_DOCKER_TIMEOUT", 600.0),
        openshorts_subtitle_style=os.getenv("OPENSHORTS_SUBTITLE_STYLE", "bold").strip().lower() or "bold",
        openshorts_reframe_mode=os.getenv("OPENSHORTS_REFRAME_MODE", "auto").strip().lower() or "auto",
        viral_metadata_enabled=_bool_env("VIRAL_METADATA_ENABLED", True),
        viral_metadata_model=os.getenv("VIRAL_METADATA_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        viral_title_emoji=_bool_env("VIRAL_TITLE_EMOJI", True),
        viral_title_language=os.getenv("VIRAL_TITLE_LANGUAGE", "en").strip().lower() or "en",
        monitor_platform=monitor_platform,
        target_youtube_channels=yt_channels,
        excluded_youtube_channels=_tuple_env("EXCLUDED_YOUTUBE_CHANNELS"),
        excluded_target_channels=_tuple_env("EXCLUDED_TARGET_CHANNELS"),
        language_filter_enabled=_bool_env("LANGUAGE_FILTER_ENABLED", True),
        target_languages=target_languages,
        shorts_output_bitrate=os.getenv("SHORTS_OUTPUT_BITRATE", "6M").strip() or "6M",
        shorts_output_crf=_int_env("SHORTS_OUTPUT_CRF", 18),
        youtube_chat_poll_interval=_float_env("YOUTUBE_CHAT_POLL_INTERVAL", 2.0),
    )
