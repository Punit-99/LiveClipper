"""Non-blocking asyncio queue & worker pool bridging HighlightSink to Gemini.

How it protects Twitch chat collection:
- `StreamHighlightDetector` calls `worker.submit(event)` synchronously.
- `submit()` does O(1) in-memory queue insertion (`put_nowait`), NEVER awaiting
  network I/O.
- If the bounded queue is full, `submit()` evicts the OLDEST queued event so
  fresher live moments are preserved instead of blindly dropping the newest
  candidate, and logs the dropped event's ID and reason.
- Background worker tasks run pre-filtering, rate-budget checks, and async
  Gemini analysis concurrently (bounded by `max_concurrency`), isolating all
  exceptions so chat monitoring continues even if Gemini is slow or down.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Callable

from app.analysis.gemini_client import (
    GeminiAnalysisError,
    GeminiAuthError,
    GeminiRateLimitError,
    HighlightAnalyzer,
)
from app.analysis.models import (
    AnalysisConfig,
    AnalysisSink,
    AnalysisStatus,
    AnalyzedHighlight,
    logging_analysis_sink,
)
from app.analysis.prefilter import evaluate_prefilter
from app.detection.highlight_event import HighlightEvent
from app.logging_setup import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class DroppedEventRecord:
    """Audit record for a candidate event dropped before reaching Gemini."""

    event_id: str
    stream_id: str
    reason: str


class _SlidingRateLimiter:
    """Enforces a hard ceiling on Gemini calls per rolling 60-second window."""

    def __init__(self, max_calls_per_minute: int) -> None:
        self._limit = max_calls_per_minute
        self._timestamps: deque[float] = deque()

    def try_acquire(self, now: float) -> bool:
        cutoff = now - 60.0
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._limit:
            return False
        self._timestamps.append(now)
        return True


class HighlightAnalysisWorker:
    """Async producer-consumer bridge from `HighlightSink` to `HighlightAnalyzer`.

    Instances are directly callable (`worker(event)` == `worker.submit(event)`),
    so a worker can be passed anywhere a synchronous `HighlightSink` is expected.
    """

    _MAX_SEEN_EVENT_IDS = 2048

    def __init__(
        self,
        analyzer: HighlightAnalyzer,
        config: AnalysisConfig,
        sink: AnalysisSink = logging_analysis_sink,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        on_event_dropped: Callable[[HighlightEvent, str], None] | None = None,
    ) -> None:
        self._analyzer = analyzer
        self._config = config
        self._sink = sink
        self._clock = clock
        self._wall_clock = wall_clock
        self._on_event_dropped = on_event_dropped

        self._queue: asyncio.Queue[HighlightEvent] = asyncio.Queue(
            maxsize=config.queue_max_size
        )
        self._queued_per_stream: Counter[str] = Counter()
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._accepting = False

        # Event-ID deduplication (bounded).
        self._seen_ids: set[str] = set()
        self._id_order: deque[str] = deque()

        # Rate limiting & failure protection.
        self._rate_limiter = _SlidingRateLimiter(config.max_calls_per_minute)
        self._auth_disabled = False
        self._cooldown_until: float = 0.0

        # Operational counters (useful for logging and tests).
        self.submitted_count: int = 0
        self.analyzed_count: int = 0
        self.prefilter_skipped_count: int = 0
        self.failed_count: int = 0
        self.dropped_events: list[DroppedEventRecord] = []

    # -- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        """Start background worker tasks."""
        if self._running:
            return
        self._running = True
        self._accepting = True
        self._tasks = [
            asyncio.create_task(self._worker_loop(idx), name=f"gemini-worker-{idx}")
            for idx in range(self._config.max_concurrency)
        ]
        log.info(
            "Gemini analysis worker started (model=%s, concurrency=%d, queue_max=%d, "
            "max_calls_per_min=%d).",
            self._config.model,
            self._config.max_concurrency,
            self._config.queue_max_size,
            self._config.max_calls_per_minute,
        )

    async def stop(self, *, drain_timeout: float = 5.0) -> None:
        """Stop accepting new events, briefly drain in-flight work, and close client."""
        if not self._running:
            await self._analyzer.close()
            return

        self._accepting = False
        if drain_timeout > 0:
            try:
                # join() returns immediately if no items are queued OR in-flight,
                # and otherwise waits until all queued and in-flight work finishes.
                await asyncio.wait_for(self._queue.join(), timeout=drain_timeout)
            except asyncio.TimeoutError:
                log.warning(
                    "Shutdown drain timed out after %.1fs with %d queued event(s) remaining.",
                    drain_timeout,
                    self._queue.qsize(),
                )

        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Drain any unstarted queued events so callers (e.g. rolling-buffer pins)
        # receive drop notifications cleanly instead of waiting for TTL expiry.
        while not self._queue.empty():
            try:
                leftover = self._queue.get_nowait()
                self._queue.task_done()
                self._decrement_stream_queued(leftover.stream_id)
                self._record_drop(leftover, "shutdown_drain_timeout")
            except asyncio.QueueEmpty:
                break

        await self._analyzer.close()
        log.info(
            "Gemini analysis worker stopped (submitted=%d, analyzed=%d, "
            "prefilter_skipped=%d, failed=%d, dropped=%d).",
            self.submitted_count,
            self.analyzed_count,
            self.prefilter_skipped_count,
            self.failed_count,
            len(self.dropped_events),
        )

    # -- synchronous HighlightSink producer ------------------------------------

    def __call__(self, event: HighlightEvent) -> None:
        """Allow the worker to be used directly as a synchronous `HighlightSink`."""
        self.submit(event)

    def submit(self, event: HighlightEvent) -> bool:
        """Enqueue a finalized `HighlightEvent` without blocking.

        Returns True if the event was queued, False if skipped/dropped.
        """
        if not self._accepting:
            self._record_drop(event, "worker_not_running")
            return False

        if self._auth_disabled:
            self._record_drop(event, "gemini_auth_disabled")
            return False

        if event.event_id and event.event_id in self._seen_ids:
            log.info(
                "Skipping duplicate candidate event id=%s stream=#%s.",
                event.event_id[:8],
                event.stream_id,
            )
            return False

        # Per-stream fairness guard: prevents one hyper-active streamer from
        # filling the shared queue and starving other active streamers.
        per_stream_cap = self._config.max_queued_per_stream
        if (
            per_stream_cap is not None
            and self._queued_per_stream[event.stream_id] >= per_stream_cap
        ):
            self._record_drop(event, "per_stream_queue_limit_reached")
            return False

        # Bounded queue backpressure: when full, evict the OLDEST queued event
        # so newer live moments are kept rather than blindly dropping fresh events.
        if self._queue.full():
            try:
                oldest = self._queue.get_nowait()
                self._queue.task_done()
                self._decrement_stream_queued(oldest.stream_id)
                self._record_drop(oldest, "queue_full_evicted_oldest")
            except asyncio.QueueEmpty:
                pass

        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            # Rare race fallback if the queue refilled concurrently.
            self._record_drop(event, "queue_full_insert_failed")
            return False

        self._remember_event_id(event.event_id)
        self._queued_per_stream[event.stream_id] += 1
        self.submitted_count += 1
        return True

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def queued_count_for_stream(self, stream_id: str) -> int:
        """Return how many events for `stream_id` are currently waiting in the queue."""
        return self._queued_per_stream.get(stream_id, 0)

    # -- worker loop ------------------------------------------------------------

    async def _worker_loop(self, worker_idx: int) -> None:
        while True:
            event = await self._queue.get()
            self._decrement_stream_queued(event.stream_id)
            try:
                await self._handle_event(event)
            except asyncio.CancelledError:
                # Notify callers (e.g. rolling-buffer pin cleanup) exactly once
                # for an in-flight event aborted by shutdown drain timeout.
                self._record_drop(event, "shutdown_drain_timeout")
                raise
            except Exception:  # noqa: BLE001  never let an unexpected error kill a worker
                log.exception(
                    "Unexpected error in Gemini worker-%d on event %s.",
                    worker_idx,
                    event.event_id[:8],
                )
                self._record_drop(event, "internal_worker_error")
            finally:
                self._queue.task_done()

    async def _handle_event(self, event: HighlightEvent) -> None:
        """Process one accepted event and emit exactly one terminal sink record.

        Terminal notification contract:
        - Every event completing `_handle_event` emits to `self._sink` ONLY
          (`ANALYZED`, `SKIPPED_PREFILTER`, or `FAILED`) so downstream pin/sink
          consumers are never double-notified.
        - Events that never reach or finish `_handle_event` (eviction,
          shutdown cancellation/drain timeout, or synchronous `submit()`
          rejection) invoke `on_event_dropped` once instead.
        """
        # 1) Cheap local pre-filter ($0 cost).
        decision = evaluate_prefilter(event)
        if not decision.should_analyze:
            self.prefilter_skipped_count += 1
            result = AnalyzedHighlight.from_event(
                event,
                status=AnalysisStatus.SKIPPED_PREFILTER,
                skip_or_error_reason=decision.reason,
                analyzed_at=self._wall_clock(),
            )
            self._emit(result)
            return

        # 2) Auth circuit breaker (event was queued before auth failed).
        if self._auth_disabled:
            self.failed_count += 1
            log.warning(
                "Skipping queued event id=%s stream=#%s: gemini_auth_disabled",
                event.event_id[:8],
                event.stream_id,
            )
            self._emit(
                AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.FAILED,
                    model_used=self._config.model,
                    skip_or_error_reason="gemini_auth_disabled",
                    analyzed_at=self._wall_clock(),
                )
            )
            return

        # 3) Cooldown after sustained 429 rate limits.
        now = self._clock()
        if now < self._cooldown_until:
            remaining = self._cooldown_until - now
            reason = f"rate_limit_cooldown ({remaining:.0f}s remaining)"
            self.failed_count += 1
            log.warning(
                "Skipping event id=%s stream=#%s: %s",
                event.event_id[:8],
                event.stream_id,
                reason,
            )
            self._emit(
                AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.FAILED,
                    model_used=self._config.model,
                    skip_or_error_reason=reason,
                    analyzed_at=self._wall_clock(),
                )
            )
            return

        # 4) Hard calls-per-minute budget ceiling.
        if not self._rate_limiter.try_acquire(now):
            reason = "calls_per_minute_budget_exceeded"
            self.failed_count += 1
            log.warning(
                "Skipping event id=%s stream=#%s: %s",
                event.event_id[:8],
                event.stream_id,
                reason,
            )
            self._emit(
                AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.FAILED,
                    model_used=self._config.model,
                    skip_or_error_reason=reason,
                    analyzed_at=self._wall_clock(),
                )
            )
            return

        # 5) Async Gemini API call.
        try:
            result = await self._analyzer.analyze(event)
            self.analyzed_count += 1
            self._emit(result)
        except GeminiAuthError as exc:
            self.failed_count += 1
            if not self._auth_disabled:
                self._auth_disabled = True
                log.error(
                    "Disabling Gemini analysis for this session due to auth error: %s",
                    exc,
                )
            self._emit(
                AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.FAILED,
                    model_used=self._config.model,
                    skip_or_error_reason=str(exc),
                    analyzed_at=self._wall_clock(),
                )
            )
        except GeminiRateLimitError as exc:
            self.failed_count += 1
            self._cooldown_until = self._clock() + self._config.rate_limit_cooldown_seconds
            log.warning(
                "Gemini rate limit hit on event %s; pausing calls for %.0fs (%s).",
                event.event_id[:8],
                self._config.rate_limit_cooldown_seconds,
                exc,
            )
            self._emit(
                AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.FAILED,
                    model_used=self._config.model,
                    skip_or_error_reason=str(exc),
                    analyzed_at=self._wall_clock(),
                )
            )
        except (GeminiAnalysisError, Exception) as exc:  # noqa: BLE001
            self.failed_count += 1
            log.warning(
                "Gemini analysis failed for event %s (chat continues): %s",
                event.event_id[:8],
                exc,
            )
            self._emit(
                AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.FAILED,
                    model_used=self._config.model,
                    skip_or_error_reason=str(exc),
                    analyzed_at=self._wall_clock(),
                )
            )

    # -- helpers ----------------------------------------------------------------

    def _emit(self, result: AnalyzedHighlight) -> None:
        try:
            self._sink(result)
        except Exception:  # noqa: BLE001  a failing sink must not break workers
            log.exception("Analysis sink raised (worker continues).")

    def _record_drop(self, event: HighlightEvent, reason: str) -> None:
        self.dropped_events.append(
            DroppedEventRecord(
                event_id=event.event_id,
                stream_id=event.stream_id,
                reason=reason,
            )
        )
        log.warning(
            "Dropped candidate event id=%s stream=#%s reason=%s",
            event.event_id[:8],
            event.stream_id,
            reason,
        )
        if self._on_event_dropped is not None:
            try:
                self._on_event_dropped(event, reason)
            except Exception:  # noqa: BLE001  drop hook faults must not break worker
                log.exception("on_event_dropped callback raised.")

    def _decrement_stream_queued(self, stream_id: str) -> None:
        remaining = self._queued_per_stream.get(stream_id, 0) - 1
        if remaining <= 0:
            self._queued_per_stream.pop(stream_id, None)
        else:
            self._queued_per_stream[stream_id] = remaining

    def _remember_event_id(self, event_id: str) -> None:
        if not event_id or event_id in self._seen_ids:
            return
        self._seen_ids.add(event_id)
        self._id_order.append(event_id)
        if len(self._id_order) > self._MAX_SEEN_EVENT_IDS:
            old = self._id_order.popleft()
            self._seen_ids.discard(old)
