"""Stage 5.4: Non-blocking `Stage5PinAndSubmitSink` and `ClipExtractionWorker`.

Clean integration seams that plug into Stages 1–4 without modifying detection
or Gemini worker internals:
- `Stage5PinAndSubmitSink` wraps `analysis_worker.submit(event)` as a
  synchronous `HighlightSink`:
  * Plans the clip window from the full `HighlightEvent` (while `messages` and
    `merged_count` are available).
  * Submits to Stage 4 Gemini analysis FIRST; pins segments in
    `RollingBufferManager` ONLY if Stage 4 accepted the event (zero orphaned
    pins). If buffer capacity is exhausted (`BufferCapacityError`), logs a
    warning and lets Stage 4 Gemini analysis continue normally.
- `ClipExtractionWorker` plugs into `CompositeAnalysisSink`:
  * Receives EVERY `AnalyzedHighlight` (`ANALYZED`, `SKIPPED_PREFILTER`, `FAILED`).
  * Immediately unpins rejected/skipped/failed events.
  * For `should_clip=True`, immediately promotes the pin to `APPROVED` (never
    TTL-expired or auto-evicted), queues extraction non-blocking, promotes to
    `EXTRACTING`, waits briefly only if trailing post-roll is still flushing,
    runs async `ClipExtractor`, and ALWAYS unpins in `finally`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Protocol

from app.analysis.models import AnalysisStatus, AnalyzedHighlight
from app.detection.highlight_event import HighlightEvent
from app.logging_setup import get_logger
from app.recording.buffer_manager import BufferCapacityError, RollingBufferManager
from app.recording.clip_extractor import ClipExtractionResult
from app.recording.clip_planner import ClipWindowPlanner
from app.recording.models import ClipWindowPlan, SegmentSelection

log = get_logger(__name__)


class AsyncClipExtractor(Protocol):
    """Minimal async extractor protocol (satisfied by `ClipExtractor`)."""

    async def extract(
        self,
        analysis: AnalyzedHighlight,
        plan: ClipWindowPlan,
        selection: SegmentSelection,
        platform: str | None = None,
    ) -> ClipExtractionResult: ...


class Stage5PinAndSubmitSink:
    """Synchronous `HighlightSink` that submits to Stage 4 and pins footage if accepted."""

    def __init__(
        self,
        submit_to_analysis: Callable[[HighlightEvent], bool],
        planner: ClipWindowPlanner,
        buffer_manager: RollingBufferManager,
    ) -> None:
        self._submit = submit_to_analysis
        self._planner = planner
        self._buffer_manager = buffer_manager

        self.pinned_count: int = 0
        self.pin_rejected_capacity_count: int = 0
        self.analysis_rejected_count: int = 0

    def __call__(self, event: HighlightEvent) -> bool:
        """Submit `event` to Gemini analysis and pin its rolling buffer window if accepted."""
        plan = self._planner.plan(event)
        accepted = self._submit(event)
        if not accepted:
            self.analysis_rejected_count += 1
            return False

        try:
            self._buffer_manager.pin_event(
                event.event_id,
                plan.requested_clip_start,
                plan.requested_clip_end,
                plan=plan,
            )
            self.pinned_count += 1
        except BufferCapacityError as exc:
            self.pin_rejected_capacity_count += 1
            log.warning(
                "Could not pin rolling buffer for event %s (Gemini analysis continues): %s",
                event.event_id[:8],
                exc,
            )
        return True


class ClipExtractionWorker:
    """Non-blocking `AnalysisSink` bridging approved Gemini verdicts to `ClipExtractor`."""

    def __init__(
        self,
        extractor: AsyncClipExtractor,
        buffer_manager: RollingBufferManager,
        *,
        queue_max_size: int = 16,
        max_concurrency: int = 1,
        extraction_semaphore: asyncio.Semaphore | None = None,
        trailing_wait_seconds: float = 15.0,
        trailing_poll_seconds: float = 0.5,
        mono_clock: Callable[[], float] = time.monotonic,
        on_extracted: Callable[[ClipExtractionResult], None] | None = None,
        platform: str | None = None,
    ) -> None:
        if queue_max_size < 1:
            raise ValueError("queue_max_size must be >= 1")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if trailing_wait_seconds < 0:
            raise ValueError("trailing_wait_seconds must be >= 0")
        if trailing_poll_seconds <= 0:
            raise ValueError("trailing_poll_seconds must be > 0")

        self._extractor = extractor
        self._buffer_manager = buffer_manager
        self._max_concurrency = max_concurrency
        self._extraction_semaphore = extraction_semaphore
        self._trailing_wait = trailing_wait_seconds
        self._trailing_poll = trailing_poll_seconds
        self._mono_clock = mono_clock
        self._on_extracted = on_extracted
        self._platform = platform

        self._queue: asyncio.Queue[tuple[AnalyzedHighlight, ClipWindowPlan]] = asyncio.Queue(
            maxsize=queue_max_size
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._accepting = False

        # Operational counters.
        self.queued_count: int = 0
        self.extracted_count: int = 0
        self.skipped_count: int = 0
        self.failed_count: int = 0
        self.rejected_unpinned_count: int = 0
        self.dropped_count: int = 0

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        """Start background clip extraction worker tasks."""
        if self._running:
            return
        self._running = True
        self._accepting = True
        self._tasks = [
            asyncio.create_task(self._worker_loop(idx), name=f"clip-extractor-{idx}")
            for idx in range(self._max_concurrency)
        ]
        log.info(
            "ClipExtractionWorker started (concurrency=%d, queue_max=%d).",
            self._max_concurrency,
            self._queue.maxsize,
        )

    async def stop(self, *, drain_timeout: float = 30.0) -> None:
        """Stop accepting new clip jobs, drain active extraction, and shut down cleanly."""
        if not self._running:
            return

        self._accepting = False
        if drain_timeout > 0:
            try:
                await asyncio.wait_for(self._queue.join(), timeout=drain_timeout)
            except asyncio.TimeoutError:
                log.warning(
                    "ClipExtractionWorker shutdown drain timed out after %.1fs with %d job(s) left.",
                    drain_timeout,
                    self._queue.qsize(),
                )

        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        # Release pins for any items still sitting unstarted in the queue after forced timeout.
        while not self._queue.empty():
            try:
                analysis, _ = self._queue.get_nowait()
                self._queue.task_done()
                self._buffer_manager.unpin_event(analysis.event_id)
            except asyncio.QueueEmpty:
                break

        log.info(
            "ClipExtractionWorker stopped (queued=%d, extracted=%d, skipped=%d, "
            "failed=%d, rejected_unpinned=%d, dropped=%d).",
            self.queued_count,
            self.extracted_count,
            self.skipped_count,
            self.failed_count,
            self.rejected_unpinned_count,
            self.dropped_count,
        )

    # -- synchronous AnalysisSink interface ------------------------------------

    def __call__(self, result: AnalyzedHighlight) -> None:
        """Allow direct use inside `CompositeAnalysisSink`."""
        self.submit(result)

    def submit(self, result: AnalyzedHighlight) -> bool:
        """Handle an `AnalyzedHighlight` without blocking.

        - Non-clip / skipped / failed verdicts immediately unpin their segments.
        - `should_clip=True` verdicts immediately promote their pin to `APPROVED`
          and enqueue extraction with `put_nowait()`.
        """
        should_clip = (
            result.status == AnalysisStatus.ANALYZED
            and result.verdict is not None
            and result.verdict.should_clip
        )
        if not should_clip:
            self._buffer_manager.unpin_event(result.event_id)
            self.rejected_unpinned_count += 1
            return False

        pin = self._buffer_manager.get_pin(result.event_id)
        if pin is None or not pin.is_active or pin.plan is None:
            log.warning(
                "Approved highlight %s has no active rolling-buffer pin; skipping extraction.",
                result.event_id[:8],
            )
            self.skipped_count += 1
            return False

        if not self._accepting:
            self._buffer_manager.unpin_event(result.event_id)
            self.dropped_count += 1
            log.warning(
                "ClipExtractionWorker not accepting jobs; unpinned approved event %s.",
                result.event_id[:8],
            )
            return False

        # Promote to APPROVED BEFORE enqueuing so TTL/disk cleanup never evicts it!
        self._buffer_manager.mark_pin_approved(result.event_id)

        try:
            self._queue.put_nowait((result, pin.plan))
        except asyncio.QueueFull:
            self._buffer_manager.unpin_event(result.event_id)
            self.dropped_count += 1
            log.error(
                "Clip extraction queue full (%d); dropped event %s and released pin.",
                self._queue.maxsize,
                result.event_id[:8],
            )
            return False

        self.queued_count += 1
        return True

    # -- worker loop -----------------------------------------------------------

    async def _worker_loop(self, worker_idx: int) -> None:
        while True:
            analysis, plan = await self._queue.get()
            try:
                await self._process_job(analysis, plan)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  isolate faults per job
                self.failed_count += 1
                log.exception(
                    "ClipExtractionWorker-%d failed on event %s (continuing).",
                    worker_idx,
                    analysis.event_id[:8],
                )
            finally:
                # Guarantee pin release across success, skip, failure, timeout, or cancel.
                self._buffer_manager.unpin_event(analysis.event_id)
                self._queue.task_done()

    async def _process_job(
        self, analysis: AnalyzedHighlight, plan: ClipWindowPlan
    ) -> None:
        self._buffer_manager.mark_pin_extracting(analysis.event_id)
        await self._wait_for_trailing_segments(plan.requested_clip_end)

        selection = self._buffer_manager.query_range(
            plan.requested_clip_start, plan.requested_clip_end
        )
        kwargs = {}
        if self._platform is not None:
            kwargs["platform"] = self._platform

        try:
            if self._extraction_semaphore is not None:
                async with self._extraction_semaphore:
                    outcome = await self._extractor.extract(analysis, plan, selection, **kwargs)
            else:
                outcome = await self._extractor.extract(analysis, plan, selection, **kwargs)
        except TypeError:
            if self._extraction_semaphore is not None:
                async with self._extraction_semaphore:
                    outcome = await self._extractor.extract(analysis, plan, selection)
            else:
                outcome = await self._extractor.extract(analysis, plan, selection)

        if outcome.status == "extracted":
            self.extracted_count += 1
        else:
            self.skipped_count += 1

        if self._on_extracted is not None:
            try:
                self._on_extracted(outcome)
            except Exception:  # noqa: BLE001
                log.exception("on_extracted callback raised.")

    async def _wait_for_trailing_segments(self, target_end_wall: float) -> None:
        """Wait briefly ONLY if `target_end_wall` has not been flushed by the recorder yet."""
        if self._trailing_wait <= 0:
            return

        deadline = self._mono_clock() + self._trailing_wait
        while self._running and not self._buffer_manager.emergency_stop_triggered:
            segs = self._buffer_manager.index.segments
            if segs and segs[-1].approx_end_wall >= target_end_wall:
                return
            remaining = deadline - self._mono_clock()
            if remaining <= 0:
                return
            await asyncio.sleep(min(self._trailing_poll, remaining))
