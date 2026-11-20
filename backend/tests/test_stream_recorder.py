"""Offline unit & async tests for Stage 5.3 StreamRecorder (mocked subprocesses)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.recording import (
    PipelineProcesses,
    RecorderDependencyError,
    RecordingConfig,
    RollingBufferManager,
    StreamRecorder,
    build_ffmpeg_segment_command,
    build_streamlink_command,
    find_ffmpeg_binary,
    find_streamlink_binary,
)


class _FakeProcess:
    """Deterministic in-memory fake async subprocess for offline tests."""

    def __init__(self, *, exit_on_terminate: bool = True, name: str = "proc") -> None:
        self.name = name
        self._exit_on_terminate = exit_on_terminate
        self._returncode: int | None = None
        self._done = asyncio.Event()
        self.terminated = False
        self.killed = False
        self.terminate_order: list[str] = []
        self.stderr = None

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def finish(self, code: int = 0) -> None:
        if self._returncode is None:
            self._returncode = code
            self._done.set()

    def terminate(self) -> None:
        self.terminated = True
        self.terminate_order.append(f"term:{self.name}")
        if self._exit_on_terminate:
            self.finish(0)

    def kill(self) -> None:
        self.killed = True
        self.terminate_order.append(f"kill:{self.name}")
        self.finish(-9)

    async def wait(self) -> int:
        await self._done.wait()
        assert self._returncode is not None
        return self._returncode


class TestStreamRecorderCommandsAndDiscovery(unittest.TestCase):
    def test_streamlink_command_builder_respects_low_latency_and_omits_disable_ads(self):
        cfg_ll = RecordingConfig(quality="720p60,best", low_latency=True)
        cmd_ll = build_streamlink_command("/venv/bin/streamlink", "KaiCenat", cfg_ll)
        self.assertEqual(
            cmd_ll,
            [
                "/venv/bin/streamlink",
                "--stdout",
                "https://www.youtube.com/@KaiCenat/live",
                "720p60,best",
            ],
        )

        cfg_std = RecordingConfig(quality="best", low_latency=False)
        cmd_std = build_streamlink_command("/venv/bin/streamlink", "xqc", cfg_std)
        self.assertEqual(
            cmd_std,
            ["/venv/bin/streamlink", "--stdout", "https://www.youtube.com/@xqc/live", "best"],
        )

    def test_streamlink_youtube_channel_name_and_handle_resolution(self):
        cfg = RecordingConfig(quality="720p60,best")

        # Known channel name
        cmd_speed = build_streamlink_command("/venv/bin/streamlink", "IShowSpeed", cfg, platform="youtube")
        self.assertIn("https://www.youtube.com/channel/UCWsDFcIhY2DBi3GB5uykGXA/live", cmd_speed)

        # Handle format
        cmd_handle = build_streamlink_command("/venv/bin/streamlink", "@CustomCreator", cfg, platform="youtube")
        self.assertIn("https://www.youtube.com/@CustomCreator/live", cmd_handle)

        # Generic name
        cmd_gen = build_streamlink_command("/venv/bin/streamlink", "SomeGamer", cfg, platform="youtube")
        self.assertIn("https://www.youtube.com/@SomeGamer/live", cmd_gen)

        # Standalone video ID (11 chars)
        cmd_vid = build_streamlink_command("/venv/bin/streamlink", "dQw4w9WgXcQ", cfg, platform="youtube")
        self.assertIn("https://www.youtube.com/watch?v=dQw4w9WgXcQ", cmd_vid)

    def test_ffmpeg_command_builder_uses_verified_flags_and_per_session_filenames(self):
        cfg = RecordingConfig(segment_seconds=4.0)
        cmd, csv_path = build_ffmpeg_segment_command("/usr/bin/ffmpeg", Path("/tmp/buf"), 3, cfg)
        self.assertEqual(csv_path, Path("/tmp/buf/s3_segments.csv"))
        self.assertEqual(
            cmd,
            [
                "/usr/bin/ffmpeg",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-i",
                "pipe:0",
                "-c",
                "copy",
                "-f",
                "stream_segment",
                "-segment_time",
                "4",
                "-reset_timestamps",
                "1",
                "-segment_list",
                "/tmp/buf/s3_segments.csv",
                "-segment_list_type",
                "csv",
                "/tmp/buf/s3_seg_%05d.ts",
            ],
        )

    def test_missing_binaries_raise_actionable_dependency_error(self):
        with (
            mock.patch("app.recording.stream_recorder.shutil.which", return_value=None),
            mock.patch.object(Path, "is_file", return_value=False),
        ):
            with self.assertRaises(RecorderDependencyError) as sl_ctx:
                find_streamlink_binary()
            self.assertIn("pip install streamlink", str(sl_ctx.exception))

            with self.assertRaises(RecorderDependencyError) as ff_ctx:
                find_ffmpeg_binary()
            self.assertIn("brew install ffmpeg", str(ff_ctx.exception))


class TestStreamRecorderRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_csv_segment_ingestion_records_separate_timestamps_and_disk_sizes(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_rec_csv_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root, segment_seconds=4.0)
            clock_val = {"wall": 2000.0, "mono": 500.0}
            mgr = RollingBufferManager(cfg, mono_clock=lambda: clock_val["mono"])

            order: list[str] = []
            sl = _FakeProcess(name="sl")
            ff = _FakeProcess(name="ff")
            sl.terminate_order = order
            ff.terminate_order = order

            async def _factory(sl_cmd: list[str], ff_cmd: list[str]) -> PipelineProcesses:
                csv_path = Path(ff_cmd[ff_cmd.index("-segment_list") + 1])
                chan_dir = csv_path.parent
                # Write 2 real .ts files with distinct byte sizes + append CSV lines
                (chan_dir / "s1_seg_00000.ts").write_bytes(b"a" * 1234)
                (chan_dir / "s1_seg_00001.ts").write_bytes(b"b" * 5678)
                csv_path.write_text(
                    "s1_seg_00000.ts,0.000000,4.034000\n"
                    "s1_seg_00001.ts,4.034000,8.034000\n",
                    encoding="utf-8",
                )
                return PipelineProcesses(streamlink=sl, ffmpeg=ff)

            recorder = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/streamlink",
                ffmpeg_bin="/fake/ffmpeg",
                pipeline_factory=_factory,
                wall_clock=lambda: clock_val["wall"],
                mono_clock=lambda: clock_val["mono"],
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=0.05,
            )
            await recorder.start()
            await asyncio.sleep(0.05)
            await recorder.stop()

            segs = mgr.index.segments
            self.assertEqual(len(segs), 2)
            self.assertEqual(segs[0].segment_id, "s1_seg_00000.ts")
            self.assertEqual(segs[0].session_id, 1)
            self.assertEqual(segs[0].size_bytes, 1234)
            self.assertEqual(segs[0].wall_closed, 2000.0)
            self.assertEqual(segs[0].mono_closed, 500.0)
            self.assertAlmostEqual(segs[0].media_duration, 4.034)
            self.assertAlmostEqual(segs[0].ffmpeg_start_pts, 0.0)
            self.assertAlmostEqual(segs[0].ffmpeg_end_pts, 4.034)

            self.assertEqual(segs[1].segment_id, "s1_seg_00001.ts")
            self.assertEqual(segs[1].size_bytes, 5678)
            self.assertAlmostEqual(segs[1].media_duration, 4.0)
            # Graceful shutdown terminated Streamlink first!
            self.assertTrue(sl.terminated)
            self.assertEqual(order[0], "term:sl")

    async def test_restart_increments_session_id_and_records_discontinuity(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_rec_restart_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root, segment_seconds=4.0)
            mono = {"t": 100.0}
            wall = {"t": 1000.0}
            mgr = RollingBufferManager(cfg, mono_clock=lambda: mono["t"])

            spawn_count = 0
            second_session_seen = asyncio.Event()

            async def _factory(sl_cmd: list[str], ff_cmd: list[str]) -> PipelineProcesses:
                nonlocal spawn_count
                spawn_count += 1
                csv_path = Path(ff_cmd[ff_cmd.index("-segment_list") + 1])
                chan_dir = csv_path.parent
                sl = _FakeProcess(name=f"sl{spawn_count}")
                ff = _FakeProcess(name=f"ff{spawn_count}")

                seg_name = f"s{spawn_count}_seg_00000.ts"
                (chan_dir / seg_name).write_bytes(b"z" * 2048)
                csv_path.write_text(f"{seg_name},0.000000,4.000000\n", encoding="utf-8")

                if spawn_count == 1:
                    # Simulate Streamlink drop right after seg 0
                    async def _drop_soon() -> None:
                        await asyncio.sleep(0.025)
                        mono["t"] += 10.0
                        wall["t"] += 10.0
                        sl.finish(1)
                        ff.finish(0)

                    asyncio.create_task(_drop_soon())
                else:
                    second_session_seen.set()

                return PipelineProcesses(streamlink=sl, ffmpeg=ff)

            recorder = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/streamlink",
                ffmpeg_bin="/fake/ffmpeg",
                pipeline_factory=_factory,
                wall_clock=lambda: wall["t"],
                mono_clock=lambda: mono["t"],
                poll_interval_seconds=0.01,
                initial_backoff_seconds=0.01,
                max_backoff_seconds=0.02,
                shutdown_grace_seconds=0.05,
            )
            await recorder.start()
            await asyncio.wait_for(second_session_seen.wait(), timeout=2.0)
            await asyncio.sleep(0.04)
            await recorder.stop()

            self.assertGreaterEqual(recorder.session_id, 2)
            self.assertGreaterEqual(recorder.restart_count, 1)
            segs = mgr.index.segments
            self.assertEqual([s.session_id for s in segs[:2]], [1, 2])
            self.assertEqual(segs[0].segment_id, "s1_seg_00000.ts")
            self.assertEqual(segs[1].segment_id, "s2_seg_00000.ts")
            # Session restart must be recorded explicitly as a discontinuity
            self.assertTrue(any(d.reason == "session_restart" for d in mgr.index.discontinuities))

    async def test_stall_detection_triggers_clean_restart(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_rec_stall_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root)
            mgr = RollingBufferManager(cfg)

            procs_created: list[PipelineProcesses] = []
            restarted = asyncio.Event()

            async def _stalling_factory(sl_cmd: list[str], ff_cmd: list[str]) -> PipelineProcesses:
                sl = _FakeProcess(name="sl")
                ff = _FakeProcess(name="ff")
                pp = PipelineProcesses(streamlink=sl, ffmpeg=ff)
                procs_created.append(pp)
                if len(procs_created) >= 2:
                    restarted.set()
                return pp

            recorder = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/streamlink",
                ffmpeg_bin="/fake/ffmpeg",
                pipeline_factory=_stalling_factory,
                poll_interval_seconds=0.01,
                startup_timeout_seconds=0.06,
                stall_timeout_seconds=0.06,
                initial_backoff_seconds=0.01,
                shutdown_grace_seconds=0.05,
            )
            await recorder.start()
            await asyncio.wait_for(restarted.wait(), timeout=2.0)
            await recorder.stop()

            self.assertGreaterEqual(recorder.stall_count, 1)
            self.assertGreaterEqual(recorder.restart_count, 1)
            self.assertTrue(procs_created[0].streamlink.terminated)

    async def test_emergency_stop_halts_recorder_cleanly_without_restarting(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_rec_emerg_") as tmp:
            root = Path(tmp)
            # Tiny emergency cap: 100 bytes max, 125 bytes emergency ceiling
            cfg = RecordingConfig(
                buffer_dir=root,
                max_buffer_mb=100 / (1024 * 1024),
                emergency_buffer_multiplier=1.25,
            )
            recorder_ref: list[StreamRecorder] = []
            mgr = RollingBufferManager(
                cfg,
                on_emergency_stop=lambda reason: recorder_ref[0].request_emergency_stop(reason),
            )

            spawns = 0
            sl_proc: list[_FakeProcess] = []

            async def _factory(sl_cmd: list[str], ff_cmd: list[str]) -> PipelineProcesses:
                nonlocal spawns
                spawns += 1
                sl = _FakeProcess(name="sl")
                ff = _FakeProcess(name="ff")
                sl_proc.append(sl)
                return PipelineProcesses(streamlink=sl, ffmpeg=ff)

            recorder = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/streamlink",
                ffmpeg_bin="/fake/ffmpeg",
                pipeline_factory=_factory,
                poll_interval_seconds=0.01,
                initial_backoff_seconds=0.01,
                shutdown_grace_seconds=0.05,
            )
            recorder_ref.append(recorder)

            await recorder.start()
            await asyncio.sleep(0.03)
            # Trigger emergency stop cleanly via callback
            recorder.request_emergency_stop("Emergency disk ceiling reached")
            await asyncio.sleep(0.18)

            self.assertTrue(recorder.emergency_stopped)
            self.assertFalse(recorder.is_running)
            self.assertEqual(spawns, 1)  # Must NOT restart after emergency stop!
            self.assertEqual(recorder.restart_count, 0)
            self.assertTrue(sl_proc[0].terminated)
            await recorder.stop()

    async def test_graceful_shutdown_kills_stuck_child_processes_no_orphans(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_rec_kill_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root)
            mgr = RollingBufferManager(cfg)

            # Both children ignore SIGTERM to test kill() fallback
            sl = _FakeProcess(exit_on_terminate=False, name="stuck_sl")
            ff = _FakeProcess(exit_on_terminate=False, name="stuck_ff")

            async def _factory(sl_cmd: list[str], ff_cmd: list[str]) -> PipelineProcesses:
                return PipelineProcesses(streamlink=sl, ffmpeg=ff)

            recorder = StreamRecorder(
                "kaicenat",
                cfg,
                mgr,
                streamlink_bin="/fake/streamlink",
                ffmpeg_bin="/fake/ffmpeg",
                pipeline_factory=_factory,
                poll_interval_seconds=0.01,
                shutdown_grace_seconds=0.1,
            )
            await recorder.start()
            await asyncio.sleep(0.03)
            await recorder.stop()

            self.assertTrue(sl.terminated)
            self.assertTrue(sl.killed)
            self.assertTrue(ff.terminated)
            self.assertTrue(ff.killed)
            self.assertIsNotNone(sl.returncode)
            self.assertIsNotNone(ff.returncode)


if __name__ == "__main__":
    unittest.main(verbosity=2)
