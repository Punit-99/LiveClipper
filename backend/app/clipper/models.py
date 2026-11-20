"""Data models for the YouTube Video Clipper feature."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any


@dataclass
class VideoMetadata:
    """Metadata extracted for a YouTube video."""

    url: str = ""
    video_id: str = ""
    title: str = ""
    channel: str = ""
    duration: float = 0.0
    description: str = ""
    view_count: int = 0
    like_count: int = 0
    thumbnail_url: str = ""
    chapters: list[dict[str, Any]] = field(default_factory=list)
    heatmap: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TranscriptSegment:
    """Individual transcript speech segment with timing."""

    start: float
    end: float
    text: str


@dataclass
class MomentCandidate:
    """A detected highlight moment candidate with multi-signal scores."""

    moment_id: str
    start_time: float
    end_time: float
    duration: float
    score: float  # 0 - 100
    reason: str
    suggested_title: str
    transcript_excerpt: str = ""
    signal_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImportClip:
    """Extracted clip ready for viewing or shorts processing."""

    clip_id: str
    job_id: str
    video_id: str
    video_title: str
    channel: str
    start_time: float
    end_time: float
    duration: float
    score: float
    reason: str
    suggested_title: str
    transcript: str
    file_path: str
    thumbnail_path: str
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_imported: bool = True
    source_type: str = "imported"
    source_url: str = ""

    @property
    def sidecar_path(self) -> str:
        return str(Path(self.file_path).with_suffix(".json"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImportJob:
    """State tracking for a video import & analysis job."""

    job_id: str
    url: str
    video_id: str = ""
    status: str = "pending"  # pending, downloading, transcribing, analyzing, extracting, completed, failed
    progress: int = 0  # 0 - 100
    current_step: str = "Queued"
    error: str | None = None
    metadata: VideoMetadata | None = None
    clips: list[ImportClip] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.metadata:
            data["metadata"] = self.metadata.to_dict()
        data["clips"] = [c.to_dict() if isinstance(c, ImportClip) else c for c in self.clips]
        return data
