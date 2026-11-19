"""Offline unit & async tests for Stage 6.1 multi-stream prerequisites & hardening."""

from __future__ import annotations

import asyncio
import concurrent.futures
import io
import json
import os
import signal
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import requests

from app.analysis.gemini_client import GeminiAuthError, GeminiRateLimitError
from app.analysis.models import (
    AnalysisConfig,
    AnalysisStatus,
    AnalyzedHighlight,
    GeminiHighlightVerdict,
    HighlightCategory,
    JsonlAnalysisSink,
)
from app.analysis.worker import HighlightAnalysisWorker
from app.auth import user_token
from app.chat.twitch_chat import ChatMonitor
from app.config import Settings
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent
from app.recording import (
    ClipExtractionResult,
    ClipExtractionWorker,
    ClipExtractor,
    ClipWindowPlanner,
    PipelineProcesses,
    RecordingConfig,
    RollingBufferManager,
    Stage5PinAndSubmitSink,
    StreamRecorder,
    VideoSegment,
)
from app.recording.stream_recorder import _default_spawn_pipeline, _signal_process_tree


def _settings(root: Path) -> Settings:
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
        recording_buffer_dir=root / ".buffer",
        clips_output_dir=root / "clips",
    )


def _make_event(event_id: str, stream_id: str) -> HighlightEvent:
    msgs = [
        ChatMessage("u1", "before", 1005.0),
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


def _ok_analysis(event: HighlightEvent) -> AnalyzedHighlight:
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
            title="Funny moment",
            reasoning="Viewers laughed.",
            reaction_summary="Laughter",
            key_quotes=["LMAOOOOO NO WAY"],
        ),
    )


class TestOAuthConcurrencyAndTransientErrors(unittest.IsolatedAsyncioTestCase):
    async def test_sync_and_async_refresh_share_lock_and_do_not_block_event_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / ".tokens.json"
            # Write an expired token
            tokens_file.write_text(
                json.dumps(
                    {
                        "access_token": "old_access",
                        "refresh_token": "old_refresh",
                        "login": "tester",
                        "scopes": ["chat:read", "clips:edit"],
                        "expires_at": time.time() - 60,
                    }
                )
            )
            settings = _settings(Path(tmp))
            refresh_calls = 0

            def _slow_post_token(data: dict) -> dict:
                nonlocal refresh_calls
                refresh_calls += 1
                time.sleep(0.08)  # simulate slow network HTTP call
                return {
                    "access_token": f"new_access_{refresh_calls}",
                    "refresh_token": f"new_refresh_{refresh_calls}",
                    "expires_in": 3600,
                }

            def _fake_validate(_settings: Settings, tok_resp: dict) -> dict:
                data = {
                    "access_token": tok_resp["access_token"],
                    "refresh_token": tok_resp["refresh_token"],
                    "login": "tester",
                    "scopes": ["chat:read", "clips:edit"],
                    "expires_at": time.time() + 3600,
                }
                user_token._save_tokens(data)
                return {
                    "access_token": data["access_token"],
                    "login": "tester",
                    "scopes": data["scopes"],
                }

            ticks = 0
            stop_ticks = False

            async def _heartbeat() -> None:
                nonlocal ticks
                while not stop_ticks:
                    ticks += 1
                    await asyncio.sleep(0.01)

            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch.object(user_token, "_post_token", side_effect=_slow_post_token),
                mock.patch.object(user_token, "_validate_and_store", side_effect=_fake_validate),
            ):
                hb = asyncio.create_task(_heartbeat())
                # Mix 4 concurrent async callers + 1 concurrent sync thread caller
                sync_fut = asyncio.get_running_loop().run_in_executor(
                    None, user_token.get_valid_access_token, settings
                )
                results = await asyncio.gather(
                    user_token.get_valid_access_token_async(settings),
                    user_token.get_valid_access_token_async(settings),
                    user_token.get_valid_access_token_async(settings),
                    user_token.get_valid_access_token_async(settings),
                    sync_fut,
                )
                stop_ticks = True
                await hb

            # Exactly ONE refresh HTTP call across all 5 concurrent sync+async callers!
            self.assertEqual(refresh_calls, 1)
            self.assertEqual({r["access_token"] for r in results}, {"new_access_1"})
            # Event loop kept ticking freely during the 80ms simulated HTTP refresh!
            self.assertGreaterEqual(ticks, 3)

    async def test_force_refresh_coalesces_when_token_already_rotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / ".tokens.json"
            tokens_file.write_text(
                json.dumps(
                    {
                        "access_token": "stale_tok",
                        "refresh_token": "ref_1",
                        "login": "tester",
                        "scopes": ["chat:read"],
                        "expires_at": time.time() + 3600,
                    }
                )
            )
            settings = _settings(Path(tmp))
            calls = 0

            def _fake_refresh(_settings: Settings, _rt: str) -> dict:
                nonlocal calls
                calls += 1
                time.sleep(0.03)
                fresh = {
                    "access_token": f"rotated_{calls}",
                    "refresh_token": f"ref_new_{calls}",
                    "login": "tester",
                    "scopes": ["chat:read"],
                    "expires_at": time.time() + 3600,
                }
                user_token._save_tokens(fresh)
                return fresh

            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch.object(user_token, "_refresh", side_effect=_fake_refresh),
            ):
                res = await asyncio.gather(
                    user_token.force_refresh_async(settings, failed_access_token="stale_tok"),
                    user_token.force_refresh_async(settings, failed_access_token="stale_tok"),
                    user_token.force_refresh_async(settings, failed_access_token="stale_tok"),
                )

            self.assertEqual(calls, 1)
            self.assertEqual({r["access_token"] for r in res}, {"rotated_1"})

    def test_transient_network_error_preserves_tokens_while_400_clears(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / ".tokens.json"
            tokens_file.write_text(
                json.dumps(
                    {
                        "access_token": "old",
                        "refresh_token": "valid_refresh",
                        "login": "tester",
                        "scopes": ["chat:read"],
                        "expires_at": time.time() - 10,
                    }
                )
            )
            settings = _settings(Path(tmp))

            # 1) Network blip -> TransientTokenError, tokens file MUST remain intact!
            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch("requests.post", side_effect=requests.ConnectionError("dns blip")),
            ):
                with self.assertRaises(user_token.TransientTokenError):
                    user_token.get_valid_access_token(settings)
                self.assertTrue(tokens_file.exists())

            # 2) HTTP 503 -> TransientTokenError, tokens file MUST remain intact!
            resp_503 = mock.Mock(status_code=503, text="unavailable")
            resp_503.json.return_value = {"message": "unavailable"}
            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch("requests.post", return_value=resp_503),
            ):
                with self.assertRaises(user_token.TransientTokenError):
                    user_token.get_valid_access_token(settings)
                self.assertTrue(tokens_file.exists())

            # 3) HTTP 400 (revoked token) -> clears tokens & raises ReauthRequired!
            resp_400 = mock.Mock(status_code=400, text="Invalid refresh token")
            resp_400.json.return_value = {"message": "Invalid refresh token"}
            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch("requests.post", return_value=resp_400),
            ):
                with self.assertRaises(user_token.ReauthRequired):
                    user_token.get_valid_access_token(settings)
                self.assertFalse(tokens_file.exists())


class _FakeWebSocket:
    def __init__(self, frames: list[str]) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        for f in frames:
            self._queue.put_nowait(f)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(None)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


class TestIndependentChatLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_stopping_one_chat_monitor_does_not_affect_other_and_quiet_mode_suppresses_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            ws_a = _FakeWebSocket(
                [
                    ":tmi.twitch.tv 001 tester :Welcome",
                    ":tester!tester@tester.tmi.twitch.tv JOIN #alpha",
                    "@id=m1;display-name=Alice :alice!a@a.tmi.twitch.tv PRIVMSG #alpha :hello alpha",
                ]
            )
            ws_b = _FakeWebSocket(
                [
                    ":tmi.twitch.tv 001 tester :Welcome",
                    ":tester!tester@tester.tmi.twitch.tv JOIN #beta",
                    "@id=m2;display-name=Bob :bob!b@b.tmi.twitch.tv PRIVMSG #beta :hello beta",
                ]
            )
            sockets = iter([ws_a, ws_b])

            class _RecPipeline:
                def __init__(self) -> None:
                    self.messages: list[str] = []

                def process(self, msg, mono_now, wall_now) -> None:
                    self.messages.append(msg.text)

                def tick(self, mono_now, wall_now=None) -> None:
                    pass

            pipe_a = _RecPipeline()
            pipe_b = _RecPipeline()
            mon_a = ChatMonitor(settings, "alpha", pipeline=pipe_a, print_messages=False)
            mon_b = ChatMonitor(settings, "beta", pipeline=pipe_b, print_messages=False)

            buf = io.StringIO()
            with (
                mock.patch(
                    "app.chat.twitch_chat.get_valid_access_token_async",
                    return_value={"access_token": "tok", "login": "tester", "scopes": ["chat:read"]},
                ),
                mock.patch("app.chat.twitch_chat.websockets.connect", side_effect=lambda *a, **k: next(sockets)),
                redirect_stdout(buf),
            ):
                task_a = asyncio.create_task(mon_a.run())
                task_b = asyncio.create_task(mon_b.run())
                await asyncio.sleep(0.05)

                # Stop Stream A independently; Stream B must remain open and keep processing
                await mon_a.aclose()
                await asyncio.wait_for(task_a, timeout=1.0)
                self.assertTrue(ws_a.closed)
                self.assertFalse(ws_b.closed)

                ws_b._queue.put_nowait(
                    "@id=m3;display-name=Cara :cara!c@c.tmi.twitch.tv PRIVMSG #beta :still alive"
                )
                await asyncio.sleep(0.05)

                await mon_b.aclose()
                await asyncio.wait_for(task_b, timeout=1.0)

            self.assertEqual(pipe_a.messages, ["hello alpha"])
            self.assertEqual(pipe_b.messages, ["hello beta", "still alive"])
            # Quiet mode suppressed raw chat stdout printing
            self.assertEqual(buf.getvalue(), "")


class TestGeminiPerStreamFairnessAndDrops(unittest.IsolatedAsyncioTestCase):
    async def test_per_stream_queue_limit_prevents_starvation_and_fires_drop_hook(self):
        gate = asyncio.Event()
        started_first = asyncio.Event()

        class _GateAnalyzer:
            def __init__(self) -> None:
                self.analyzed: list[str] = []

            async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                if event.event_id == "a0":
                    started_first.set()
                    await gate.wait()
                self.analyzed.append(event.event_id)
                return _ok_analysis(event)

            async def close(self) -> None:
                pass

        dropped: list[tuple[str, str]] = []
        results: list[AnalyzedHighlight] = []
        cfg = AnalysisConfig(queue_max_size=6, max_queued_per_stream=2, max_concurrency=1)
        analyzer = _GateAnalyzer()
        worker = HighlightAnalysisWorker(
            analyzer,
            cfg,
            sink=results.append,
            on_event_dropped=lambda ev, reason: dropped.append((ev.event_id, reason)),
        )
        await worker.start()
        try:
            # a0 enters the single worker and blocks on gate
            self.assertTrue(worker.submit(_make_event("a0", "stream_a")))
            await asyncio.wait_for(started_first.wait(), timeout=1.0)

            # Stream A queues 2 more (hitting max_queued_per_stream=2); 3rd must be rejected!
            self.assertTrue(worker.submit(_make_event("a1", "stream_a")))
            self.assertTrue(worker.submit(_make_event("a2", "stream_a")))
            self.assertFalse(worker.submit(_make_event("a3", "stream_a")))
            self.assertEqual(worker.queued_count_for_stream("stream_a"), 2)

            # Stream B is NOT starved and can queue its own 2 events cleanly!
            self.assertTrue(worker.submit(_make_event("b1", "stream_b")))
            self.assertTrue(worker.submit(_make_event("b2", "stream_b")))
            self.assertEqual(worker.queued_count_for_stream("stream_b"), 2)

            gate.set()
        finally:
            await worker.stop(drain_timeout=2.0)

        self.assertEqual(analyzer.analyzed, ["a0", "a1", "a2", "b1", "b2"])
        self.assertIn(("a3", "per_stream_queue_limit_reached"), dropped)
        self.assertEqual(worker.queued_count_for_stream("stream_a"), 0)
        self.assertEqual(worker.queued_count_for_stream("stream_b"), 0)

    async def test_shutdown_drain_timeout_invokes_on_event_dropped_for_unstarted_events(self):
        gate = asyncio.Event()
        started = asyncio.Event()

        class _SlowAnalyzer:
            async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                started.set()
                await gate.wait()
                return _ok_analysis(event)

            async def close(self) -> None:
                pass

        dropped: list[tuple[str, str]] = []
        worker = HighlightAnalysisWorker(
            _SlowAnalyzer(),
            AnalysisConfig(queue_max_size=5, max_concurrency=1),
            sink=lambda _r: None,
            on_event_dropped=lambda ev, reason: dropped.append((ev.event_id, reason)),
        )
        await worker.start()
        worker.submit(_make_event("running-1", "s1"))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        worker.submit(_make_event("queued-2", "s1"))
        await worker.stop(drain_timeout=0.02)

        self.assertIn(("queued-2", "shutdown_drain_timeout"), dropped)

    def test_jsonl_sink_is_thread_safe_for_concurrent_multi_stream_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "highlights.jsonl"
            sink = JsonlAnalysisSink(out)
            events = [_ok_analysis(_make_event(f"ev-{i}", f"stream_{i % 3}")) for i in range(60)]
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(sink, events))
            lines = out.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 60)
            parsed_ids = {json.loads(ln)["event_id"] for ln in lines}
            self.assertEqual(len(parsed_ids), 60)


class TestSharedExtractionSemaphoreAndProcessTreeCleanup(unittest.IsolatedAsyncioTestCase):
    async def test_shared_semaphore_serializes_concurrent_extractions_and_releases_on_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sem = asyncio.Semaphore(1)

            in_flight = 0
            max_in_flight = 0

            def _setup_stream(name: str, should_fail: bool = False):
                cfg = RecordingConfig(
                    buffer_dir=root / name / "buf",
                    clips_output_dir=root / name / "clips",
                )
                mgr = RollingBufferManager(cfg)
                p = root / f"{name}_seg.ts"
                p.write_bytes(b"ts")
                mgr.register_segment(
                    VideoSegment(
                        segment_id=p.name,
                        path=p,
                        session_id=1,
                        wall_closed=1020.0,
                        mono_closed=100.0,
                        media_duration=20.0,
                        ffmpeg_start_pts=0.0,
                        ffmpeg_end_pts=20.0,
                        size_bytes=10,
                    )
                )

                class _SemExtractor:
                    async def extract(self, analysis, plan, selection) -> ClipExtractionResult:
                        nonlocal in_flight, max_in_flight
                        in_flight += 1
                        max_in_flight = max(max_in_flight, in_flight)
                        try:
                            await asyncio.sleep(0.05)
                            if should_fail:
                                raise RuntimeError("simulated ffmpeg failure")
                            return ClipExtractionResult(status="extracted", event_id=analysis.event_id)
                        finally:
                            in_flight -= 1

                worker = ClipExtractionWorker(
                    _SemExtractor(),
                    mgr,
                    extraction_semaphore=sem,
                    trailing_wait_seconds=0.0,
                )
                sink = Stage5PinAndSubmitSink(lambda _ev: True, ClipWindowPlanner(cfg), mgr)
                return worker, sink, mgr

            w_a, s_a, m_a = _setup_stream("a", should_fail=True)
            w_b, s_b, m_b = _setup_stream("b", should_fail=False)
            await w_a.start()
            await w_b.start()
            try:
                ev_a = _make_event("ev-a", "a")
                ev_b = _make_event("ev-b", "b")
                s_a(ev_a)
                s_b(ev_b)
                w_a.submit(_ok_analysis(ev_a))
                w_b.submit(_ok_analysis(ev_b))
            finally:
                await asyncio.gather(w_a.stop(drain_timeout=2.0), w_b.stop(drain_timeout=2.0))

            # Strictly serialized across both streams even though A failed!
            self.assertEqual(max_in_flight, 1)
            self.assertEqual(w_a.failed_count, 1)
            self.assertEqual(w_b.extracted_count, 1)
            self.assertEqual(m_a.index.active_pins, [])
            self.assertEqual(m_b.index.active_pins, [])

    async def test_clip_extractor_cancellation_kills_ffmpeg_and_cleans_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root / "buf", clips_output_dir=root / "clips")
            mgr = RollingBufferManager(cfg)
            seg_file = root / "s1_seg_00000.ts"
            seg_file.write_bytes(b"data")
            mgr.register_segment(
                VideoSegment(
                    segment_id="s1_seg_00000.ts",
                    path=seg_file,
                    session_id=1,
                    wall_closed=1020.0,
                    mono_closed=100.0,
                    media_duration=20.0,
                    ffmpeg_start_pts=0.0,
                    ffmpeg_end_pts=20.0,
                    size_bytes=4,
                )
            )
            ev = _make_event("ev-cancel", "kaicenat")
            plan = ClipWindowPlanner(cfg).plan(ev)
            sel = mgr.query_range(1000.0, 1020.0)

            started = asyncio.Event()
            killed = False

            class _SlowProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None
                    self._done = asyncio.Event()

                async def communicate(self):
                    started.set()
                    await self._done.wait()
                    return b"", b""

                def kill(self) -> None:
                    nonlocal killed
                    killed = True
                    self.returncode = -9
                    self._done.set()

                async def wait(self) -> int:
                    await self._done.wait()
                    assert self.returncode is not None
                    return self.returncode

            async def _spawn(cmd: list[str]):
                Path(cmd[-1]).write_bytes(b"partial-tmp")
                return _SlowProc()

            extractor = ClipExtractor(cfg, ffmpeg_bin="/fake/ffmpeg", process_factory=_spawn)
            task = asyncio.create_task(extractor.extract(_ok_analysis(ev), plan, sel))
            await asyncio.wait_for(started.wait(), timeout=1.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            self.assertTrue(killed)
            leftovers = [p for p in (root / "clips").rglob("*") if p.is_file()]
            self.assertEqual(leftovers, [])

    @unittest.skipUnless(os.name == "posix", "POSIX process groups required")
    async def test_posix_process_group_kill_terminates_child_and_grandchild_no_orphans(self):
        with tempfile.TemporaryDirectory() as tmp:
            grandchild_pid_file = Path(tmp) / "grandchild.pid"
            # Spawn a session-leader parent that spawns a long-lived grandchild process
            script = (
                "import subprocess, sys, time; "
                "gc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                f"open({str(grandchild_pid_file)!r}, 'w').write(str(gc.pid)); "
                "time.sleep(30)"
            )
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                script,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 3.0
                while not grandchild_pid_file.exists() and time.monotonic() < deadline:
                    await asyncio.sleep(0.02)
                self.assertTrue(grandchild_pid_file.exists())
                gc_pid = int(grandchild_pid_file.read_text().strip())

                # Kill the entire process group via _signal_process_tree
                _signal_process_tree(proc, signal.SIGKILL)
                await asyncio.wait_for(proc.wait(), timeout=2.0)

                # Wait briefly for OS to reap grandchild and verify it is DEAD (no orphan!)
                dead = False
                for _ in range(40):
                    try:
                        os.kill(gc_pid, 0)
                        # On Linux containers, killed processes become zombies until reaped by PID 1
                        proc_stat = Path(f"/proc/{gc_pid}/status")
                        if proc_stat.is_file():
                            stat_text = proc_stat.read_text(encoding="utf-8", errors="ignore")
                            if "State:\tZ" in stat_text or "State:\tX" in stat_text:
                                dead = True
                                break
                    except ProcessLookupError:
                        dead = True
                        break
                    await asyncio.sleep(0.02)
                self.assertTrue(dead, f"Grandchild pid={gc_pid} survived process-group kill!")
            finally:
                if proc.returncode is None:
                    _signal_process_tree(proc, signal.SIGKILL)
                    await proc.wait()

    async def test_stream_recorder_hard_cancellation_kills_both_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root)
            mgr = RollingBufferManager(cfg)

            class _Stuck:
                def __init__(self) -> None:
                    self.returncode: int | None = None
                    self.stderr = None
                    self.terminated = False
                    self.killed = False
                    self._done = asyncio.Event()

                def terminate(self) -> None:
                    self.terminated = True

                def kill(self) -> None:
                    self.killed = True
                    self.returncode = -9
                    self._done.set()

                async def wait(self) -> int:
                    await self._done.wait()
                    assert self.returncode is not None
                    return self.returncode

            sl, ff = _Stuck(), _Stuck()
            spawned = asyncio.Event()

            async def _factory(_sl, _ff):
                spawned.set()
                return PipelineProcesses(streamlink=sl, ffmpeg=ff)

            rec = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_factory,
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=0.1,
            )
            await rec.start()
            await asyncio.wait_for(spawned.wait(), timeout=1.0)
            assert rec._task is not None
            rec._task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await rec._task
            # Even after hard cancellation, both children were terminated/killed cleanly
            self.assertTrue(sl.killed)
            self.assertTrue(ff.killed)


class TestStage611Hardening(unittest.IsolatedAsyncioTestCase):
    """Targeted Stage 6.1.1 audit-fix regression tests."""

    def test_token_rotation_persists_rotated_refresh_token_when_validate_fails_transiently(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / ".tokens.json"
            tokens_file.write_text(
                json.dumps(
                    {
                        "access_token": "old_access",
                        "refresh_token": "old_refresh",
                        "login": "myuser",
                        "scopes": ["chat:read", "clips:edit"],
                        "expires_at": time.time() - 60,
                    }
                )
            )
            settings = _settings(Path(tmp))

            post_resp = mock.Mock(status_code=200, text="ok")
            post_resp.json.return_value = {
                "access_token": "rotated_access",
                "refresh_token": "rotated_refresh",
                "expires_in": 3600,
            }

            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch("requests.post", return_value=post_resp) as m_post,
                mock.patch(
                    "requests.get",
                    side_effect=requests.ConnectionError("validate network blip"),
                ),
            ):
                with self.assertRaises(user_token.TransientTokenError):
                    user_token.get_valid_access_token(settings)
                self.assertEqual(m_post.call_count, 1)

                # Rotated access & refresh tokens + preserved login/scopes are atomically persisted!
                saved = json.loads(tokens_file.read_text(encoding="utf-8"))
                self.assertEqual(saved["access_token"], "rotated_access")
                self.assertEqual(saved["refresh_token"], "rotated_refresh")
                self.assertEqual(saved["login"], "myuser")
                self.assertEqual(saved["scopes"], ["chat:read", "clips:edit"])
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(tokens_file.stat().st_mode), 0o600)
                # No leftover atomic temp files.
                leftover_tmps = list(Path(tmp).glob(".tokens.*.tmp"))
                self.assertEqual(leftover_tmps, [])

                # Subsequent call immediately succeeds from disk without re-refreshing.
                tok = user_token.get_valid_access_token(settings)
                self.assertEqual(tok["access_token"], "rotated_access")
                self.assertEqual(tok["login"], "myuser")
                self.assertEqual(tok["scopes"], ["chat:read", "clips:edit"])
                self.assertEqual(m_post.call_count, 1)

    def test_validation_http_4xx_clears_tokens_and_raises_reauth_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / ".tokens.json"
            tokens_file.write_text(
                json.dumps(
                    {
                        "access_token": "old_access",
                        "refresh_token": "old_refresh",
                        "login": "myuser",
                        "scopes": ["chat:read"],
                        "expires_at": time.time() - 60,
                    }
                )
            )
            settings = _settings(Path(tmp))

            post_resp = mock.Mock(status_code=200, text="ok")
            post_resp.json.return_value = {
                "access_token": "bad_access",
                "refresh_token": "new_refresh",
                "expires_in": 3600,
            }
            val_401 = mock.Mock(status_code=401, text="invalid access token")
            val_401.json.return_value = {"message": "invalid access token"}

            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch("requests.post", return_value=post_resp),
                mock.patch("requests.get", return_value=val_401),
            ):
                with self.assertRaises(user_token.ReauthRequired):
                    user_token.get_valid_access_token(settings)
                self.assertFalse(tokens_file.exists())

    def test_concurrent_async_oauth_calls_from_separate_event_loop_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / ".tokens.json"
            tokens_file.write_text(
                json.dumps(
                    {
                        "access_token": "old_access",
                        "refresh_token": "old_refresh",
                        "login": "tester",
                        "scopes": ["chat:read", "clips:edit"],
                        "expires_at": time.time() - 60,
                    }
                )
            )
            settings = _settings(Path(tmp))
            refresh_calls = 0
            count_lock = threading.Lock()

            def _slow_post(_data: dict) -> dict:
                nonlocal refresh_calls
                time.sleep(0.06)
                with count_lock:
                    refresh_calls += 1
                    n = refresh_calls
                return {
                    "access_token": f"loop_access_{n}",
                    "refresh_token": f"loop_refresh_{n}",
                    "expires_in": 3600,
                }

            def _fake_validate(_settings: Settings, tok_resp: dict) -> dict:
                data = {
                    "access_token": tok_resp["access_token"],
                    "refresh_token": tok_resp["refresh_token"],
                    "login": "tester",
                    "scopes": ["chat:read", "clips:edit"],
                    "expires_at": time.time() + 3600,
                }
                user_token._save_tokens(data)
                return {
                    "access_token": data["access_token"],
                    "login": "tester",
                    "scopes": data["scopes"],
                }

            barrier = threading.Barrier(4)

            def _thread_runner() -> dict:
                barrier.wait(timeout=2.0)
                return asyncio.run(user_token.get_valid_access_token_async(settings))

            with (
                mock.patch.object(user_token, "_TOKENS_PATH", tokens_file),
                mock.patch.object(user_token, "_post_token", side_effect=_slow_post),
                mock.patch.object(user_token, "_validate_and_store", side_effect=_fake_validate),
                concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool,
            ):
                futures = [pool.submit(_thread_runner) for _ in range(4)]
                results = [f.result(timeout=5.0) for f in futures]

            self.assertEqual(refresh_calls, 1)
            self.assertEqual({r["access_token"] for r in results}, {"loop_access_1"})

    async def test_inflight_gemini_cancellation_invokes_drop_callback_once_and_releases_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root / "buf", clips_output_dir=root / "clips")
            mgr = RollingBufferManager(cfg)
            seg_file = root / "s1_seg_00000.ts"
            seg_file.write_bytes(b"ts")
            mgr.register_segment(
                VideoSegment(
                    segment_id="s1_seg_00000.ts",
                    path=seg_file,
                    session_id=1,
                    wall_closed=1030.0,
                    mono_closed=100.0,
                    media_duration=30.0,
                    ffmpeg_start_pts=0.0,
                    ffmpeg_end_pts=30.0,
                    size_bytes=2,
                )
            )

            started = asyncio.Event()
            never = asyncio.Event()

            class _HangingAnalyzer:
                async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                    started.set()
                    await never.wait()
                    return _ok_analysis(event)

                async def close(self) -> None:
                    pass

            dropped: list[tuple[str, str]] = []
            emitted: list[AnalyzedHighlight] = []

            def _on_drop(ev: HighlightEvent, reason: str) -> None:
                dropped.append((ev.event_id, reason))
                mgr.unpin_event(ev.event_id)

            worker = HighlightAnalysisWorker(
                _HangingAnalyzer(),
                AnalysisConfig(queue_max_size=5, max_concurrency=1),
                sink=emitted.append,
                on_event_dropped=_on_drop,
            )
            pin_sink = Stage5PinAndSubmitSink(worker.submit, ClipWindowPlanner(cfg), mgr)

            await worker.start()
            self.assertTrue(pin_sink(_make_event("inflight-1", "s1")))
            await asyncio.wait_for(started.wait(), timeout=1.0)
            self.assertTrue(pin_sink(_make_event("queued-2", "s1")))
            self.assertEqual(len(mgr.index.active_pins), 2)

            await worker.stop(drain_timeout=0.02)

            # Both the cancelled in-flight event and unstarted queued event fired drop ONCE each.
            self.assertEqual(dropped.count(("inflight-1", "shutdown_drain_timeout")), 1)
            self.assertEqual(dropped.count(("queued-2", "shutdown_drain_timeout")), 1)
            self.assertEqual(len(dropped), 2)
            # Zero double-notification to sink, and all rolling-buffer pins released!
            self.assertEqual(emitted, [])
            self.assertEqual(mgr.index.active_pins, [])

    async def test_drop_and_failed_notifications_are_mutually_exclusive(self):
        # 1) Cooldown & CPM budget -> FAILED to sink ONLY, zero on_event_dropped calls.
        now = 100.0

        class _RateAnalyzer:
            async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                if event.event_id == "rl-1":
                    raise GeminiRateLimitError("429")
                return _ok_analysis(event)

            async def close(self) -> None:
                pass

        dropped: list[tuple[str, str]] = []
        emitted: list[AnalyzedHighlight] = []
        worker = HighlightAnalysisWorker(
            _RateAnalyzer(),
            AnalysisConfig(max_concurrency=1, max_calls_per_minute=1, rate_limit_cooldown_seconds=30.0),
            sink=emitted.append,
            clock=lambda: now,
            on_event_dropped=lambda ev, r: dropped.append((ev.event_id, r)),
        )
        await worker.start()
        try:
            worker.submit(_make_event("rl-1", "s1"))
            await asyncio.sleep(0.03)
            worker.submit(_make_event("rl-cooldown", "s1"))
            await asyncio.sleep(0.03)
            # Advance past cooldown, hit CPM budget (max_calls_per_minute=1 already used by rl-1? No, acquired at t=100; advance to t=200 for clean CPM test)
            now = 200.0
            worker.submit(_make_event("cpm-ok", "s1"))
            await asyncio.sleep(0.03)
            worker.submit(_make_event("cpm-over", "s1"))
            await asyncio.sleep(0.03)
        finally:
            await worker.stop(drain_timeout=2.0)

        by_id = {r.event_id: r for r in emitted}
        self.assertEqual(by_id["rl-cooldown"].status, AnalysisStatus.FAILED)
        self.assertIn("rate_limit_cooldown", by_id["rl-cooldown"].skip_or_error_reason or "")
        self.assertEqual(by_id["cpm-over"].status, AnalysisStatus.FAILED)
        self.assertEqual(by_id["cpm-over"].skip_or_error_reason, "calls_per_minute_budget_exceeded")
        self.assertEqual(dropped, [])

        # 2) Already-queued event when auth circuit breaker trips -> FAILED to sink ONLY.
        auth_gate = asyncio.Event()
        auth_started = asyncio.Event()

        class _AuthFailAnalyzer:
            async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                auth_started.set()
                await auth_gate.wait()
                raise GeminiAuthError("invalid key")

            async def close(self) -> None:
                pass

        auth_dropped: list[tuple[str, str]] = []
        auth_emitted: list[AnalyzedHighlight] = []
        auth_worker = HighlightAnalysisWorker(
            _AuthFailAnalyzer(),
            AnalysisConfig(max_concurrency=1),
            sink=auth_emitted.append,
            on_event_dropped=lambda ev, r: auth_dropped.append((ev.event_id, r)),
        )
        await auth_worker.start()
        try:
            self.assertTrue(auth_worker.submit(_make_event("auth-1", "s1")))
            await asyncio.wait_for(auth_started.wait(), timeout=1.0)
            # Queue auth-2 BEFORE auth-1 trips the circuit breaker.
            self.assertTrue(auth_worker.submit(_make_event("auth-2", "s1")))
            auth_gate.set()
            await asyncio.sleep(0.05)
            # Submit auth-3 AFTER breaker tripped -> synchronous reject -> on_event_dropped only.
            self.assertFalse(auth_worker.submit(_make_event("auth-3", "s1")))
        finally:
            await auth_worker.stop(drain_timeout=2.0)

        auth_by_id = {r.event_id: r for r in auth_emitted}
        self.assertEqual(auth_by_id["auth-1"].status, AnalysisStatus.FAILED)
        self.assertEqual(auth_by_id["auth-2"].status, AnalysisStatus.FAILED)
        self.assertEqual(auth_by_id["auth-2"].skip_or_error_reason, "gemini_auth_disabled")
        self.assertNotIn("auth-3", auth_by_id)
        self.assertEqual(auth_dropped, [("auth-3", "gemini_auth_disabled")])

    async def test_drop_callback_exception_does_not_break_worker_submit_or_shutdown(self):
        started = asyncio.Event()
        never = asyncio.Event()

        class _BlockingAnalyzer:
            def __init__(self) -> None:
                self.closed = False

            async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
                started.set()
                await never.wait()
                return _ok_analysis(event)

            async def close(self) -> None:
                self.closed = True

        drop_calls: list[tuple[str, str]] = []

        def _exploding_drop(ev: HighlightEvent, reason: str) -> None:
            drop_calls.append((ev.event_id, reason))
            raise RuntimeError("drop callback exploded")

        analyzer = _BlockingAnalyzer()
        worker = HighlightAnalysisWorker(
            analyzer,
            AnalysisConfig(queue_max_size=2, max_queued_per_stream=1, max_concurrency=1),
            sink=lambda _r: None,
            on_event_dropped=_exploding_drop,
        )
        await worker.start()
        self.assertTrue(worker.submit(_make_event("run-1", "s1")))
        await asyncio.wait_for(started.wait(), timeout=1.0)
        self.assertTrue(worker.submit(_make_event("q-s1", "s1")))
        # Per-stream rejection triggers exploding callback -> submit must still return False cleanly.
        self.assertFalse(worker.submit(_make_event("q-s1-over", "s1")))
        self.assertTrue(worker.submit(_make_event("q-s2", "s2")))
        # Global queue full eviction triggers exploding callback -> must still succeed!
        self.assertTrue(worker.submit(_make_event("q-s3", "s3")))

        # Shutdown drain timeout triggers exploding callback on in-flight + remaining queued items!
        await worker.stop(drain_timeout=0.02)

        self.assertTrue(analyzer.closed)
        self.assertEqual(worker.queued_count_for_stream("s1"), 0)
        self.assertEqual(worker.queued_count_for_stream("s2"), 0)
        self.assertEqual(worker.queued_count_for_stream("s3"), 0)
        self.assertIn(("q-s1-over", "per_stream_queue_limit_reached"), drop_calls)
        self.assertIn(("q-s1", "queue_full_evicted_oldest"), drop_calls)
        self.assertIn(("run-1", "shutdown_drain_timeout"), drop_calls)
        self.assertIn(("q-s2", "shutdown_drain_timeout"), drop_calls)
        self.assertIn(("q-s3", "shutdown_drain_timeout"), drop_calls)

    async def test_chat_aclose_during_websocket_handshake_exits_cleanly_without_sending_irc(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            entering = asyncio.Event()
            hang = asyncio.Event()
            sent: list[str] = []
            aexit_called = False

            class _HangingConnectCM:
                async def __aenter__(self):
                    entering.set()
                    await hang.wait()
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    nonlocal aexit_called
                    aexit_called = True

                async def send(self, msg: str) -> None:
                    sent.append(msg)

            mon = ChatMonitor(settings, "alpha", print_messages=False)
            with (
                mock.patch(
                    "app.chat.twitch_chat.get_valid_access_token_async",
                    return_value={"access_token": "tok", "login": "tester", "scopes": ["chat:read"]},
                ),
                mock.patch("app.chat.twitch_chat.websockets.connect", return_value=_HangingConnectCM()),
            ):
                task = asyncio.create_task(mon.run())
                await asyncio.wait_for(entering.wait(), timeout=1.0)
                t0 = time.monotonic()
                await mon.aclose()
                await asyncio.wait_for(task, timeout=1.0)
                elapsed = time.monotonic() - t0

            self.assertLess(elapsed, 0.5)
            self.assertEqual(sent, [])

            # Also verify same-tick connect completion after _stop closes socket cleanly.
            aexit_called = False

            class _LateCompleteCM:
                async def __aenter__(self):
                    mon._stop = True
                    return self

                async def __aexit__(self, exc_type, exc, tb):
                    nonlocal aexit_called
                    aexit_called = True

                async def send(self, msg: str) -> None:
                    sent.append(msg)

            mon = ChatMonitor(settings, "alpha", print_messages=False)
            with (
                mock.patch(
                    "app.chat.twitch_chat.get_valid_access_token_async",
                    return_value={"access_token": "tok", "login": "tester", "scopes": ["chat:read"]},
                ),
                mock.patch("app.chat.twitch_chat.websockets.connect", return_value=_LateCompleteCM()),
            ):
                await asyncio.wait_for(mon.run(), timeout=1.0)

            self.assertTrue(aexit_called)
            self.assertEqual(sent, [])

    async def test_chat_aclose_interrupts_reconnect_backoff_sleep_and_recovers_from_transient_token_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = _settings(Path(tmp))
            token_attempted = asyncio.Event()

            async def _transient_fail(_settings: Settings) -> dict:
                token_attempted.set()
                raise user_token.TransientTokenError("twitch 503")

            mon = ChatMonitor(settings, "alpha", print_messages=False)
            mon._backoff = 30  # Long backoff sleep that MUST be interrupted immediately!

            with mock.patch(
                "app.chat.twitch_chat.get_valid_access_token_async",
                side_effect=_transient_fail,
            ):
                task = asyncio.create_task(mon.run())
                await asyncio.wait_for(token_attempted.wait(), timeout=1.0)
                await asyncio.sleep(0.03)  # Now sleeping inside 30s reconnect backoff.
                self.assertFalse(task.done(), "ChatMonitor should back off on TransientTokenError, not exit.")
                t0 = time.monotonic()
                await mon.aclose()
                await asyncio.wait_for(task, timeout=1.0)
                elapsed = time.monotonic() - t0

            self.assertLess(elapsed, 0.5)

    async def test_default_spawn_pipeline_cancellation_between_spawns_kills_streamlink_and_closes_fds(self):
        ff_spawning = asyncio.Event()
        never = asyncio.Event()
        closed_fds: list[int] = []
        created_fds: list[int] = []

        class _FakeSL:
            def __init__(self) -> None:
                self.returncode: int | None = None
                self.killed = False
                self._done = asyncio.Event()

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9
                self._done.set()

            async def wait(self) -> int:
                await self._done.wait()
                assert self.returncode is not None
                return self.returncode

        fake_sl = _FakeSL()
        spawn_count = 0

        async def _fake_create_subprocess(*args, **kwargs):
            nonlocal spawn_count
            spawn_count += 1
            if spawn_count == 1:
                return fake_sl
            ff_spawning.set()
            await never.wait()

        real_pipe = os.pipe
        real_close = os.close

        def _tracked_pipe():
            r, w = real_pipe()
            created_fds.extend([r, w])
            return r, w

        def _tracked_close(fd: int):
            closed_fds.append(fd)
            return real_close(fd)

        with (
            mock.patch("app.recording.stream_recorder.os.pipe", side_effect=_tracked_pipe),
            mock.patch("app.recording.stream_recorder.os.close", side_effect=_tracked_close),
            mock.patch(
                "app.recording.stream_recorder.asyncio.create_subprocess_exec",
                side_effect=_fake_create_subprocess,
            ),
        ):
            task = asyncio.create_task(_default_spawn_pipeline(["sl"], ["ff"]))
            await asyncio.wait_for(ff_spawning.wait(), timeout=1.0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        self.assertTrue(fake_sl.killed)
        self.assertEqual(fake_sl.returncode, -9)
        self.assertEqual(set(created_fds), set(closed_fds))

    async def test_stream_recorder_double_cancel_during_teardown_kills_children_and_awaits_stderr_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root)
            mgr = RollingBufferManager(cfg)

            class _SlowStderr:
                def __init__(self) -> None:
                    self._never = asyncio.Event()

                async def readline(self) -> bytes:
                    await self._never.wait()
                    return b""

            class _StubbornProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None
                    self.stderr = _SlowStderr()
                    self.terminated = False
                    self.killed = False
                    self.wait_entered = asyncio.Event()
                    self._done = asyncio.Event()

                def terminate(self) -> None:
                    self.terminated = True

                def kill(self) -> None:
                    self.killed = True
                    self.returncode = -9
                    self._done.set()

                async def wait(self) -> int:
                    self.wait_entered.set()
                    await self._done.wait()
                    assert self.returncode is not None
                    return self.returncode

            sl, ff = _StubbornProc(), _StubbornProc()
            spawned = asyncio.Event()

            async def _factory(_sl, _ff):
                spawned.set()
                return PipelineProcesses(streamlink=sl, ffmpeg=ff)

            rec = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/sl",
                ffmpeg_bin="/fake/ff",
                pipeline_factory=_factory,
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=5.0,
            )
            await rec.start()
            await asyncio.wait_for(spawned.wait(), timeout=1.0)
            assert rec._task is not None

            # First cancel enters _terminate_pipeline and blocks in sl.wait().
            rec._task.cancel()
            await asyncio.wait_for(sl.wait_entered.wait(), timeout=1.0)

            # Second cancel interrupts teardown -> must SIGKILL both and clean up all tasks!
            rec._task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(rec._task, timeout=1.0)

            self.assertTrue(sl.killed)
            self.assertTrue(ff.killed)
            self.assertIsNone(rec._active_procs)
            # Confirm no lingering child/stderr tasks remain attached to this recorder.
            current = asyncio.current_task()
            other_tasks = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
            self.assertEqual(other_tasks, [])

    async def test_shared_semaphore_not_held_during_trailing_segment_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sem = asyncio.Semaphore(1)

            # Stream A has an initial segment ending at 1015.0, BEFORE requested_clip_end (~1045.0).
            cfg_a = RecordingConfig(buffer_dir=root / "a" / "buf", clips_output_dir=root / "a" / "clips")
            mgr_a = RollingBufferManager(cfg_a)
            p_a1 = root / "a_seg_1.ts"
            p_a1.write_bytes(b"ts")
            mgr_a.register_segment(
                VideoSegment(
                    segment_id=p_a1.name,
                    path=p_a1,
                    session_id=1,
                    wall_closed=1015.0,
                    mono_closed=100.0,
                    media_duration=15.0,
                    ffmpeg_start_pts=0.0,
                    ffmpeg_end_pts=15.0,
                    size_bytes=2,
                )
            )

            # Stream B already has full coverage past requested_clip_end.
            cfg_b = RecordingConfig(buffer_dir=root / "b" / "buf", clips_output_dir=root / "b" / "clips")
            mgr_b = RollingBufferManager(cfg_b)
            p_b1 = root / "b_seg_1.ts"
            p_b1.write_bytes(b"ts")
            mgr_b.register_segment(
                VideoSegment(
                    segment_id=p_b1.name,
                    path=p_b1,
                    session_id=1,
                    wall_closed=1080.0,
                    mono_closed=100.0,
                    media_duration=80.0,
                    ffmpeg_start_pts=0.0,
                    ffmpeg_end_pts=80.0,
                    size_bytes=2,
                )
            )

            order: list[str] = []
            b_extracted = asyncio.Event()

            class _OrderExtractor:
                async def extract(self, analysis, plan, selection) -> ClipExtractionResult:
                    order.append(analysis.event_id)
                    if analysis.event_id == "ev-b":
                        b_extracted.set()
                    return ClipExtractionResult(status="extracted", event_id=analysis.event_id)

            w_a = ClipExtractionWorker(
                _OrderExtractor(),
                mgr_a,
                extraction_semaphore=sem,
                trailing_wait_seconds=5.0,
                trailing_poll_seconds=0.02,
            )
            w_b = ClipExtractionWorker(
                _OrderExtractor(),
                mgr_b,
                extraction_semaphore=sem,
                trailing_wait_seconds=0.0,
            )
            sink_a = Stage5PinAndSubmitSink(lambda _ev: True, ClipWindowPlanner(cfg_a), mgr_a)
            sink_b = Stage5PinAndSubmitSink(lambda _ev: True, ClipWindowPlanner(cfg_b), mgr_b)

            await w_a.start()
            await w_b.start()
            try:
                ev_a = _make_event("ev-a", "a")
                ev_b = _make_event("ev-b", "b")
                sink_a(ev_a)
                sink_b(ev_b)

                # Start Stream A first -> it enters _wait_for_trailing_segments()!
                w_a.submit(_ok_analysis(ev_a))
                await asyncio.sleep(0.05)

                # Now submit Stream B -> MUST acquire semaphore and finish while A is STILL waiting!
                w_b.submit(_ok_analysis(ev_b))
                await asyncio.wait_for(b_extracted.wait(), timeout=1.0)
                self.assertEqual(order, ["ev-b"])

                # Now flush Stream A's trailing segment so A finishes cleanly too.
                p_a2 = root / "a_seg_2.ts"
                p_a2.write_bytes(b"ts")
                mgr_a.register_segment(
                    VideoSegment(
                        segment_id=p_a2.name,
                        path=p_a2,
                        session_id=1,
                        wall_closed=1080.0,
                        mono_closed=165.0,
                        media_duration=65.0,
                        ffmpeg_start_pts=15.0,
                        ffmpeg_end_pts=80.0,
                        size_bytes=2,
                    )
                )
            finally:
                await asyncio.gather(w_a.stop(drain_timeout=2.0), w_b.stop(drain_timeout=2.0))

            self.assertEqual(order, ["ev-b", "ev-a"])
            self.assertEqual(w_a.extracted_count, 1)
            self.assertEqual(w_b.extracted_count, 1)
            self.assertEqual(mgr_a.index.active_pins, [])
            self.assertEqual(mgr_b.index.active_pins, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
