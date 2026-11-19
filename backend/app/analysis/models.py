"""Data contracts, configuration, and output sinks for Stage 4 Gemini analysis.

This module defines:
- `HighlightCategory`: canonical highlight & reaction categories.
- `GeminiHighlightVerdict`: the Pydantic schema enforced natively by Gemini's
  structured output API (`response_schema`).
- `AnalyzedHighlight`: the complete, JSON-serializable Stage 4 output record
  combining `HighlightEvent` metadata with Gemini's verdict, latency, and token
  usage.
- `AnalysisConfig` + `build_analysis_config()`: validated settings for the
  analysis client, queue, and guardrails.
- `AnalysisSink` implementations (`logging_analysis_sink`, `JsonlAnalysisSink`,
  `CompositeAnalysisSink`) plus `CompositeHighlightSink` for wiring into
  `StreamHighlightDetector` without modifying detection code.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from app.config import Settings
from app.detection.highlight_event import HighlightEvent
from app.detection.highlight_detector import HighlightSink
from app.logging_setup import get_logger

log = get_logger(__name__)


class HighlightCategory(str, Enum):
    """Classification of a chat-detected highlight candidate."""

    FUNNY_MOMENT = "funny_moment"
    UNEXPECTED_MOMENT = "unexpected_moment"
    EXCITING_GAMEPLAY = "exciting_gameplay"
    SURPRISING_EVENT = "surprising_event"
    STREAMER_FAIL = "streamer_fail"
    IMPRESSIVE_GAMEPLAY = "impressive_gameplay"
    EMOTIONAL_REACTION = "emotional_reaction"
    MEME_COMMUNITY_REACTION = "meme_community_reaction"
    SPAM_OR_FALSE_POSITIVE = "spam_or_false_positive"
    OTHER = "other"


class AnalysisStatus(str, Enum):
    """Lifecycle outcome for an analyzed candidate event."""

    ANALYZED = "analyzed"
    SKIPPED_PREFILTER = "skipped_prefilter"
    FAILED = "failed"


class GeminiHighlightVerdict(BaseModel):
    """Structured JSON verdict returned by Gemini for one chat candidate.

    Important scope notes:
    - Gemini only inspects chat messages and detection signals (no video/audio),
      so `title`, `reasoning`, and `reaction_summary` describe a *chat-based*
      highlight candidate rather than visually confirmed gameplay.
    - `should_clip` means Gemini recommends flagging/saving this timestamp
      window for clipping in a later stage (no clipping happens here).
    """

    is_highlight: bool = Field(
        description=(
            "True if chat messages indicate viewers are genuinely reacting to "
            "an interesting stream moment; False for spam, noise, or mundane chat."
        )
    )
    should_clip: bool = Field(
        description=(
            "True if this chat-based highlight candidate is strong enough to "
            "recommend saving for clipping later."
        )
    )
    highlight_score: int = Field(
        ge=0,
        le=100,
        description=(
            "Highlight strength from 0 to 100: 0-29 spam/false positive, "
            "30-54 mild/unclear chatter, 55-74 good candidate moment, "
            "75-100 strong unmistakable highlight."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Confidence (0.0 to 1.0) in this assessment based solely on the "
            "available chat context and signals."
        ),
    )
    category: HighlightCategory = Field(
        description="Primary category describing the viewer reaction or false positive."
    )
    is_spam_or_false_positive: bool = Field(
        description=(
            "True if the spike is driven by bot spam, giveaway commands, raid "
            "greetings, repetitive copypasta, or other non-highlight noise."
        )
    )
    title: str = Field(
        description=(
            "Short, descriptive candidate title (max ~80 chars) framed from "
            "chat reactions without claiming visual confirmation."
        )
    )
    reasoning: str = Field(
        description=(
            "1-3 concise sentences explaining WHY viewers appear to be reacting "
            "and why the moment is or is not worth saving for clipping, grounded "
            "in actual chat content rather than repeating message counts."
        )
    )
    reaction_summary: str = Field(
        description=(
            "Brief summary of the dominant viewer emotion/reaction observed in chat."
        )
    )
    key_quotes: list[str] = Field(
        default_factory=list,
        description="2-5 short verbatim chat messages illustrating the viewer reaction.",
    )


@dataclass(frozen=True)
class TokenUsage:
    """Token consumption reported by the Gemini API for a single request."""

    prompt_tokens: int = 0
    output_tokens: int = 0
    thoughts_tokens: int = 0
    total_tokens: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class AnalysisConfig:
    """Validated configuration for Stage 4 Gemini analysis."""

    model: str = "gemini-3.1-flash-lite"
    timeout_seconds: float = 30.0
    max_retries: int = 2
    queue_max_size: int = 20
    max_queued_per_stream: int | None = None
    max_concurrency: int = 2
    max_calls_per_minute: int = 10
    max_messages: int = 120
    min_clip_score: int = 65
    min_confidence: float = 0.6
    rate_limit_cooldown_seconds: float = 30.0
    temperature: float = 0.2
    max_output_tokens: int = 600
    output_path: Path | None = None

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must be a non-empty string")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.queue_max_size < 1:
            raise ValueError("queue_max_size must be >= 1")
        if self.max_queued_per_stream is not None and self.max_queued_per_stream < 1:
            raise ValueError("max_queued_per_stream must be >= 1 when set")
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.max_calls_per_minute < 1:
            raise ValueError("max_calls_per_minute must be >= 1")
        if self.max_messages < 1:
            raise ValueError("max_messages must be >= 1")
        if not 0 <= self.min_clip_score <= 100:
            raise ValueError("min_clip_score must be in [0, 100]")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be in [0.0, 1.0]")
        if self.rate_limit_cooldown_seconds < 0:
            raise ValueError("rate_limit_cooldown_seconds must be >= 0")


def build_analysis_config(settings: Settings) -> AnalysisConfig:
    """Build a validated `AnalysisConfig` from app `Settings`."""
    return AnalysisConfig(
        model=settings.gemini_model,
        timeout_seconds=settings.gemini_timeout_seconds,
        max_retries=settings.gemini_max_retries,
        queue_max_size=settings.gemini_queue_max_size,
        max_concurrency=settings.gemini_max_concurrency,
        max_calls_per_minute=settings.gemini_max_calls_per_minute,
        max_messages=settings.gemini_max_messages,
        min_clip_score=settings.gemini_min_clip_score,
        min_confidence=settings.gemini_min_confidence,
        output_path=settings.highlights_output_path,
    )


def utc_iso_from_epoch(epoch_seconds: float) -> str:
    """Format a Unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class AnalyzedHighlight:
    """Complete output record pairing a `HighlightEvent` with Gemini's analysis."""

    event_id: str
    stream_id: str
    streamer_name: str
    start_timestamp: float
    trigger_timestamp: float
    end_timestamp: float
    duration_seconds: float
    message_count: int
    unique_chatters: int
    emote_count: int
    activity_score: float
    detection_signals_triggered: list[str]
    status: AnalysisStatus
    analyzed_at: float
    analyzed_at_iso: str
    model_used: str | None = None
    verdict: GeminiHighlightVerdict | None = None
    latency_ms: float | None = None
    token_usage: TokenUsage | None = None
    skip_or_error_reason: str | None = None

    @classmethod
    def from_event(
        cls,
        event: HighlightEvent,
        *,
        status: AnalysisStatus,
        model_used: str | None = None,
        verdict: GeminiHighlightVerdict | None = None,
        latency_ms: float | None = None,
        token_usage: TokenUsage | None = None,
        skip_or_error_reason: str | None = None,
        analyzed_at: float | None = None,
    ) -> AnalyzedHighlight:
        ts = time.time() if analyzed_at is None else analyzed_at
        return cls(
            event_id=event.event_id,
            stream_id=event.stream_id,
            streamer_name=event.streamer_name,
            start_timestamp=event.start_timestamp,
            trigger_timestamp=event.trigger_timestamp,
            end_timestamp=event.end_timestamp,
            duration_seconds=event.duration_seconds,
            message_count=event.message_count,
            unique_chatters=event.unique_chatters,
            emote_count=event.emote_count,
            activity_score=event.activity_score,
            detection_signals_triggered=list(event.detection_signals.triggered),
            status=status,
            analyzed_at=ts,
            analyzed_at_iso=utc_iso_from_epoch(ts),
            model_used=model_used,
            verdict=verdict,
            latency_ms=latency_ms,
            token_usage=token_usage,
            skip_or_error_reason=skip_or_error_reason,
        )

    def to_dict(self) -> dict:
        verdict = self.verdict
        return {
            "event_id": self.event_id,
            "stream_id": self.stream_id,
            "streamer_name": self.streamer_name,
            "start_timestamp": self.start_timestamp,
            "trigger_timestamp": self.trigger_timestamp,
            "end_timestamp": self.end_timestamp,
            "duration_seconds": self.duration_seconds,
            "message_count": self.message_count,
            "unique_chatters": self.unique_chatters,
            "emote_count": self.emote_count,
            "activity_score": round(self.activity_score, 3),
            "detection_signals_triggered": list(self.detection_signals_triggered),
            "status": self.status.value,
            "analyzed_at": self.analyzed_at,
            "analyzed_at_iso": self.analyzed_at_iso,
            "model_used": self.model_used,
            "is_highlight": verdict.is_highlight if verdict else False,
            "should_clip": verdict.should_clip if verdict else False,
            "highlight_score": verdict.highlight_score if verdict else 0,
            "confidence": verdict.confidence if verdict else 0.0,
            "category": (
                verdict.category.value
                if verdict
                else (
                    HighlightCategory.SPAM_OR_FALSE_POSITIVE.value
                    if self.status == AnalysisStatus.SKIPPED_PREFILTER
                    else HighlightCategory.OTHER.value
                )
            ),
            "is_spam_or_false_positive": (
                verdict.is_spam_or_false_positive
                if verdict
                else (self.status == AnalysisStatus.SKIPPED_PREFILTER)
            ),
            "title": verdict.title if verdict else "",
            "reasoning": verdict.reasoning if verdict else "",
            "reaction_summary": verdict.reaction_summary if verdict else "",
            "key_quotes": list(verdict.key_quotes) if verdict else [],
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "token_usage": self.token_usage.to_dict() if self.token_usage else None,
            "skip_or_error_reason": self.skip_or_error_reason,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# A sink receives each completed AnalyzedHighlight record.
AnalysisSink = Callable[[AnalyzedHighlight], None]


def logging_analysis_sink(result: AnalyzedHighlight) -> None:
    """Log a concise, secret-safe summary of an analyzed highlight candidate."""
    short_id = result.event_id[:8]
    if result.status == AnalysisStatus.SKIPPED_PREFILTER:
        log.info(
            "GEMINI SKIPPED [%s] id=%s reason=%s (prefilter; 0 API calls)",
            result.streamer_name,
            short_id,
            result.skip_or_error_reason or "unknown",
        )
        return

    if result.status == AnalysisStatus.FAILED or result.verdict is None:
        log.warning(
            "GEMINI FAILED [%s] id=%s reason=%s",
            result.streamer_name,
            short_id,
            result.skip_or_error_reason or "unknown",
        )
        return

    v = result.verdict
    if v.is_spam_or_false_positive:
        label = "SPAM"
    elif v.should_clip:
        label = "CLIP_RECOMMENDED"
    elif v.is_highlight:
        label = "HIGHLIGHT_WATCH"
    else:
        label = "SKIP"

    tokens = result.token_usage.total_tokens if result.token_usage else 0
    latency = f"{result.latency_ms:.0f}ms" if result.latency_ms is not None else "-"
    log.warning(
        "GEMINI VERDICT [%s] id=%s verdict=%s category=%s score=%d/100 "
        "conf=%.2f latency=%s tokens=%d title=%r | why: %s",
        result.streamer_name,
        short_id,
        label,
        v.category.value,
        v.highlight_score,
        v.confidence,
        latency,
        tokens,
        v.title,
        v.reasoning,
    )


class JsonlAnalysisSink:
    """Append each `AnalyzedHighlight` as one JSON line to a local `.jsonl` file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, result: AnalyzedHighlight) -> None:
        line = result.to_json() + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)


class CompositeAnalysisSink:
    """Forward each `AnalyzedHighlight` to multiple sinks with error isolation."""

    def __init__(self, *sinks: AnalysisSink) -> None:
        self._sinks = list(sinks)

    def __call__(self, result: AnalyzedHighlight) -> None:
        for sink in self._sinks:
            try:
                sink(result)
            except Exception:  # noqa: BLE001  one failing sink must not break others
                log.exception("Analysis sink %r raised.", sink)


class CompositeHighlightSink:
    """Fan out a finalized `HighlightEvent` to multiple non-blocking sinks."""

    def __init__(self, *sinks: HighlightSink) -> None:
        self._sinks = list(sinks)

    def __call__(self, event: HighlightEvent) -> None:
        for sink in self._sinks:
            try:
                sink(event)
            except Exception:  # noqa: BLE001  keep detection and other sinks isolated
                log.exception("Highlight sink %r raised.", sink)
