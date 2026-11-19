"""Offline unit & async tests for Stage 6.3 MultiStreamManager, GlobalDiskBudget, and Helix liveness."""

from __future__ import annotations

import asyncio
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import requests

from app.analysis.models import (
    AnalysisConfig,
    AnalysisStatus,
    AnalyzedHighlight,
    GeminiHighlightVerdict,
    HighlightCategory,
)
from app.config import Settings
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent
from app.discovery.stream_finder import LiveStream, RateLimitError, StreamFinder
from app.multistream import (
    BudgetCheckResult,
    BudgetState,
    ChannelLivenessChecker,
    GlobalDiskBudget,
    MultiStreamManager,
    SessionState,
    StreamSession,
)
from app.recording import (
    PipelineProcesses,
    RecordingConfig,
    RollingBufferManager,
    VideoSegment,
)


def _settings(root: Path, *, recording_enabled: bool = False, max_streams: int = 3) -> Settings:
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
        max_concurrent_streams=max_streams,
        max_total_buffer_mb=100.0,
        max_concurrent_extractions=2,
        liveness_poll_seconds=60.0,
    )


def _make_event(event_id: str, stream_id: str) -> HighlightEvent:
    msgs = [
        ChatMessage("u1", "let's go", 1005.0),
        ChatMessage("u2", "LMAOOOOO NO WAY", 1010.0, emote_count=2),
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
        unique_chatters=2,
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
            highlight_score=90 if should_clip else 15,
            confidence=0.95,
            category=HighlightCategory.FUNNY_MOMENT,
            is_spam_or_false_positive=False,
            title="Clip moment" if should_clip else "Skip",
            reasoning="Chat went crazy.",
            reaction_summary="Hype",
            key_quotes=["LMAOOOOO NO WAY"],
        ),
    )


class _FakeMonitor:
    def __init__(self) -> None:
        self._stop_ev = asyncio.Event()

    async def run(self) -> None:
        await self._stop_ev.wait()

    def stop(self) -> None:
        self._stop_ev.set()

    async def aclose(self) -> None:
        self.stop()


class _FakeChildProc:
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
    def __init__(self, out_path: Path) -> None:
        self._out_path = out_path
        self.returncode: int | None = 0

    async def communicate(self):
        self._out_path.write_bytes(b"fake-mp4")
        return b"", b""

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return 0 if self.returncode is None else self.returncode


async def _fake_pipeline_factory(_sl, _ff):
    ff = _FakeChildProc()
    sl = _FakeChildProc(on_exit=ff.terminate)
    return PipelineProcesses(streamlink=sl, ffmpeg=ff)


async def _fake_extract_factory(cmd: list[str]) -> _FakeExtractProc:
    return _FakeExtractProc(Path(cmd[-1]))


def _seed_segment_on_mgr(
    mgr: RollingBufferManager,
    path: Path,
    *,
    size_bytes: int = 10,
    wall_closed: float = 1020.0,
    duration: float = 20.0,
) -> VideoSegment:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size_bytes)
    seg = VideoSegment(
        segment_id=path.name,
        path=path,
        session_id=1,
        wall_closed=wall_closed,
        mono_closed=100.0,
        media_duration=duration,
        ffmpeg_start_pts=0.0,
        ffmpeg_end_pts=duration,
        size_bytes=size_bytes,
    )
    mgr.register_segment(seg)
    return seg


# ==============================================================================
# 1. Batched Helix Liveness & StreamFinder Tests
# ==============================================================================

class TestStreamFinderBatchedLiveness(unittest.TestCase):
    def test_check_live_channels_batches_and_distinguishes_live_vs_offline(self):
        settings = _settings(Path("/tmp"))
        finder = StreamFinder(settings)

        resp_data = {
            "data": [
                {
                    "user_login": "kaicenat",
                    "user_name": "KaiCenat",
                    "title": "GTA 6 live",
                    "viewer_count": 80000,
                    "language": "en",
                },
                {
                    "user_login": "xqc",
                    "user_name": "xQc",
                    "title": "JUICED",
                    "viewer_count": 45000,
                    "language": "en",
                },
            ]
        }

        with mock.patch.object(finder, "_get", return_value=resp_data) as m_get:
            res = finder.check_live_channels(["#KaiCenat", "xqc", "offline_streamer"])
            self.assertEqual(m_get.call_count, 1)

            # Queried in one single batch.
            call_path, call_params = m_get.call_args[0]
            self.assertEqual(call_path, "/streams")
            self.assertEqual(call_params["user_login"], ["kaicenat", "xqc", "offline_streamer"])

            self.assertIsNotNone(res["kaicenat"])
            assert res["kaicenat"] is not None
            self.assertEqual(res["kaicenat"].channel, "kaicenat")
            self.assertEqual(res["kaicenat"].viewer_count, 80000)

            self.assertIsNotNone(res["xqc"])
            self.assertIsNone(res["offline_streamer"])

    def test_rate_limit_error_parsing(self):
        settings = _settings(Path("/tmp"))
        finder = StreamFinder(settings)

        resp = mock.Mock(status_code=429, text="rate limit exceeded")
        resp.headers = {"Retry-After": "25"}
        resp.json.return_value = {"message": "rate limit exceeded"}

        with (
            mock.patch.object(finder, "_headers", return_value={}),
            mock.patch.object(finder._http, "get", return_value=resp),
        ):
            with self.assertRaises(RateLimitError) as ctx:
                finder.check_live_channels(["kaicenat"])
            self.assertEqual(ctx.exception.retry_after, 25.0)


class TestChannelLivenessChecker(unittest.IsolatedAsyncioTestCase):
    async def test_offline_and_online_transitions_invoke_callbacks(self):
        offline_events: list[str] = []
        online_events: list[tuple[str, int]] = []

        streams = {
            "kaicenat": LiveStream("kaicenat", "Kai", "GTA", 50000, "en"),
            "xqc": LiveStream("xqc", "xQc", "GTA", 30000, "en"),
        }

        def _fake_check(channels: list[str]) -> dict[str, LiveStream | None]:
            return {c: streams.get(c) for c in channels}

        checker = ChannelLivenessChecker(
            _fake_check,
            poll_interval_seconds=0.05,
            on_channel_offline=offline_events.append,
            on_channel_online=lambda ch, s: online_events.append((ch, s.viewer_count)),
        )
        checker.set_watched_channels(["kaicenat", "xqc"], initial_live=True)

        # 1) xqc drops offline.
        del streams["xqc"]
        res = await checker.check_now()
        self.assertIsNone(res["xqc"])
        self.assertIsNotNone(res["kaicenat"])
        self.assertEqual(offline_events, ["xqc"])
        self.assertEqual(online_events, [])

        # 2) xqc comes back online.
        streams["xqc"] = LiveStream("xqc", "xQc", "GTA back", 35000, "en")
        await checker.check_now()
        self.assertEqual(online_events, [("xqc", 35000)])

    async def test_transient_error_and_rate_limit_do_not_mark_channels_offline(self):
        offline_events: list[str] = []

        def _failing_check(_channels):
            raise RateLimitError("rate limited", retry_after=0.05)

        checker = ChannelLivenessChecker(
            _failing_check,
            poll_interval_seconds=0.02,
            rate_limit_backoff_seconds=0.02,
            on_channel_offline=offline_events.append,
        )
        checker.set_watched_channels(["kaicenat"], initial_live=True)
        await checker.start()
        try:
            await asyncio.sleep(0.08)
        finally:
            await checker.stop()

        # Despite multiple rate limit errors, kaicenat was NEVER falsely declared offline!
        self.assertEqual(offline_events, [])
        self.assertGreater(checker.snapshot().consecutive_errors, 0)


# ==============================================================================
# 2. GlobalDiskBudget Tests
# ==============================================================================

class TestGlobalDiskBudget(unittest.TestCase):
    def test_enforce_budget_trims_unpinned_segments_across_sessions_and_preserves_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_a = RecordingConfig(buffer_dir=root / "a" / "buf", max_buffer_mb=100.0)
            cfg_b = RecordingConfig(buffer_dir=root / "b" / "buf", max_buffer_mb=100.0)
            mgr_a = RollingBufferManager(cfg_a)
            mgr_b = RollingBufferManager(cfg_b)

            # Stream A has 60 bytes unpinned + 40 bytes PINNED.
            _seed_segment_on_mgr(mgr_a, root / "a" / "seg1.ts", size_bytes=60, wall_closed=1010.0)
            _seed_segment_on_mgr(mgr_a, root / "a" / "seg2.ts", size_bytes=40, wall_closed=1020.0)
            mgr_a.pin_event("pin-a", 1015.0, 1025.0)  # pins seg2 (40 bytes)!

            # Stream B has 80 bytes unpinned.
            _seed_segment_on_mgr(mgr_b, root / "b" / "seg1.ts", size_bytes=80, wall_closed=1005.0)

            # Total storage = 60 + 40 + 80 = 180 bytes.
            # Budget set to 100 bytes (0.000095 MB) -> must free 80 bytes unpinned!
            budget_bytes = 100
            budget_mb = budget_bytes / (1024 * 1024)
            budget = GlobalDiskBudget(max_total_buffer_mb=budget_mb)

            class _MockSession:
                def __init__(self, ch: str, mgr: RollingBufferManager) -> None:
                    self.channel = ch
                    self.buffer_manager = mgr

            sess_a = _MockSession("a", mgr_a)
            sess_b = _MockSession("b", mgr_b)
            budget.register_session(sess_a)  # type: ignore[arg-type]
            budget.register_session(sess_b)  # type: ignore[arg-type]

            res: BudgetCheckResult = budget.enforce_budget()

            self.assertEqual(res.state, BudgetState.OK)
            self.assertEqual(res.initial_bytes, 180)
            self.assertGreaterEqual(res.freed_bytes, 80)
            self.assertLessEqual(res.final_bytes, 100)

            # Pinned seg2 in Stream A MUST STILL BE INTACT!
            self.assertTrue((root / "a" / "seg2.ts").is_file())
            self.assertIsNotNone(mgr_a.get_pin("pin-a"))
            self.assertEqual(mgr_a.index.pinned_bytes, 40)

    def test_emergency_state_triggered_when_pinned_storage_exceeds_ceiling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root / "buf", max_buffer_mb=10.0)
            mgr = RollingBufferManager(cfg)
            _seed_segment_on_mgr(mgr, root / "seg1.ts", size_bytes=200, wall_closed=1010.0)
            mgr.pin_event("pin-big", 1000.0, 1020.0)

            emergencies: list[str] = []
            # 100 byte max, 1.2x emergency ceiling = 120 bytes.
            budget = GlobalDiskBudget(
                max_total_buffer_mb=100 / (1024 * 1024),
                emergency_multiplier=1.2,
                on_emergency_budget=emergencies.append,
            )

            class _MockSession:
                def __init__(self, mgr: RollingBufferManager) -> None:
                    self.channel = "ch"
                    self.buffer_manager = mgr

            budget.register_session(_MockSession(mgr))  # type: ignore[arg-type]
            res = budget.enforce_budget()

            self.assertEqual(res.state, BudgetState.EMERGENCY)
            self.assertTrue(res.emergency_triggered)
            self.assertEqual(len(emergencies), 1)
            self.assertIn("Global disk budget emergency ceiling reached", emergencies[0])
            # Pinned file is preserved even under emergency!
            self.assertTrue((root / "seg1.ts").is_file())


# ==============================================================================
# 3. MultiStreamManager Orchestrator Tests
# ==============================================================================

class TestMultiStreamManager(unittest.IsolatedAsyncioTestCase):
    async def test_starts_up_to_max_concurrent_streams_and_enforces_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp), max_streams=2)
            manager = MultiStreamManager(
                settings,
                channels=["alpha", "beta", "gamma"],
                max_concurrent_streams=2,
                enable_liveness_check=False,
                enable_disk_budget=False,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            res = await manager.start()
            self.assertTrue(manager.is_running)
            self.assertEqual(res, {"alpha": True, "beta": True})
            self.assertEqual(manager.active_channels(), ["alpha", "beta"])

            # 3rd stream (gamma) was deferred because max_concurrent_streams=2.
            self.assertFalse(await manager.start_channel("gamma"))
            self.assertEqual(manager.active_channels(), ["alpha", "beta"])

            # Stopping alpha frees up a slot so gamma can start!
            self.assertTrue(await manager.stop_channel("alpha"))
            self.assertEqual(manager.active_channels(), ["beta"])
            self.assertTrue(await manager.start_channel("gamma"))
            self.assertEqual(manager.active_channels(), ["beta", "gamma"])

            await manager.stop()
            self.assertFalse(manager.is_running)
            self.assertEqual(manager.active_channels(), [])

    async def test_shared_gemini_worker_fairness_and_result_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True, max_streams=2)

            class _EchoAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    return _ok_analysis(event, should_clip=True)

                async def close(self) -> None:
                    pass

            extracted: list[str] = []
            ev_extracted = asyncio.Event()

            def _on_extracted(r):
                extracted.append(r.event_id)
                if len(extracted) == 2:
                    ev_extracted.set()

            manager = MultiStreamManager(
                settings,
                channels=["kaicenat", "xqc"],
                max_concurrent_streams=2,
                analyzer=_EchoAnalyzer(),
                enable_liveness_check=False,
                enable_disk_budget=False,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_fake_pipeline_factory,
                extractor_process_factory=_fake_extract_factory,
                trailing_wait_seconds=0.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
                on_extracted=_on_extracted,
            )

            await manager.start()
            try:
                s_kai = manager.get_session("kaicenat")
                s_xqc = manager.get_session("xqc")
                assert s_kai is not None and s_xqc is not None

                _seed_segment_on_mgr(
                    s_kai.buffer_manager,  # type: ignore[arg-type]
                    s_kai.buffer_run_dir / "s1_seg_00000.ts",  # type: ignore[operator]
                    wall_closed=1080.0,
                    duration=80.0,
                )
                _seed_segment_on_mgr(
                    s_xqc.buffer_manager,  # type: ignore[arg-type]
                    s_xqc.buffer_run_dir / "s1_seg_00000.ts",  # type: ignore[operator]
                    wall_closed=1080.0,
                    duration=80.0,
                )

                # Submit highlights from both channels to the shared pipeline.
                assert s_kai._pin_sink is not None
                assert s_xqc._pin_sink is not None
                self.assertTrue(s_kai._pin_sink(_make_event("kai-ev", "kaicenat")))
                self.assertTrue(s_xqc._pin_sink(_make_event("xqc-ev", "xqc")))

                await asyncio.wait_for(ev_extracted.wait(), timeout=2.0)
                self.assertEqual(set(extracted), {"kai-ev", "xqc-ev"})

                # Both clips written to their respective per-channel output directories!
                kai_clips = list((root / "clips" / "kaicenat").glob("*.mp4"))
                xqc_clips = list((root / "clips" / "xqc").glob("*.mp4"))
                self.assertEqual(len(kai_clips), 1)
                self.assertEqual(len(xqc_clips), 1)
            finally:
                await manager.stop()

    async def test_two_phase_graceful_shutdown_drains_gemini_before_stopping_clip_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True, max_streams=1)

            started = asyncio.Event()
            release = asyncio.Event()

            class _GatedAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    started.set()
                    await release.wait()
                    return _ok_analysis(event, should_clip=True)

                async def close(self) -> None:
                    pass

            manager = MultiStreamManager(
                settings,
                channels=["kaicenat"],
                max_concurrent_streams=1,
                analyzer=_GatedAnalyzer(),
                enable_liveness_check=False,
                enable_disk_budget=False,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_fake_pipeline_factory,
                extractor_process_factory=_fake_extract_factory,
                trailing_wait_seconds=0.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            await manager.start()
            s_kai = manager.get_session("kaicenat")
            assert s_kai is not None

            _seed_segment_on_mgr(
                s_kai.buffer_manager,  # type: ignore[arg-type]
                s_kai.buffer_run_dir / "s1_seg_00000.ts",  # type: ignore[operator]
                wall_closed=1080.0,
                duration=80.0,
            )

            # In-flight event in Gemini.
            assert s_kai._pin_sink is not None
            self.assertTrue(s_kai._pin_sink(_make_event("ev-shutdown", "kaicenat")))
            await asyncio.wait_for(started.wait(), timeout=1.0)

            # Trigger stop in background task.
            stop_task = asyncio.create_task(manager.stop(gemini_drain_timeout=2.0, clip_drain_timeout=2.0))
            await asyncio.sleep(0.05)

            # Release Gemini analyzer -> verdict routes cleanly to open clip worker during drain!
            release.set()
            await asyncio.wait_for(stop_task, timeout=2.0)

            self.assertEqual(s_kai.snapshot().clips_extracted, 1)
            self.assertEqual(manager.router.routed_count, 1)

    async def test_offline_channel_stops_individual_session_gracefully_without_affecting_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp), max_streams=2)
            streams = {
                "alpha": LiveStream("alpha", "Alpha", "GTA", 1000, "en"),
                "beta": LiveStream("beta", "Beta", "GTA", 2000, "en"),
            }

            def _fake_check(channels: list[str]) -> dict[str, LiveStream | None]:
                return {c: streams.get(c) for c in channels}

            stopped_alpha = asyncio.Event()

            def _watch_state(sess: StreamSession, _old, new):
                if sess.channel == "alpha" and new == SessionState.STOPPED:
                    stopped_alpha.set()

            manager = MultiStreamManager(
                settings,
                channels=["alpha", "beta"],
                max_concurrent_streams=2,
                stream_finder=_fake_check,
                liveness_poll_seconds=0.05,
                enable_disk_budget=False,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
                on_state_change=_watch_state,
            )

            await manager.start()
            self.assertEqual(manager.active_channels(), ["alpha", "beta"])

            # Channel alpha goes offline on Twitch!
            del streams["alpha"]
            # Trigger liveness poll.
            assert manager.liveness_checker is not None
            await manager.liveness_checker.check_now()
            await asyncio.wait_for(stopped_alpha.wait(), timeout=1.0)

            # Alpha is stopped gracefully (Option A); Beta is STILL RUNNING!
            self.assertEqual(manager.active_channels(), ["beta"])
            s_alpha = manager.get_session("alpha")
            assert s_alpha is not None
            self.assertEqual(s_alpha.state, SessionState.STOPPED)

            await manager.stop()

    async def test_request_shutdown_triggers_run_until_shutdown_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp), max_streams=1)
            manager = MultiStreamManager(
                settings,
                channels=["alpha"],
                enable_liveness_check=False,
                enable_disk_budget=False,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            async def _killer():
                await asyncio.sleep(0.05)
                manager.request_shutdown()

            asyncio.create_task(_killer())
            await manager.run_until_shutdown()
            self.assertFalse(manager.is_running)

    async def test_auto_restart_online_restarts_previously_monitored_channel_when_it_returns(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp), max_streams=2)
            streams: dict[str, LiveStream] = {
                "alpha": LiveStream("alpha", "Alpha", "GTA", 1000, "en"),
            }

            def _fake_check(channels: list[str]) -> dict[str, LiveStream | None]:
                return {c: streams.get(c) for c in channels}

            manager = MultiStreamManager(
                settings,
                channels=["alpha"],
                max_concurrent_streams=2,
                auto_restart_online=True,
                stream_finder=_fake_check,
                liveness_poll_seconds=60.0,
                enable_disk_budget=False,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            await manager.start()
            self.assertEqual(manager.active_channels(), ["alpha"])
            assert manager.liveness_checker is not None

            # Goes offline -> session stops gracefully.
            del streams["alpha"]
            await manager.liveness_checker.check_now()
            self.assertEqual(manager.active_channels(), [])
            s_alpha = manager.get_session("alpha")
            assert s_alpha is not None
            self.assertEqual(s_alpha.state, SessionState.STOPPED)

            # Returns online -> automatically restarted with start_count=2!
            streams["alpha"] = LiveStream("alpha", "Alpha", "GTA back", 2500, "en")
            await manager.liveness_checker.check_now()
            self.assertEqual(manager.active_channels(), ["alpha"])
            self.assertEqual(s_alpha.state, SessionState.RUNNING)
            self.assertEqual(s_alpha.start_count, 2)

            await manager.stop()

    async def test_manager_status_snapshot_and_global_disk_emergency_pauses_recorders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _settings(root, recording_enabled=True, max_streams=2)

            class _NoopAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    return _ok_analysis(event, should_clip=False)

                async def close(self) -> None:
                    pass

            manager = MultiStreamManager(
                settings,
                channels=["alpha"],
                max_concurrent_streams=2,
                max_total_buffer_mb=100 / (1024 * 1024),
                disk_emergency_multiplier=1.2,
                analyzer=_NoopAnalyzer(),
                enable_liveness_check=False,
                enable_disk_budget=False,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_fake_pipeline_factory,
                extractor_process_factory=_fake_extract_factory,
                trailing_wait_seconds=0.0,
                chat_monitor_factory=lambda _s, _c, _p, _pm: _FakeMonitor(),
            )

            await manager.start()
            try:
                s_alpha = manager.get_session("alpha")
                assert s_alpha is not None and s_alpha.buffer_manager is not None
                assert s_alpha.buffer_run_dir is not None and s_alpha.recorder is not None

                seg_path = s_alpha.buffer_run_dir / "s1_seg_00000.ts"
                _seed_segment_on_mgr(
                    s_alpha.buffer_manager,
                    seg_path,
                    size_bytes=200,
                    wall_closed=1020.0,
                )
                s_alpha.buffer_manager.pin_event("pin-keep", 1000.0, 1020.0)

                # Trigger global disk budget check -> emergency pauses recorder, preserves pin!
                res = manager.disk_budget.enforce_budget()
                self.assertEqual(res.state, BudgetState.EMERGENCY)
                self.assertTrue(s_alpha.recorder.emergency_stopped)
                self.assertTrue(seg_path.is_file())

                # Verify full inspectable status snapshot.
                st = manager.status()
                self.assertTrue(st.running)
                self.assertEqual(st.active_channels, ["alpha"])
                self.assertIn("alpha", st.sessions)
                self.assertEqual(st.disk_budget.state, BudgetState.EMERGENCY)
                self.assertTrue(st.gemini.enabled)
            finally:
                await manager.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
