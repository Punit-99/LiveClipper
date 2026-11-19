"""Deterministic offline async unit tests for HighlightAnalysisWorker & sinks."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.analysis.gemini_client import (
    GeminiAnalysisError,
    GeminiAuthError,
    GeminiRateLimitError,
)
from app.analysis.models import (
    AnalysisConfig,
    AnalysisStatus,
    AnalyzedHighlight,
    CompositeAnalysisSink,
    CompositeHighlightSink,
    GeminiHighlightVerdict,
    HighlightCategory,
    JsonlAnalysisSink,
)
from app.analysis.worker import HighlightAnalysisWorker
from app.detection import (
    ActivityDetectorConfig,
    ChatMessage,
    DetectionSignals,
    HighlightConfig,
    HighlightEvent,
    StreamHighlightDetector,
)


def _make_event(event_id: str, messages: list[ChatMessage] | None = None, stream_id: str = "s1") -> HighlightEvent:
    msgs = messages if messages is not None else [
        ChatMessage("u1", "wait watch this", 1005.0),
        ChatMessage("u2", "LMAOOOOO NO WAY", 1010.0),
        ChatMessage("u3", "CLIP IT!", 1011.0),
    ]
    return HighlightEvent(
        stream_id=stream_id,
        streamer_name=stream_id,
        event_id=event_id,
        start_timestamp=1000.0,
        end_timestamp=1030.0,
        trigger_timestamp=1010.0,
        messages=msgs,
        message_count=len(msgs),
        unique_chatters=len({m.username for m in msgs}),
        emote_count=0,
        activity_score=15.0,
        detection_signals=DetectionSignals(
            messages_per_sec=5.0,
            baseline_rate=1.0,
            rate_ratio=5.0,
            unique_chatters=len({m.username for m in msgs}),
            triggered=["rate_spike"],
        ),
    )


def _ok_result(event: HighlightEvent) -> AnalyzedHighlight:
    return AnalyzedHighlight.from_event(
        event,
        status=AnalysisStatus.ANALYZED,
        model_used="gemini-3.1-flash-lite",
        verdict=GeminiHighlightVerdict(
            is_highlight=True,
            should_clip=True,
            highlight_score=85,
            confidence=0.9,
            category=HighlightCategory.FUNNY_MOMENT,
            is_spam_or_false_positive=False,
            title="Chat laughs at unexpected moment",
            reasoning="Viewers immediately reacted with laughter and clip requests.",
            reaction_summary="Laughter and hype",
            key_quotes=["LMAOOOOO NO WAY", "CLIP IT!"],
        ),
        latency_ms=120.0,
    )


class _StubAnalyzer:
    def __init__(self, handler=None) -> None:
        self._handler = handler
        self.calls: list[str] = []
        self.closed = False

    async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
        self.calls.append(event.event_id)
        if self._handler is not None:
            return await self._handler(event)
        return _ok_result(event)

    async def close(self) -> None:
        self.closed = True


class TestAnalysisWorker(unittest.IsolatedAsyncioTestCase):
    async def test_submit_is_non_blocking_and_processes_concurrently(self):
        gate = asyncio.Event()
        in_flight = 0
        max_in_flight = 0

        async def _slow_handler(event: HighlightEvent) -> AnalyzedHighlight:
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await gate.wait()
            in_flight -= 1
            return _ok_result(event)

        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer(_slow_handler)
        cfg = AnalysisConfig(queue_max_size=10, max_concurrency=2)
        worker = HighlightAnalysisWorker(analyzer, cfg, sink=results.append)
        await worker.start()
        try:
            # Synchronous submit must return immediately while analyzer is blocked on gate.
            self.assertTrue(worker.submit(_make_event("e1")))
            self.assertTrue(worker.submit(_make_event("e2")))
            await asyncio.sleep(0.02)
            self.assertEqual(max_in_flight, 2)
            gate.set()
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual({r.event_id for r in results}, {"e1", "e2"})
        self.assertTrue(analyzer.closed)

    async def test_queue_full_evicts_oldest_and_keeps_newest(self):
        # Worker has NOT pulled yet while gate blocks the single worker slot.
        gate = asyncio.Event()
        started_e0 = asyncio.Event()

        async def _blocking(event: HighlightEvent) -> AnalyzedHighlight:
            if event.event_id == "e0":
                started_e0.set()
            await gate.wait()
            return _ok_result(event)

        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer(_blocking)
        cfg = AnalysisConfig(queue_max_size=2, max_concurrency=1)
        worker = HighlightAnalysisWorker(analyzer, cfg, sink=results.append)
        await worker.start()
        try:
            # e0 is picked up by the single worker and blocks on gate.
            worker.submit(_make_event("e0"))
            await asyncio.wait_for(started_e0.wait(), timeout=1.0)

            # Queue holds [e1, e2] (now full at maxsize=2).
            worker.submit(_make_event("e1"))
            worker.submit(_make_event("e2"))
            # Submitting e3 must evict oldest (e1) and KEEP newest (e3)!
            self.assertTrue(worker.submit(_make_event("e3")))
            gate.set()
        finally:
            await worker.stop(drain_timeout=2.0)

        processed_ids = [r.event_id for r in results]
        self.assertIn("e0", processed_ids)
        self.assertNotIn("e1", processed_ids)
        self.assertIn("e2", processed_ids)
        self.assertIn("e3", processed_ids)
        self.assertEqual(len(worker.dropped_events), 1)
        self.assertEqual(worker.dropped_events[0].event_id, "e1")
        self.assertEqual(worker.dropped_events[0].reason, "queue_full_evicted_oldest")

    async def test_duplicate_event_id_skipped(self):
        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer()
        worker = HighlightAnalysisWorker(analyzer, AnalysisConfig(), sink=results.append)
        await worker.start()
        try:
            self.assertTrue(worker.submit(_make_event("dup-1")))
            self.assertFalse(worker.submit(_make_event("dup-1")))
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual(len(results), 1)

    async def test_prefilter_skips_bot_commands_without_calling_gemini(self):
        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer()
        worker = HighlightAnalysisWorker(analyzer, AnalysisConfig(), sink=results.append)
        await worker.start()
        try:
            bot_msgs = [ChatMessage(f"u{i}", "!giveaway", 1010.0 + i * 0.1) for i in range(10)]
            worker.submit(_make_event("bot-ev", messages=bot_msgs))
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual(analyzer.calls, [])
        self.assertEqual(worker.prefilter_skipped_count, 1)
        self.assertEqual(results[0].status, AnalysisStatus.SKIPPED_PREFILTER)
        self.assertEqual(results[0].skip_or_error_reason, "bot_command_spam")

    async def test_auth_error_trips_circuit_breaker(self):
        async def _auth_fail(_event: HighlightEvent):
            raise GeminiAuthError("bad key")

        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer(_auth_fail)
        cfg = AnalysisConfig(max_concurrency=1)
        worker = HighlightAnalysisWorker(analyzer, cfg, sink=results.append)
        await worker.start()
        try:
            worker.submit(_make_event("e1"))
            await asyncio.sleep(0.03)
            # After circuit breaker trips, subsequent submissions are dropped immediately.
            self.assertFalse(worker.submit(_make_event("e2")))
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual(analyzer.calls, ["e1"])
        self.assertEqual(results[0].status, AnalysisStatus.FAILED)
        self.assertTrue(any(d.event_id == "e2" and d.reason == "gemini_auth_disabled" for d in worker.dropped_events))

    async def test_rate_limit_cooldown_and_per_minute_budget(self):
        now = 100.0
        async def _rate_fail(_event: HighlightEvent):
            raise GeminiRateLimitError("429")

        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer(_rate_fail)
        cfg = AnalysisConfig(max_concurrency=1, rate_limit_cooldown_seconds=30.0)
        worker = HighlightAnalysisWorker(
            analyzer, cfg, sink=results.append, clock=lambda: now
        )
        await worker.start()
        try:
            worker.submit(_make_event("e1"))
            await asyncio.sleep(0.03)
            # Still inside 30s cooldown -> skipped without hitting Gemini.
            worker.submit(_make_event("e2"))
            await asyncio.sleep(0.03)
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual(analyzer.calls, ["e1"])
        self.assertEqual(len(results), 2)
        self.assertIn("rate_limit_cooldown", results[1].skip_or_error_reason)

    async def test_calls_per_minute_ceiling_enforced(self):
        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer()
        cfg = AnalysisConfig(max_concurrency=1, max_calls_per_minute=2)
        worker = HighlightAnalysisWorker(
            analyzer, cfg, sink=results.append, clock=lambda: 500.0
        )
        await worker.start()
        try:
            worker.submit(_make_event("e1"))
            worker.submit(_make_event("e2"))
            worker.submit(_make_event("e3"))
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual(analyzer.calls, ["e1", "e2"])
        by_id = {r.event_id: r for r in results}
        self.assertEqual(by_id["e3"].status, AnalysisStatus.FAILED)
        self.assertEqual(by_id["e3"].skip_or_error_reason, "calls_per_minute_budget_exceeded")

    async def test_jsonl_persistence_and_sink_fault_isolation(self):
        def _broken_sink(_result: AnalyzedHighlight) -> None:
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "highlights.jsonl"
            composite = CompositeAnalysisSink(_broken_sink, JsonlAnalysisSink(out_path))
            analyzer = _StubAnalyzer()
            worker = HighlightAnalysisWorker(analyzer, AnalysisConfig(), sink=composite)
            await worker.start()
            try:
                worker.submit(_make_event("persist-1"))
            finally:
                await worker.stop(drain_timeout=2.0)

            lines = out_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["event_id"], "persist-1")
            self.assertEqual(record["stream_id"], "s1")
            self.assertEqual(record["highlight_score"], 85)
            self.assertEqual(record["confidence"], 0.9)
            self.assertEqual(record["category"], "funny_moment")
            self.assertTrue(record["should_clip"])
            self.assertIn("analyzed_at", record)
            self.assertIn("analyzed_at_iso", record)

    async def test_integration_with_stream_highlight_detector_via_composite_sink(self):
        # Verify the unmodified detection engine feeds the async worker seamlessly.
        seen_candidates: list[HighlightEvent] = []
        analyzed: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer()
        worker = HighlightAnalysisWorker(analyzer, AnalysisConfig(), sink=analyzed.append)
        await worker.start()

        det_cfg = HighlightConfig(
            detector=ActivityDetectorConfig(
                window_seconds=10.0,
                spike_multiplier=3.0,
                min_messages=10,
                cooldown_seconds=15.0,
                warmup_seconds=30.0,
            ),
            pre_seconds=5.0,
            post_seconds=10.0,
        )
        detector = StreamHighlightDetector(
            stream_id="gta_ch",
            streamer_name="gta_ch",
            config=det_cfg,
            sink=CompositeHighlightSink(seen_candidates.append, worker),
        )

        # Feed 40s baseline + 10s spike, then tick to finalize.
        mid = 0
        for sec in range(40):
            for u in range(10):
                mid += 1
                detector.process(ChatMessage(f"u{u}", f"normal {mid}", float(sec), 0, f"m{mid}"), float(sec), float(sec))
        for sec in range(40, 50):
            for u in range(40):
                mid += 1
                detector.process(ChatMessage(f"u{u}", f"OMG NO WAY {mid}", float(sec), 1, f"m{mid}"), float(sec), float(sec))
        detector.tick(100.0)

        await worker.stop(drain_timeout=2.0)

        self.assertEqual(len(seen_candidates), 1)
        self.assertEqual(len(analyzed), 1)
        self.assertEqual(analyzed[0].event_id, seen_candidates[0].event_id)
        self.assertEqual(analyzed[0].status, AnalysisStatus.ANALYZED)

    async def test_analyzer_exception_does_not_crash_worker(self):
        async def _flaky(event: HighlightEvent) -> AnalyzedHighlight:
            if event.event_id == "bad":
                raise GeminiAnalysisError("temporary failure")
            return _ok_result(event)

        results: list[AnalyzedHighlight] = []
        analyzer = _StubAnalyzer(_flaky)
        worker = HighlightAnalysisWorker(analyzer, AnalysisConfig(max_concurrency=1), sink=results.append)
        await worker.start()
        try:
            worker.submit(_make_event("bad"))
            worker.submit(_make_event("good"))
        finally:
            await worker.stop(drain_timeout=2.0)

        by_id = {r.event_id: r for r in results}
        self.assertEqual(by_id["bad"].status, AnalysisStatus.FAILED)
        self.assertEqual(by_id["good"].status, AnalysisStatus.ANALYZED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
