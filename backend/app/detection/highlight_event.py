"""Data structures for candidate highlight events.

These are the Stage-4 (Gemini) contract: a `HighlightEvent` is a self-contained,
JSON-serializable description of a candidate moment and the chat around it. This
module is pure data  no detection logic, no networking.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ChatMessage:
    """One chat message, as captured for a highlight event.

    `timestamp` is wall-clock epoch seconds — meaningful for humans and Gemini.
    """

    username: str
    text: str
    timestamp: float
    emote_count: int = 0
    message_id: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DetectionSignals:
    """The signal readings that justified a candidate event (for transparency).

    Rates are in the detector's units. `baseline_rate` and `rate_ratio` come from
    the (weighted) trigger core; the rest are raw counts over the trigger window.
    """

    messages_per_sec: float = 0.0
    baseline_rate: float = 0.0
    rate_ratio: float = 0.0
    unique_chatters: int = 0
    emote_count: int = 0
    emote_rate: float = 0.0
    repeat_ratio: float = 0.0        # 1 - (unique message texts / messages)
    top_user_share: float = 0.0      # busiest single user's share of messages
    sustained_seconds: float = 0.0   # consecutive elevated seconds at trigger
    triggered: list[str] = field(default_factory=list)  # notable signal names

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HighlightEvent:
    """A candidate highlight: a trigger moment plus the surrounding chat."""

    stream_id: str
    streamer_name: str
    event_id: str
    start_timestamp: float          # wall-clock epoch seconds (context start)
    end_timestamp: float            # wall-clock epoch seconds (collection end)
    trigger_timestamp: float        # wall-clock epoch seconds (spike moment)
    messages: list[ChatMessage]
    message_count: int
    unique_chatters: int
    emote_count: int
    activity_score: float
    detection_signals: DetectionSignals
    merged_count: int = 0           # how many later spikes merged into this event
    audio_energy: dict | None = None  # loudness and volume spikes from stream buffer

    def to_dict(self) -> dict:
        return {
            "stream_id": self.stream_id,
            "streamer_name": self.streamer_name,
            "event_id": self.event_id,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "trigger_timestamp": self.trigger_timestamp,
            "messages": [m.to_dict() for m in self.messages],
            "message_count": self.message_count,
            "unique_chatters": self.unique_chatters,
            "emote_count": self.emote_count,
            "activity_score": self.activity_score,
            "detection_signals": self.detection_signals.to_dict(),
            "merged_count": self.merged_count,
            "audio_energy": self.audio_energy,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.end_timestamp - self.start_timestamp)
