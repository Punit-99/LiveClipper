"""Offline unit & async tests for Stage 6.2 StreamSession & StreamRoutingAnalysisSink."""

from __future__ import annotations

import asyncio
import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from app.analysis.models import (
    AnalysisConfig,
    AnalysisStatus,
    AnalyzedHighlight,
    CompositeAnalysisSink,
    GeminiHighlightVerdict,
    HighlightCategory,
)
from app.analysis.worker import HighlightAnalysisWorker
from app.config import Settings
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent
from app.multistream import (
    SessionState,
    StreamRoutingAnalysisSink,
    StreamSession,
)
from app.recording import (
    PipelineProcesses,
    VideoSegment,
)


def _settings(root: Path, *, recording_enabled: bool = False) -> Settings:
    return Settings(
        client_id="cid",
        client_secret="csec",
        redirect_uri="http://localhost:3000",
        category_name="Grand Theft Auto VI",
        max_streams=20,
        stream_language=None,
        spike_window_seconds=10.0,
        spike_multiplier=3.0,
        spike_min_messages=10,
        spike_cooldown_seconds=15.0,
        spike_warmup_seconds=30.0,
        event_pre_seconds=10.0,
        event_post_seconds=20.0,
        emote_weight=0.5,
        max_event_seconds=120.0,
        highlights_output_path=root / "highlights.jsonl",
        recording_enabled=recording_enabled,
        recording_buffer_dir=root / ".buffer",
        clips_output_dir=root / "clips",
    )


def _make_event(event_id: str, stream_id: str) -> HighlightEvent:
    msgs = [
        ChatMessage("u1", "wait watch this", 1005.0),
        ChatMessage("u2", "LMAOOOOO NO WAY", 1010.0, emote_count=2),
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
        unique_chatters=3,
        emote_count=2,
        activity_score=15.0,
        detection_signals=DetectionSignals(triggered=["rate_spike"]),
    )


def _ok_analysis(event: HighlightEvent, *, should_clip: bool = True) -> AnalyzedHighlight:
    return AnalyzedHighlight.from_event(
        event,
        status=AnalysisStatus.ANALYZED,
        model_used="gemini-3.1-flash-lite",
        verdict=GeminiHighlightVerdict(
            is_highlight=should_clip,
            should_clip=should_clip,
            highlight_score=88 if should_clip else 20,
            confidence=0.92,
            category=HighlightCategory.FUNNY_MOMENT,
            is_spam_or_false_positive=False,
            title="Big moment" if should_clip else "Skip",
            reasoning="Chat reacted strongly.",
            reaction_summary="Laughter",
            key_quotes=["LMAOOOOO NO WAY"],
        ),
    )


class _FakeMonitor:
    """Controllable offline stand-in for `ChatMonitor`."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self._fail_with = fail_with
        self._stop_event = asyncio.Event()
        self.started = asyncio.Event()
        self.closed = False

    async def run(self) -> None:
        self.started.set()
        if self._fail_with is not None:
            await asyncio.sleep(0.01)
            raise self._fail_with
        await self._stop_event.wait()

    def stop(self) -> None:
        self.closed = True
        self._stop_event.set()

    async def aclose(self) -> None:
        self.stop()


class _FakeChildProc:
    """Controllable offline stand-in for Streamlink / FFmpeg processes."""

    def __init__(self, *, on_exit=None) -> None:
        self.returncode: int | None = None
        self.stderr = None
        self._done = asyncio.Event()
        self._on_exit = on_exit

    def terminate(self) -> None:
        if self.returncode is None:
            self.returncode = 0
            self._done.set()
            if self._on_exit is not None:
                self._on_exit()

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9
            self._done.set()
            if self._on_exit is not None:
                self._on_exit()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode


class _FakeExtractProc:
    """Fake FFmpeg extraction process that writes a non-empty .tmp.mp4."""

    def __init__(self, out_path: Path) -> None:
        self._out_path = out_path
        self.returncode: int | None = 0

    async def communicate(self):
        self._out_path.write_bytes(b"fake-mp4-bytes")
        return b"", b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return 0 if self.returncode is None else self.returncode


async def _ok_pipeline_factory(_sl_cmd: list[str], _ff_cmd: list[str]) -> PipelineProcesses:
    # Simulate OS pipe EOF: when Streamlink exits, FFmpeg flushes and exits cleanly.
    ff = _FakeChildProc()
    sl = _FakeChildProc(on_exit=ff.terminate)
    return PipelineProcesses(streamlink=sl, ffmpeg=ff)


async def _ok_extractor_factory(cmd: list[str]) -> _FakeExtractProc:
    return _FakeExtractProc(Path(cmd[-1]))


def _seed_segment(session: StreamSession, *, wall_closed: float = 1080.0, duration: float = 80.0) -> Path:
    assert session.buffer_manager is not None
    assert session.buffer_run_dir is not None
    session.buffer_run_dir.mkdir(parents=True, exist_ok=True)
    seg_path = session.buffer_run_dir / "s1_seg_00000.ts"
    seg_path.write_bytes(b"fake-ts-payload")
    session.buffer_manager.register_segment(
        VideoSegment(
            segment_id=seg_path.name,
            path=seg_path,
            session_id=1,
            wall_closed=wall_closed,
            mono_closed=100.0,
            media_duration=duration,
            ffmpeg_start_pts=0.0,
            ffmpeg_end_pts=duration,
            size_bytes=seg_path.stat().st_size,
        )
    )
    return seg_path


class TestStreamRoutingAnalysisSink(unittest.TestCase):
    def test_routes_analysis_results_to_correct_session_case_insensitive_and_strips_hash(self):
        router = StreamRoutingAnalysisSink()
        got_alpha: list[str] = []
        got_beta: list[str] = []

        self.assertEqual(router.register("#Alpha", lambda r: got_alpha.append(r.event_id)), "alpha")
        self.assertEqual(router.register("beta", lambda r: got_beta.append(r.event_id)), "beta")
        self.assertEqual(router.registered_channels(), ["alpha", "beta"])

        self.assertTrue(router.route(_ok_analysis(_make_event("e1", "alpha"))))
        self.assertTrue(router.route(_ok_analysis(_make_event("e2", "#ALPHA"))))
        self.assertTrue(router.route(_ok_analysis(_make_event("e3", "Beta"))))

        self.assertEqual(got_alpha, ["e1", "e2"])
        self.assertEqual(got_beta, ["e3"])
        self.assertEqual(router.routed_count, 3)
        self.assertEqual(router.discarded_count, 0)

    def test_discards_results_for_stopped_or_unknown_session_without_raising(self):
        router = StreamRoutingAnalysisSink()
        seen: list[str] = []
        router.register("alpha", lambda r: seen.append(r.event_id))

        # Unknown channel -> discarded cleanly.
        self.assertFalse(router.route(_ok_analysis(_make_event("ghost-1", "ghost"))))

        # Unregister alpha -> late verdict discarded cleanly without raising.
        self.assertTrue(router.unregister("alpha"))
        self.assertFalse(router.has_route("alpha"))
        router(_ok_analysis(_make_event("late-1", "alpha")))

        self.assertEqual(seen, [])
        self.assertEqual(router.routed_count, 0)
        self.assertEqual(router.discarded_count, 2)
        self.assertEqual(router.error_count, 0)

    def test_handler_exception_is_isolated_and_does_not_affect_other_routes(self):
        router = StreamRoutingAnalysisSink()
        got_beta: list[str] = []

        def _broken(_result: AnalyzedHighlight) -> None:
            raise RuntimeError("session handler exploded")

        router.register("alpha", _broken)
        router.register("beta", lambda r: got_beta.append(r.event_id))

        self.assertFalse(router.route(_ok_analysis(_make_event("e-a", "alpha"))))
        self.assertTrue(router.route(_ok_analysis(_make_event("e-b", "beta"))))
        self.assertEqual(got_beta, ["e-b"])
        self.assertEqual(router.error_count, 1)
        self.assertEqual(router.routed_count, 1)

    def test_unregister_expected_guard_protects_restarted_session(self):
        router = StreamRoutingAnalysisSink()
        seen_new: list[str] = []

        class _Target:
            def __init__(self, out: list[str]) -> None:
                self.out = out

            def handle_analysis_result(self, r: AnalyzedHighlight) -> bool:
                self.out.append(r.event_id)
                return True

        old_target = _Target([])
        new_target = _Target(seen_new)
        router.register("alpha", old_target)
        router.register("alpha", new_target)

        # Slow teardown of old_target MUST NOT remove new_target!
        self.assertFalse(router.unregister("alpha", expected=old_target))
        self.assertTrue(router.has_route("alpha"))
        self.assertTrue(router.route(_ok_analysis(_make_event("e-new", "alpha"))))
        self.assertEqual(seen_new, ["e-new"])

        # Matching expected removes cleanly.
        self.assertTrue(router.unregister("alpha", expected=new_target))
        self.assertFalse(router.has_route("alpha"))

    def test_dropped_event_routing_and_discard_for_stopped_session(self):
        router = StreamRoutingAnalysisSink()
        dropped_seen: list[tuple[str, str]] = []
        router.register(
            "alpha",
            lambda _r: None,
            on_dropped=lambda ev, reason: dropped_seen.append((ev.event_id, reason)),
        )

        self.assertTrue(
            router.handle_dropped_event(_make_event("d1", "alpha"), "shutdown_drain_timeout")
        )
        router.unregister("alpha")
        self.assertFalse(
            router.handle_dropped_event(_make_event("d2", "alpha"), "shutdown_drain_timeout")
        )

        self.assertEqual(dropped_seen, [("d1", "shutdown_drain_timeout")])
        self.assertEqual(router.dropped_routed_count, 1)
        self.assertEqual(router.dropped_discarded_count, 1)

    def test_router_is_thread_safe_under_concurrent_register_unregister_and_route(self):
        router = StreamRoutingAnalysisSink()
        counts = {"a": 0, "b": 0, "c": 0}
        count_lock = concurrent.futures.ThreadPoolExecutor  # avoid unused check

        import threading
        mu = threading.Lock()

        def _make_handler(key: str):
            def _h(_r: AnalyzedHighlight) -> None:
                with mu:
                    counts[key] += 1
            return _h

        for ch in ("a", "b", "c"):
            router.register(ch, _make_handler(ch))

        def _route_worker(i: int) -> None:
            ch = ("a", "b", "c", "gone")[i % 4]
            router.route(_ok_analysis(_make_event(f"ev-{i}", ch)))

        with count_lock(max_workers=8) as pool:
            list(pool.map(_route_worker, range(120)))

        self.assertEqual(router.routed_count, 90)
        self.assertEqual(router.discarded_count, 30)
        self.assertEqual(sum(counts.values()), 90)


class TestStreamSessionLifecycleAndIsolation(unittest.IsolatedAsyncioTestCase):
    async def test_recording_disabled_default_does_not_start_recorder_or_require_binaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp), recording_enabled=False)
            router = StreamRoutingAnalysisSink()
            states: list[tuple[SessionState, SessionState]] = []

            session = StreamSession(
                "#KaiCenat",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
                on_state_change=lambda _sess, old, new: states.append((old, new)),
            )

            self.assertEqual(session.channel, "kaicenat")
            self.assertEqual(session.state, SessionState.STOPPED)
            self.assertFalse(session.is_running)

            ok = await session.start()
            self.assertTrue(ok)
            self.assertEqual(session.state, SessionState.RUNNING)
            self.assertTrue(session.is_running)
            self.assertFalse(session.recording_enabled)
            self.assertIsNotNone(session.detector)
            self.assertIsNotNone(session.chat_monitor)
            self.assertIsNone(session.recorder)
            self.assertIsNone(session.buffer_manager)
            self.assertIsNone(session.segment_index)
            self.assertIsNone(session.clip_worker)
            self.assertTrue(router.has_route("kaicenat"))

            snap = session.snapshot()
            self.assertEqual(snap.channel, "kaicenat")
            self.assertEqual(snap.state, SessionState.RUNNING)
            self.assertFalse(snap.recording_enabled)
            self.assertEqual(snap.start_count, 1)

            await session.stop()
            self.assertEqual(session.state, SessionState.STOPPED)
            self.assertFalse(router.has_route("kaicenat"))
            self.assertEqual(
                states,
                [
                    (SessionState.STOPPED, SessionState.RUNNING),
                    (SessionState.RUNNING, SessionState.STOPPING),
                    (SessionState.STOPPING, SessionState.STOPPED),
                ],
            )

    async def test_recording_enabled_creates_per_channel_directories_and_extracts_routed_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True)
            router = StreamRoutingAnalysisSink()

            class _StubAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    return _ok_analysis(event, should_clip=True)

                async def close(self) -> None:
                    pass

            worker = HighlightAnalysisWorker(
                _StubAnalyzer(),
                AnalysisConfig(max_concurrency=1),
                sink=CompositeAnalysisSink(router),
                on_event_dropped=router.handle_dropped_event,
            )
            await worker.start()

            extracted_results = []
            extracted_ev = asyncio.Event()

            def _on_extracted(res):
                extracted_results.append(res)
                extracted_ev.set()

            session = StreamSession(
                "kaicenat",
                settings,
                submit_to_analysis=worker.submit,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
                on_extracted=_on_extracted,
            )

            try:
                self.assertTrue(await session.start())
                self.assertTrue(session.recording_enabled)
                self.assertIsNotNone(session.run_id)
                self.assertEqual(
                    session.buffer_run_dir,
                    root / ".buffer" / "kaicenat" / session.run_id,
                )
                self.assertEqual(session.clips_channel_dir, root / "clips" / "kaicenat")
                self.assertIsNotNone(session.buffer_manager)
                self.assertIsNotNone(session.segment_index)
                self.assertIsNotNone(session.recorder)
                self.assertIsNotNone(session.clip_worker)

                # Seed one recorded segment into this session's buffer and trigger a highlight.
                _seed_segment(session)
                assert session._pin_sink is not None
                self.assertTrue(session._pin_sink(_make_event("clip-ev-1", "kaicenat")))

                await asyncio.wait_for(extracted_ev.wait(), timeout=2.0)
                self.assertEqual(len(extracted_results), 1)
                res = extracted_results[0]
                self.assertEqual(res.status, "extracted")
                self.assertIsNotNone(res.clip_path)
                self.assertEqual(res.clip_path.parent, root / "clips" / "kaicenat")
                self.assertTrue(res.clip_path.is_file())
                self.assertTrue(res.metadata_path.is_file())
                self.assertEqual(session.segment_index.active_pins, [])
                self.assertEqual(session.snapshot().clips_extracted, 1)
            finally:
                await session.stop_chat()
                await worker.stop(drain_timeout=2.0)
                await session.stop(clip_drain_timeout=2.0)

    async def test_independent_start_stop_and_restart_creates_fresh_run_id_and_cleans_old_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True)
            router = StreamRoutingAnalysisSink()

            session = StreamSession(
                "alpha",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            self.assertTrue(await session.start())
            run_id_1 = session.run_id
            run_dir_1 = session.buffer_run_dir
            seg_1 = _seed_segment(session)
            self.assertTrue(seg_1.exists())

            await session.stop(clip_drain_timeout=1.0)
            self.assertEqual(session.state, SessionState.STOPPED)
            self.assertFalse(seg_1.exists())
            assert run_dir_1 is not None
            self.assertFalse(run_dir_1.exists())
            self.assertFalse(router.has_route("alpha"))

            # Restart creates a brand-new run_id, fresh SegmentIndex, and re-registers route!
            self.assertTrue(await session.restart(clip_drain_timeout=1.0))
            self.assertEqual(session.state, SessionState.RUNNING)
            self.assertEqual(session.start_count, 2)
            self.assertNotEqual(session.run_id, run_id_1)
            self.assertTrue(router.has_route("alpha"))
            assert session.segment_index is not None
            self.assertEqual(session.segment_index.segments, [])

            await session.stop(clip_drain_timeout=1.0)
            self.assertEqual(session.state, SessionState.STOPPED)

    async def test_two_sessions_run_independently_and_stopping_one_does_not_affect_other(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True)
            router = StreamRoutingAnalysisSink()
            shared_sem = asyncio.Semaphore(1)

            b_extracted = asyncio.Event()

            s_a = StreamSession(
                "alpha",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                extraction_semaphore=shared_sem,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )
            s_b = StreamSession(
                "beta",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                extraction_semaphore=shared_sem,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
                on_extracted=lambda _r: b_extracted.set(),
            )

            self.assertTrue(await s_a.start())
            self.assertTrue(await s_b.start())
            _seed_segment(s_a)
            _seed_segment(s_b)

            # Stop alpha independently; beta MUST remain running and unaffected!
            await s_a.stop(clip_drain_timeout=1.0)
            self.assertEqual(s_a.state, SessionState.STOPPED)
            self.assertEqual(s_b.state, SessionState.RUNNING)
            self.assertFalse(router.has_route("alpha"))
            self.assertTrue(router.has_route("beta"))

            # Late verdict for stopped alpha is discarded without raising.
            self.assertFalse(router.route(_ok_analysis(_make_event("late-a", "alpha"))))

            # Beta still pins, routes, and extracts cleanly!
            assert s_b._pin_sink is not None
            ev_b = _make_event("live-b", "beta")
            self.assertTrue(s_b._pin_sink(ev_b))
            self.assertTrue(router.route(_ok_analysis(ev_b, should_clip=True)))
            await asyncio.wait_for(b_extracted.wait(), timeout=2.0)
            self.assertEqual(s_b.snapshot().clips_extracted, 1)

            await s_b.stop(clip_drain_timeout=1.0)

    async def test_session_startup_and_runtime_failures_are_isolated_to_errored_session_and_restartable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True)
            router = StreamRoutingAnalysisSink()

            # 1) Runtime chat crash in alpha MUST NOT affect beta; alpha transitions to ERRORED.
            should_crash = True

            def _flaky_monitor_factory(_s, _c, _p, _pm):
                return _FakeMonitor(
                    fail_with=RuntimeError("chat ws crashed") if should_crash else None
                )

            errored_ev = asyncio.Event()

            def _watch_alpha(_sess, _old, new):
                if new == SessionState.ERRORED:
                    errored_ev.set()

            s_alpha = StreamSession(
                "alpha",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=_flaky_monitor_factory,
                on_state_change=_watch_alpha,
            )
            s_beta = StreamSession(
                "beta",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            self.assertTrue(await s_alpha.start())
            self.assertTrue(await s_beta.start())
            await asyncio.wait_for(errored_ev.wait(), timeout=2.0)

            self.assertEqual(s_alpha.state, SessionState.ERRORED)
            self.assertIsInstance(s_alpha.last_error, RuntimeError)
            self.assertIn("chat ws crashed", s_alpha.error_message or "")
            self.assertFalse(router.has_route("alpha"))
            # Sibling beta is completely unaffected!
            self.assertEqual(s_beta.state, SessionState.RUNNING)
            self.assertTrue(router.has_route("beta"))

            # Alpha can recover cleanly via restart()!
            should_crash = False
            self.assertTrue(await s_alpha.restart(clip_drain_timeout=1.0))
            self.assertEqual(s_alpha.state, SessionState.RUNNING)
            self.assertIsNone(s_alpha.last_error)
            self.assertTrue(router.has_route("alpha"))

            # 2) Startup failure in gamma does NOT propagate across asyncio.gather!
            def _broken_monitor_factory(_s, _c, _p, _pm):
                raise RuntimeError("startup boom")

            s_gamma = StreamSession(
                "gamma",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                chat_monitor_factory=_broken_monitor_factory,
            )
            s_delta = StreamSession(
                "delta",
                settings,
                submit_to_analysis=lambda _ev: True,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            ok_gamma, ok_delta = await asyncio.gather(s_gamma.start(), s_delta.start())
            self.assertFalse(ok_gamma)
            self.assertTrue(ok_delta)
            self.assertEqual(s_gamma.state, SessionState.ERRORED)
            self.assertIn("startup boom", s_gamma.error_message or "")
            self.assertFalse(router.has_route("gamma"))
            self.assertEqual(s_delta.state, SessionState.RUNNING)

            await asyncio.gather(
                s_alpha.stop(clip_drain_timeout=1.0),
                s_beta.stop(clip_drain_timeout=1.0),
                s_gamma.stop(clip_drain_timeout=1.0),
                s_delta.stop(clip_drain_timeout=1.0),
            )

    async def test_two_phase_shutdown_routes_inflight_gemini_results_before_clip_worker_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True)
            router = StreamRoutingAnalysisSink()

            started = asyncio.Event()
            release = asyncio.Event()

            class _GatedAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    started.set()
                    await release.wait()
                    return _ok_analysis(event, should_clip=True)

                async def close(self) -> None:
                    pass

            worker = HighlightAnalysisWorker(
                _GatedAnalyzer(),
                AnalysisConfig(max_concurrency=1),
                sink=router,
                on_event_dropped=router.handle_dropped_event,
            )
            await worker.start()

            session = StreamSession(
                "kaicenat",
                settings,
                submit_to_analysis=worker.submit,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )
            self.assertTrue(await session.start())
            _seed_segment(session)

            # Event is pinned & in-flight inside Gemini.
            assert session._pin_sink is not None
            self.assertTrue(session._pin_sink(_make_event("shutdown-ev", "kaicenat")))
            await asyncio.wait_for(started.wait(), timeout=1.0)

            # Phase 1: Stop chat ONLY -> route and clip_worker remain open!
            await session.stop_chat()
            self.assertTrue(router.has_route("kaicenat"))

            # Phase 2: Drain shared Gemini worker -> verdict routes while clip_worker is open!
            release.set()
            await worker.stop(drain_timeout=2.0)

            # Phase 3: Stop session -> drains extraction & cleans buffer!
            await session.stop(clip_drain_timeout=2.0)

            self.assertEqual(session.state, SessionState.STOPPED)
            self.assertEqual(session.snapshot().clips_extracted, 1)
            self.assertEqual(router.routed_count, 1)
            self.assertEqual(router.discarded_count, 0)

    async def test_dropped_gemini_event_routes_back_to_session_and_releases_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True)
            router = StreamRoutingAnalysisSink()

            started = asyncio.Event()
            never = asyncio.Event()

            class _HangingAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    started.set()
                    await never.wait()
                    return _ok_analysis(event)

                async def close(self) -> None:
                    pass

            worker = HighlightAnalysisWorker(
                _HangingAnalyzer(),
                AnalysisConfig(max_concurrency=1),
                sink=router,
                on_event_dropped=router.handle_dropped_event,
            )
            await worker.start()

            session = StreamSession(
                "kaicenat",
                settings,
                submit_to_analysis=worker.submit,
                router=router,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_ok_pipeline_factory,
                extractor_process_factory=_ok_extractor_factory,
                trailing_wait_seconds=0.0,
                cleanup_interval_seconds=60.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )
            self.assertTrue(await session.start())
            _seed_segment(session)

            assert session._pin_sink is not None
            self.assertTrue(session._pin_sink(_make_event("cancel-ev", "kaicenat")))
            await asyncio.wait_for(started.wait(), timeout=1.0)
            assert session.segment_index is not None
            self.assertEqual(len(session.segment_index.active_pins), 1)

            # Drain timeout cancels in-flight event -> router unpins session buffer cleanly!
            await worker.stop(drain_timeout=0.02)
            self.assertEqual(router.dropped_routed_count, 1)
            self.assertEqual(session.segment_index.active_pins, [])

            await session.stop(clip_drain_timeout=1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
