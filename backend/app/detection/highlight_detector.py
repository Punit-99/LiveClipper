"""Per-stream highlight detection pipeline + multi-stream manager.

Pipeline (one per stream, fully isolated state):

    message -> dedup -> rolling buffer (context)
                     -> signal analyzer (unique chatters, emotes, spam)
                     -> activity detector (rate spike, emote-weighted)
            spike?  -> open a candidate event (seeded with pre-spike context)
                       OR merge into the event already open
            time up -> finalize the event, score it, hand to the sink

The sink is the clean seam for Stage 4: a callable that receives a finished
`HighlightEvent`. Detection never imports or knows about Gemini.
"""

from __future__ import annotations

import uuid
from collections import Counter, deque
from dataclasses import dataclass
from typing import Callable

from app.config import Settings
from app.detection.activity_detector import (
    ActivityDetector,
    ActivityDetectorConfig,
    SpikeEvent,
)
from app.detection.event_buffer import RollingChatBuffer
from app.detection.highlight_event import (
    ChatMessage,
    DetectionSignals,
    HighlightEvent,
)
from app.detection.signals import SignalAnalyzer, SignalSnapshot, normalize_text
from app.logging_setup import get_logger

log = get_logger(__name__)

# A sink is anything callable with a finished HighlightEvent (Stage-4 seam).
HighlightSink = Callable[[HighlightEvent], None]


@dataclass(frozen=True)
class HighlightConfig:
    """All thresholds for the highlight pipeline."""

    detector: ActivityDetectorConfig
    pre_seconds: float = 10.0        # context captured before the spike
    post_seconds: float = 20.0       # collection window after the spike
    emote_weight: float = 0.5        # how much each emote amplifies the rate
    max_event_seconds: float = 120.0  # hard cap on a merged event's duration

    @property
    def window_seconds(self) -> float:
        return self.detector.window_seconds


def build_highlight_config(settings: Settings) -> HighlightConfig:
    """Build a HighlightConfig from the app Settings (.env)."""
    detector = ActivityDetectorConfig(
        window_seconds=settings.spike_window_seconds,
        spike_multiplier=settings.spike_multiplier,
        min_messages=settings.spike_min_messages,
        cooldown_seconds=settings.spike_cooldown_seconds,
        warmup_seconds=settings.spike_warmup_seconds,
    )
    return HighlightConfig(
        detector=detector,
        pre_seconds=settings.event_pre_seconds,
        post_seconds=settings.event_post_seconds,
        emote_weight=settings.emote_weight,
        max_event_seconds=settings.max_event_seconds,
    )


def logging_sink(event: HighlightEvent) -> None:
    """Default sink: log a concise summary (never the full message list)."""
    from datetime import datetime

    when = datetime.fromtimestamp(event.trigger_timestamp).strftime("%H:%M:%S")
    log.warning(
        "HIGHLIGHT CANDIDATE [%s] stream=#%s id=%s trigger=%s "
        "msgs=%d chatters=%d emotes=%d score=%.1f dur=%.0fs merges=%d signals=%s",
        event.streamer_name, event.stream_id, event.event_id[:8], when,
        event.message_count, event.unique_chatters, event.emote_count,
        event.activity_score, event.duration_seconds, event.merged_count,
        ",".join(event.detection_signals.triggered) or "-",
    )


class CollectingSink:
    """A sink that stores events in memory  handy for tests and inspection."""

    def __init__(self) -> None:
        self.events: list[HighlightEvent] = []

    def __call__(self, event: HighlightEvent) -> None:
        self.events.append(event)


@dataclass
class _OpenEvent:
    event_id: str
    start_mono: float
    start_wall: float
    trigger_mono: float
    trigger_wall: float
    end_mono: float
    spike: SpikeEvent
    snapshot: SignalSnapshot
    sustained_seconds: float
    messages: list[ChatMessage]
    merged_count: int = 0


class StreamHighlightDetector:
    """Highlight detection for ONE stream. All state here is per-instance."""

    _MAX_SEEN_IDS = 4096

    def __init__(
        self,
        stream_id: str,
        streamer_name: str,
        config: HighlightConfig,
        sink: HighlightSink = logging_sink,
    ) -> None:
        self.stream_id = stream_id
        self.streamer_name = streamer_name
        self.config = config
        self._sink = sink

        self._detector = ActivityDetector(config.detector)
        self._analyzer = SignalAnalyzer(config.window_seconds, config.emote_weight)
        horizon = config.pre_seconds + config.window_seconds + 2.0
        self._buffer = RollingChatBuffer(horizon)

        self._open: _OpenEvent | None = None
        self._seen_ids: set[str] = set()
        self._id_order: deque[str] = deque()
        # Sustained-activity tracking (consecutive elevated whole seconds):
        self._last_sec: int | None = None
        self._elevated_seconds: float = 0.0

    # -- public API -------------------------------------------------------------

    def process(self, message: ChatMessage, mono_now: float, wall_now: float) -> None:
        """Feed one chat message through the pipeline."""
        if self._is_duplicate(message.message_id):
            return

        self._buffer.add(mono_now, message)
        self._analyzer.add(mono_now, message.username, message.text, message.emote_count)
        self._update_sustained(mono_now)

        weight = self._analyzer.weight_for(message.emote_count)
        spike = self._detector.add_message(mono_now, wall_time=wall_now, weight=weight)

        opened_now = False
        if spike is not None:
            if self._open is None:
                self._open_event(spike, mono_now, wall_now)
                opened_now = True
            else:
                self._merge_event(mono_now)

        # Collect the current message into an open event (unless it was just used
        # to seed a fresh event, where the buffer slice already included it).
        if self._open is not None and not opened_now:
            if mono_now <= self._open.end_mono:
                self._open.messages.append(message)

        self._maybe_close(mono_now)

    def tick(self, mono_now: float, wall_now: float | None = None) -> None:
        """Close any open event whose collection window has elapsed.

        Call this periodically so events still finalize when chat goes quiet.
        """
        self._maybe_close(mono_now)

    # -- event lifecycle --------------------------------------------------------

    def _open_event(self, spike: SpikeEvent, mono_now: float, wall_now: float) -> None:
        cfg = self.config
        start_mono = mono_now - cfg.pre_seconds
        start_wall = wall_now - cfg.pre_seconds
        snapshot = self._analyzer.snapshot(mono_now)
        self._open = _OpenEvent(
            event_id=uuid.uuid4().hex,
            start_mono=start_mono,
            start_wall=start_wall,
            trigger_mono=mono_now,
            trigger_wall=wall_now,
            end_mono=mono_now + cfg.post_seconds,
            spike=spike,
            snapshot=snapshot,
            sustained_seconds=self._elevated_seconds,
            messages=list(self._buffer.since(start_mono)),  # pre-spike context + now
        )

    def _merge_event(self, mono_now: float) -> None:
        assert self._open is not None
        ev = self._open
        ev.merged_count += 1
        capped_end = ev.start_mono + self.config.max_event_seconds
        ev.end_mono = min(mono_now + self.config.post_seconds, capped_end)

    def _maybe_close(self, mono_now: float) -> None:
        if self._open is None or mono_now < self._open.end_mono:
            return
        event = self._finalize(self._open)
        self._open = None
        try:
            self._sink(event)
        except Exception:  # noqa: BLE001  a bad sink must not break detection
            log.exception("Highlight sink raised (detection continues).")

    def _finalize(self, oe: _OpenEvent) -> HighlightEvent:
        messages = oe.messages
        count = len(messages)
        usernames = Counter(m.username for m in messages if m.username)
        unique_chatters = len(usernames)
        emote_count = sum(m.emote_count for m in messages)
        unique_texts = len({normalize_text(m.text) for m in messages if m.text})

        denom = max(count, 1)
        diversity = unique_chatters / denom
        unique_text_ratio = unique_texts / denom
        repeat_ratio = 1.0 - unique_text_ratio
        emote_rate = emote_count / denom
        top_user_share = (max(usernames.values()) / denom) if usernames else 0.0

        intensity = oe.snapshot.messages_per_sec  # raw rate at the trigger
        activity_score = (
            intensity
            * diversity
            * unique_text_ratio
            * (1.0 + emote_rate * self.config.emote_weight)
        )

        triggered = ["rate_spike"]
        if oe.snapshot.emote_rate >= 1.0:
            triggered.append("emote_burst")
        if oe.sustained_seconds >= 3.0:
            triggered.append("sustained")
        if repeat_ratio >= 0.5 or top_user_share >= 0.5:
            triggered.append("spam_suspected")

        signals = DetectionSignals(
            messages_per_sec=oe.snapshot.messages_per_sec,
            baseline_rate=oe.spike.baseline,
            rate_ratio=oe.spike.multiplier,
            unique_chatters=unique_chatters,
            emote_count=emote_count,
            emote_rate=emote_rate,
            repeat_ratio=repeat_ratio,
            top_user_share=top_user_share,
            sustained_seconds=oe.sustained_seconds,
            triggered=triggered,
        )

        end_wall = oe.start_wall + (oe.end_mono - oe.start_mono)
        return HighlightEvent(
            stream_id=self.stream_id,
            streamer_name=self.streamer_name,
            event_id=oe.event_id,
            start_timestamp=oe.start_wall,
            end_timestamp=end_wall,
            trigger_timestamp=oe.trigger_wall,
            messages=messages,
            message_count=count,
            unique_chatters=unique_chatters,
            emote_count=emote_count,
            activity_score=activity_score,
            detection_signals=signals,
            merged_count=oe.merged_count,
        )

    # -- helpers ----------------------------------------------------------------

    def _is_duplicate(self, message_id: str | None) -> bool:
        if not message_id:
            return False
        if message_id in self._seen_ids:
            return True
        self._seen_ids.add(message_id)
        self._id_order.append(message_id)
        if len(self._id_order) > self._MAX_SEEN_IDS:
            old = self._id_order.popleft()
            self._seen_ids.discard(old)
        return False

    def _update_sustained(self, mono_now: float) -> None:
        sec = int(mono_now)
        if self._last_sec is None:
            self._last_sec = sec
            return
        if sec <= self._last_sec:
            return
        rate = self._detector.current_rate(mono_now)
        base = self._detector.baseline
        elevated = base > 0.0 and rate >= base * self.config.detector.baseline_freeze_ratio
        steps = sec - self._last_sec
        self._elevated_seconds = (self._elevated_seconds + steps) if elevated else 0.0
        self._last_sec = sec


class DetectionManager:
    """Routes messages to a per-stream detector, keeping streams independent."""

    def __init__(self, config: HighlightConfig, sink: HighlightSink = logging_sink) -> None:
        self._config = config
        self._sink = sink
        self._detectors: dict[str, StreamHighlightDetector] = {}

    def detector_for(self, stream_id: str, streamer_name: str) -> StreamHighlightDetector:
        det = self._detectors.get(stream_id)
        if det is None:
            det = StreamHighlightDetector(stream_id, streamer_name, self._config, self._sink)
            self._detectors[stream_id] = det
        return det

    def process(
        self,
        stream_id: str,
        streamer_name: str,
        message: ChatMessage,
        mono_now: float,
        wall_now: float,
    ) -> None:
        self.detector_for(stream_id, streamer_name).process(message, mono_now, wall_now)

    def tick_all(self, mono_now: float, wall_now: float | None = None) -> None:
        for det in self._detectors.values():
            det.tick(mono_now, wall_now)

    @property
    def stream_ids(self) -> list[str]:
        return list(self._detectors)
