"""Stage 5.1 data contracts for video segments, discontinuities, pins, and clip plans.

Pure data and configuration validation only — no subprocesses, no FFmpeg calls,
no filesystem writes, and no Stage 1–4 pipeline coupling.

Timestamp design note (important):
- `VideoSegment` stores `wall_closed`, `mono_closed`, `media_duration`, and
  FFmpeg's `ffmpeg_start_pts` / `ffmpeg_end_pts` **separately**.
- `approx_start_wall` (`wall_closed - media_duration`) is explicitly labeled
  approximate because HLS live-edge prefetch bursts, network jitter, and
  Twitch broadcast/reaction delays prevent exact wall-clock synchronization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from app.config import Settings


class PinState(str, Enum):
    """Lifecycle state of a rolling-buffer time-window pin."""

    PENDING_ANALYSIS = "pending_analysis"  # Waiting on Gemini verdict (subject to TTL)
    APPROVED = "approved"                  # Gemini set should_clip=True (protected)
    EXTRACTING = "extracting"              # FFmpeg actively reading segments (protected)
    RELEASED = "released"                  # Finished or rejected; segments may expire
    EXPIRED_TTL = "expired_ttl"            # Pending pin exceeded max TTL without verdict


@dataclass(frozen=True)
class RecordingConfig:
    """Validated Stage 5 configuration for rolling buffer & clip window planning."""

    enabled: bool = False
    quality: str = "1080p60,1080p,720p60,best"
    low_latency: bool = True
    segment_seconds: float = 4.0
    buffer_seconds: float = 300.0
    max_buffer_mb: float = 500.0
    emergency_buffer_multiplier: float = 1.25
    max_active_pins: int = 10
    pin_max_ttl_seconds: float = 180.0
    gap_tolerance_seconds: float = 1.5
    buffer_dir: Path | None = None
    # Heuristic chat-to-video offset and clip bounds (seconds).
    reaction_offset_seconds: float = 6.0
    clip_pre_seconds: float = 12.0
    clip_post_seconds: float = 18.0
    clip_max_duration_seconds: float = 90.0
    clip_reencode: bool = True
    clips_output_dir: Path | None = None

    def __post_init__(self) -> None:
        if not self.quality.strip():
            raise ValueError("quality must be a non-empty string")
        if self.segment_seconds <= 0:
            raise ValueError("segment_seconds must be > 0")
        if self.buffer_seconds < self.segment_seconds:
            raise ValueError("buffer_seconds must be >= segment_seconds")
        if self.max_buffer_mb <= 0:
            raise ValueError("max_buffer_mb must be > 0")
        if self.emergency_buffer_multiplier < 1.0:
            raise ValueError("emergency_buffer_multiplier must be >= 1.0")
        if self.max_active_pins < 1:
            raise ValueError("max_active_pins must be >= 1")
        if self.pin_max_ttl_seconds <= 0:
            raise ValueError("pin_max_ttl_seconds must be > 0")
        if self.gap_tolerance_seconds < 0:
            raise ValueError("gap_tolerance_seconds must be >= 0")
        if self.reaction_offset_seconds < 0:
            raise ValueError("reaction_offset_seconds must be >= 0")
        if self.clip_pre_seconds < 0:
            raise ValueError("clip_pre_seconds must be >= 0")
        if self.clip_post_seconds < 0:
            raise ValueError("clip_post_seconds must be >= 0")
        if self.clip_max_duration_seconds <= 0:
            raise ValueError("clip_max_duration_seconds must be > 0")

    @property
    def max_buffer_bytes(self) -> int:
        return int(self.max_buffer_mb * 1024 * 1024)

    @property
    def emergency_buffer_bytes(self) -> int:
        return int(self.max_buffer_bytes * self.emergency_buffer_multiplier)


def build_recording_config(settings: Settings) -> RecordingConfig:
    """Build a validated `RecordingConfig` from application `Settings`."""
    return RecordingConfig(
        enabled=settings.recording_enabled,
        quality=settings.recording_quality,
        low_latency=settings.recording_low_latency,
        segment_seconds=settings.recording_segment_seconds,
        buffer_seconds=settings.recording_buffer_seconds,
        max_buffer_mb=settings.recording_max_buffer_mb,
        max_active_pins=settings.recording_max_active_pins,
        pin_max_ttl_seconds=settings.recording_pin_max_ttl_seconds,
        gap_tolerance_seconds=settings.recording_gap_tolerance_seconds,
        buffer_dir=settings.recording_buffer_dir,
        reaction_offset_seconds=settings.clip_reaction_offset_seconds,
        clip_pre_seconds=settings.clip_pre_seconds,
        clip_post_seconds=settings.clip_post_seconds,
        clip_max_duration_seconds=settings.clip_max_duration_seconds,
        clip_reencode=settings.clip_reencode,
        clips_output_dir=settings.clips_output_dir,
    )


@dataclass(frozen=True)
class VideoSegment:
    """Metadata for one finalized rolling-buffer video segment.

    All raw timing observations are preserved separately so downstream stages
    and future calibration never lose raw measurements.
    """

    segment_id: str
    path: Path
    session_id: int
    wall_closed: float        # Local time.time() observed when segment finalized
    mono_closed: float        # Local time.monotonic() observed when segment finalized
    media_duration: float     # Actual media duration in seconds (FFmpeg CSV / probe)
    ffmpeg_start_pts: float   # FFmpeg segment_list start PTS (seconds)
    ffmpeg_end_pts: float     # FFmpeg segment_list end PTS (seconds)
    size_bytes: int = 0

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("segment_id must be non-empty")
        if self.session_id < 0:
            raise ValueError("session_id must be >= 0")
        if self.media_duration <= 0:
            raise ValueError("media_duration must be > 0")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be >= 0")

    @property
    def approx_start_wall(self) -> float:
        """Approximate local wall-clock start (NOT exact broadcast time)."""
        return self.wall_closed - self.media_duration

    @property
    def approx_end_wall(self) -> float:
        """Approximate local wall-clock end (observation time when segment closed)."""
        return self.wall_closed

    @property
    def approx_start_mono(self) -> float:
        """Approximate monotonic start derived from close time minus media duration."""
        return self.mono_closed - self.media_duration

    @property
    def approx_end_mono(self) -> float:
        """Monotonic time observed when segment closed."""
        return self.mono_closed

    def to_dict(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "path": str(self.path),
            "session_id": self.session_id,
            "wall_closed": self.wall_closed,
            "mono_closed": self.mono_closed,
            "media_duration": round(self.media_duration, 4),
            "ffmpeg_start_pts": round(self.ffmpeg_start_pts, 4),
            "ffmpeg_end_pts": round(self.ffmpeg_end_pts, 4),
            "approx_start_wall": self.approx_start_wall,
            "approx_end_wall": self.approx_end_wall,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class DiscontinuityRecord:
    """Detected discontinuity or gap between two consecutive indexed segments."""

    prev_segment_id: str
    next_segment_id: str
    prev_session_id: int
    next_session_id: int
    mono_gap_seconds: float   # next.approx_start_mono - prev.approx_end_mono
    wall_gap_seconds: float   # next.approx_start_wall - prev.approx_end_wall
    reason: str               # "session_restart", "monotonic_gap", "pts_discontinuity"

    def to_dict(self) -> dict[str, Any]:
        return {
            "prev_segment_id": self.prev_segment_id,
            "next_segment_id": self.next_segment_id,
            "prev_session_id": self.prev_session_id,
            "next_session_id": self.next_session_id,
            "mono_gap_seconds": round(self.mono_gap_seconds, 4),
            "wall_gap_seconds": round(self.wall_gap_seconds, 4),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PinCoverageReport:
    """Honest coverage assessment of how well indexed segments cover a requested window."""

    requested_start_wall: float
    requested_end_wall: float
    has_full_preroll: bool
    has_full_postroll: bool
    is_partial: bool
    segment_count: int
    actual_approx_start_wall: float | None
    actual_approx_end_wall: float | None
    missing_leading_seconds: float
    missing_trailing_seconds: float
    total_media_duration: float
    discontinuities: list[DiscontinuityRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_start_wall": self.requested_start_wall,
            "requested_end_wall": self.requested_end_wall,
            "has_full_preroll": self.has_full_preroll,
            "has_full_postroll": self.has_full_postroll,
            "is_partial": self.is_partial,
            "segment_count": self.segment_count,
            "actual_approx_start_wall": self.actual_approx_start_wall,
            "actual_approx_end_wall": self.actual_approx_end_wall,
            "missing_leading_seconds": round(self.missing_leading_seconds, 3),
            "missing_trailing_seconds": round(self.missing_trailing_seconds, 3),
            "total_media_duration": round(self.total_media_duration, 3),
            "discontinuities": [d.to_dict() for d in self.discontinuities],
        }


@dataclass(frozen=True)
class ClipWindowPlan:
    """Planned clip extraction window derived from a finalized `HighlightEvent`.

    Computed at pin time (while the full `HighlightEvent` including `messages`
    and `merged_count` is available) so Stage 1–4 contracts stay untouched.
    """

    event_id: str
    stream_id: str
    streamer_name: str
    event_start_timestamp: float
    event_trigger_timestamp: float
    event_end_timestamp: float
    event_merged_count: int
    peak_chat_timestamp: float | None
    reaction_offset_seconds: float       # Explicitly heuristic, not exact sync
    estimated_moment_start: float
    estimated_moment_end: float
    requested_clip_start: float
    requested_clip_end: float
    truncated_to_peak: bool = False
    planning_notes: list[str] = field(default_factory=list)

    @property
    def requested_duration(self) -> float:
        return max(0.0, self.requested_clip_end - self.requested_clip_start)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stream_id": self.stream_id,
            "streamer_name": self.streamer_name,
            "event_start_timestamp": self.event_start_timestamp,
            "event_trigger_timestamp": self.event_trigger_timestamp,
            "event_end_timestamp": self.event_end_timestamp,
            "event_merged_count": self.event_merged_count,
            "peak_chat_timestamp": self.peak_chat_timestamp,
            "reaction_offset_seconds": self.reaction_offset_seconds,
            "estimated_moment_start": self.estimated_moment_start,
            "estimated_moment_end": self.estimated_moment_end,
            "requested_clip_start": self.requested_clip_start,
            "requested_clip_end": self.requested_clip_end,
            "requested_duration": round(self.requested_duration, 3),
            "truncated_to_peak": self.truncated_to_peak,
            "planning_notes": list(self.planning_notes),
        }


@dataclass
class BufferPin:
    """Active time-window reservation preventing rolling-buffer deletion."""

    event_id: str
    requested_start_wall: float
    requested_end_wall: float
    created_mono: float
    ttl_seconds: float
    state: PinState = PinState.PENDING_ANALYSIS
    plan: ClipWindowPlan | None = None
    initial_coverage: PinCoverageReport | None = None

    @property
    def is_active(self) -> bool:
        return self.state in (
            PinState.PENDING_ANALYSIS,
            PinState.APPROVED,
            PinState.EXTRACTING,
        )

    @property
    def is_protected_from_eviction(self) -> bool:
        """Approved highlights and active extractions must never be auto-evicted."""
        return self.state in (PinState.APPROVED, PinState.EXTRACTING)

    def is_ttl_expired(self, now_mono: float) -> bool:
        """Only PENDING_ANALYSIS pins expire via TTL; approved/extracting stay protected."""
        if self.state != PinState.PENDING_ANALYSIS:
            return False
        return (now_mono - self.created_mono) >= self.ttl_seconds


@dataclass(frozen=True)
class SegmentSelection:
    """Segments and trim parameters selected from `SegmentIndex` for extraction."""

    segments: list[VideoSegment]
    approx_trim_start_seconds: float
    target_duration_seconds: float
    coverage: PinCoverageReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "approx_trim_start_seconds": round(self.approx_trim_start_seconds, 4),
            "target_duration_seconds": round(self.target_duration_seconds, 4),
            "coverage": self.coverage.to_dict(),
            "segments_used": [s.to_dict() for s in self.segments],
        }


@dataclass(frozen=True)
class ExtractedClipMetadata:
    """JSON sidecar contract written alongside each extracted standalone `.mp4`."""

    event_id: str
    stream_id: str
    streamer_name: str
    clip_path: str
    extracted_at: float
    extracted_at_iso: str
    clip_plan: ClipWindowPlan
    selection: SegmentSelection
    analysis: dict[str, Any]
    reencoded: bool = True
    platform: str = "twitch"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "stream_id": self.stream_id,
            "streamer_name": self.streamer_name,
            "clip_path": self.clip_path,
            "extracted_at": self.extracted_at,
            "extracted_at_iso": self.extracted_at_iso,
            "reencoded": self.reencoded,
            "platform": self.platform,
            "clip_plan": self.clip_plan.to_dict(),
            "selection": self.selection.to_dict(),
            "analysis": self.analysis,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
