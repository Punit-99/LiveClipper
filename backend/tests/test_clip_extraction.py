"""Offline unit & async integration tests for Stage 5.4 ClipExtractor, ClipExtractionWorker & Stage5PinAndSubmitSink."""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.analysis.models import (
    AnalysisStatus,
    AnalyzedHighlight,
    GeminiHighlightVerdict,
    HighlightCategory,
)
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent
from app.recording import (
    ClipExtractionError,
    ClipExtractionResult,
    ClipExtractionTimeoutError,
    ClipExtractionWorker,
    ClipExtractor,
    ClipWindowPlan,
    ClipWindowPlanner,
    PinState,
    RecordingConfig,
    RollingBufferManager,
    SegmentSelection,
    Stage5PinAndSubmitSink,
    VideoSegment,
    build_clip_output_paths,
)


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(candidate).exists():
            return candidate
    return None


FFMPEG = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")


def _sample_event(event_id: str = "abcdef1234567890", trigger: float = 1018.0) -> HighlightEvent:
    msgs = [
        ChatMessage("u1", "wait watch this", trigger - 4.0),
        ChatMessage("u2", "LMAOOOOO NO WAY", trigger + 0.5, emote_count=2),
        ChatMessage("u3", "CLIP IT!", trigger + 1.5, emote_count=1),
    ]
    return HighlightEvent(
        stream_id="kaicenat",
        streamer_name="kaicenat",
        event_id=event_id,
        start_timestamp=trigger - 10.0,
        end_timestamp=trigger + 20.0,
        trigger_timestamp=trigger,
        messages=msgs,
        message_count=len(msgs),
        unique_chatters=3,
        emote_count=3,
        activity_score=18.0,
        detection_signals=DetectionSignals(triggered=["rate_spike"]),
    )


def _analyzed(
    event: HighlightEvent,
    *,
    status: AnalysisStatus = AnalysisStatus.ANALYZED,
    should_clip: bool = True,
) -> AnalyzedHighlight:
    verdict = (
        GeminiHighlightVerdict(
            is_highlight=should_clip,
            should_clip=should_clip,
            highlight_score=85 if should_clip else 25,
            confidence=0.92,
            category=(
                HighlightCategory.FUNNY_MOMENT
                if should_clip
                else HighlightCategory.SPAM_OR_FALSE_POSITIVE
            ),
            is_spam_or_false_positive=not should_clip,
            title="Chat laughs at chaotic GTA 6 fail" if should_clip else "Not a highlight",
            reasoning="Viewers collectively laughed and asked to clip the moment.",
            reaction_summary="Laughter and hype",
            key_quotes=["LMAOOOOO NO WAY", "CLIP IT!"],
        )
        if status == AnalysisStatus.ANALYZED
        else None
    )
    return AnalyzedHighlight.from_event(
        event,
        status=status,
        model_used="gemini-3.1-flash-lite",
        verdict=verdict,
        latency_ms=1500.0,
        skip_or_error_reason=None if status == AnalysisStatus.ANALYZED else "test_skip_or_fail",
    )


class _HangingProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.killed = False
        self._done = asyncio.Event()

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._done.wait()
        return b"", b"killed"

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._done.set()

    async def wait(self) -> int:
        await self._done.wait()
        assert self.returncode is not None
        return self.returncode


class TestClipExtractorUnit(unittest.IsolatedAsyncioTestCase):
    def test_output_path_formatting_sanitizes_tokens(self):
        mp4, js = build_clip_output_paths(
            Path("/tmp/clips"), "#KaiCenat", "fde3d5c34c02499b", 1795014461.0, "funny_moment"
        )
        self.assertEqual(mp4.parent, Path("/tmp/clips/kaicenat"))
        self.assertTrue(mp4.name.endswith("_fde3d5c3_funny_moment.mp4"))
        self.assertEqual(js, mp4.with_suffix(".json"))

    async def test_zero_coverage_and_insufficient_media_skip_cleanly(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_ext_skip_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root / "buf", clips_output_dir=root / "clips")
            mgr = RollingBufferManager(cfg)
            planner = ClipWindowPlanner(cfg)
            ev = _sample_event(trigger=1015.0)
            plan = planner.plan(ev)
            extractor = ClipExtractor(cfg, ffmpeg_bin="/fake/ffmpeg", min_media_seconds=2.0)

            # 1) Zero coverage
            sel_empty = mgr.query_range(plan.requested_clip_start, plan.requested_clip_end)
            res_empty = await extractor.extract(_analyzed(ev), plan, sel_empty)
            self.assertEqual(res_empty.status, "skipped_no_coverage")
            self.assertIsNone(res_empty.clip_path)

            # 2) Below minimum media threshold (1.0s segment < 2.0s min)
            seg_file = root / "s1_seg_00000.ts"
            seg_file.write_bytes(b"data")
            mgr.register_segment(
                VideoSegment(
                    segment_id="s1_seg_00000.ts",
                    path=seg_file,
                    session_id=1,
                    wall_closed=plan.requested_clip_start + 1.0,
                    mono_closed=10.0,
                    media_duration=1.0,
                    ffmpeg_start_pts=0.0,
                    ffmpeg_end_pts=1.0,
                    size_bytes=4,
                )
            )
            sel_short = mgr.query_range(plan.requested_clip_start, plan.requested_clip_end)
            res_short = await extractor.extract(_analyzed(ev), plan, sel_short)
            self.assertEqual(res_short.status, "skipped_insufficient_media")
            self.assertFalse((root / "clips").exists())

    async def test_timeout_kills_ffmpeg_and_cleans_temp_files(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_ext_to_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root / "buf", clips_output_dir=root / "clips")
            mgr = RollingBufferManager(cfg)
            seg_file = root / "s1_seg_00000.ts"
            seg_file.write_bytes(b"video")
            mgr.register_segment(
                VideoSegment(
                    segment_id="s1_seg_00000.ts",
                    path=seg_file,
                    session_id=1,
                    wall_closed=1010.0,
                    mono_closed=10.0,
                    media_duration=6.0,
                    ffmpeg_start_pts=0.0,
                    ffmpeg_end_pts=6.0,
                    size_bytes=5,
                )
            )
            ev = _sample_event(trigger=1010.0)
            plan = ClipWindowPlanner(cfg).plan(ev)
            sel = mgr.query_range(1004.0, 1010.0)

            hanging = _HangingProcess()

            async def _spawn(cmd: list[str]) -> _HangingProcess:
                # Simulate FFmpeg creating a partial .tmp.mp4 before hanging
                Path(cmd[-1]).write_bytes(b"partial")
                return hanging

            extractor = ClipExtractor(
                cfg,
                ffmpeg_bin="/fake/ffmpeg",
                timeout_seconds=0.05,
                process_factory=_spawn,
            )
            with self.assertRaises(ClipExtractionTimeoutError):
                await extractor.extract(_analyzed(ev), plan, sel)

            self.assertTrue(hanging.killed)
            # Zero leftover .tmp.mp4 or .concat.txt files
            leftovers = list((root / "clips").rglob("*"))
            files_only = [p for p in leftovers if p.is_file()]
            self.assertEqual(files_only, [])


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe required for real extraction test")
class TestClipExtractorRealFfmpeg(unittest.IsolatedAsyncioTestCase):
    async def test_real_ffmpeg_extraction_produces_synced_mp4_and_json_sidecar(self):
        assert FFMPEG is not None and FFPROBE is not None
        with tempfile.TemporaryDirectory(prefix="liveclipper_ext_real_") as tmp:
            root = Path(tmp)
            seg_dir = root / "buffer"
            seg_dir.mkdir()
            csv_path = seg_dir / "segments.csv"

            # Generate 20s synthetic H.264/AAC MPEG-TS -> five 4s .ts segments
            p_gen = subprocess.Popen(
                [
                    FFMPEG, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=20",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=20",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
                    "-c:a", "aac", "-b:a", "128k",
                    "-f", "mpegts", "pipe:1",
                ],
                stdout=subprocess.PIPE,
            )
            p_seg = subprocess.Popen(
                [
                    FFMPEG, "-hide_banner", "-loglevel", "error",
                    "-i", "pipe:0", "-c", "copy",
                    "-f", "stream_segment", "-segment_time", "4", "-reset_timestamps", "1",
                    "-segment_list", str(csv_path), "-segment_list_type", "csv",
                    str(seg_dir / "s1_seg_%05d.ts"),
                ],
                stdin=p_gen.stdout,
            )
            assert p_gen.stdout is not None
            p_gen.stdout.close()
            self.assertEqual(p_seg.wait(), 0)
            self.assertEqual(p_gen.wait(), 0)

            cfg = RecordingConfig(
                buffer_dir=seg_dir,
                clips_output_dir=root / "clips",
                reaction_offset_seconds=2.0,
                clip_pre_seconds=4.0,
                clip_post_seconds=5.0,
                clip_reencode=True,
            )
            mgr = RollingBufferManager(cfg)
            base_wall = 1000.0
            for name, s_s, e_s in csv.reader(csv_path.read_text().splitlines()):
                s_pts, e_pts = float(s_s), float(e_s)
                p = seg_dir / name
                mgr.register_segment(
                    VideoSegment(
                        segment_id=name,
                        path=p,
                        session_id=1,
                        wall_closed=base_wall + e_pts,
                        mono_closed=100.0 + e_pts,
                        media_duration=e_pts - s_pts,
                        ffmpeg_start_pts=s_pts,
                        ffmpeg_end_pts=e_pts,
                        size_bytes=p.stat().st_size,
                    )
                )

            ev = _sample_event(event_id="fde3d5c34c02499b", trigger=1011.5)
            # Trigger 1011.5 - offset 2.0 = 1009.5 -> clip [1005.5 .. 1014.5] (9.0s)
            plan = ClipWindowPlanner(cfg).plan(ev)
            sel = mgr.query_range(plan.requested_clip_start, plan.requested_clip_end)

            extractor = ClipExtractor(cfg, ffmpeg_bin=FFMPEG)
            res = await extractor.extract(_analyzed(ev), plan, sel)

            self.assertEqual(res.status, "extracted")
            assert res.clip_path is not None and res.metadata_path is not None
            self.assertTrue(res.clip_path.is_file())
            self.assertTrue(res.metadata_path.is_file())
            # No temp files left behind
            leftovers = [p.name for p in res.clip_path.parent.iterdir()]
            self.assertEqual(sorted(leftovers), sorted([res.clip_path.name, res.metadata_path.name]))

            # Verify JSON sidecar contents
            sidecar = json.loads(res.metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar["event_id"], "fde3d5c34c02499b")
            self.assertEqual(sidecar["clip_path"], str(res.clip_path))
            self.assertTrue(sidecar["reencoded"])
            self.assertEqual(sidecar["analysis"]["highlight_score"], 85)
            self.assertEqual(sidecar["clip_plan"]["reaction_offset_seconds"], 2.0)
            self.assertFalse(sidecar["selection"]["coverage"]["is_partial"])

            # Verify MP4 duration and A/V sync via ffprobe
            probe = json.loads(
                subprocess.check_output(
                    [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(res.clip_path)],
                    text=True,
                )
            )
            v = next(s for s in probe["streams"] if s["codec_type"] == "video")
            a = next(s for s in probe["streams"] if s["codec_type"] == "audio")
            self.assertAlmostEqual(float(probe["format"]["duration"]), 9.0, delta=0.15)
            self.assertLessEqual(abs(float(v["start_time"]) - float(a["start_time"])) * 1000, 50.0)
            self.assertLessEqual(abs(float(v["duration"]) - float(a["duration"])) * 1000, 50.0)


class TestClipWorkerAndPinSink(unittest.IsolatedAsyncioTestCase):
    async def test_pin_lifecycle_pending_approved_extracting_released_and_rejects_unpin(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_wk_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(
                buffer_dir=root / "buf",
                clips_output_dir=root / "clips",
                reaction_offset_seconds=2.0,
                clip_pre_seconds=4.0,
                clip_post_seconds=4.0,
            )
            mgr = RollingBufferManager(cfg)
            for i in range(6):
                p = root / f"s1_seg_{i:05d}.ts"
                p.write_bytes(b"ts")
                mgr.register_segment(
                    VideoSegment(
                        segment_id=p.name,
                        path=p,
                        session_id=1,
                        wall_closed=1004.0 + i * 4.0,
                        mono_closed=104.0 + i * 4.0,
                        media_duration=4.0,
                        ffmpeg_start_pts=i * 4.0,
                        ffmpeg_end_pts=(i + 1) * 4.0,
                        size_bytes=100,
                    )
                )

            observed_states: list[PinState] = []
            gate = asyncio.Event()
            started = asyncio.Event()

            class _StubExtractor:
                def __init__(self) -> None:
                    self.calls: list[str] = []

                async def extract(
                    self, analysis: AnalyzedHighlight, plan: ClipWindowPlan, selection: SegmentSelection
                ) -> ClipExtractionResult:
                    self.calls.append(analysis.event_id)
                    pin = mgr.get_pin(analysis.event_id)
                    assert pin is not None
                    observed_states.append(pin.state)
                    started.set()
                    await gate.wait()
                    return ClipExtractionResult(
                        status="extracted",
                        event_id=analysis.event_id,
                        clip_path=root / f"{analysis.event_id}.mp4",
                    )

            extractor = _StubExtractor()
            worker = ClipExtractionWorker(
                extractor, mgr, trailing_wait_seconds=0.0, max_concurrency=1
            )
            planner = ClipWindowPlanner(cfg)
            sink = Stage5PinAndSubmitSink(lambda _ev: True, planner, mgr)

            await worker.start()
            try:
                # 1) Approved highlight: PENDING_ANALYSIS -> APPROVED -> EXTRACTING -> RELEASED
                ev_ok = _sample_event("ev-good", trigger=1012.0)
                self.assertTrue(sink(ev_ok))
                self.assertEqual(mgr.get_pin("ev-good").state, PinState.PENDING_ANALYSIS)

                # Submit approved verdict -> immediately promotes to APPROVED before worker finishes
                self.assertTrue(worker.submit(_analyzed(ev_ok, should_clip=True)))
                await asyncio.wait_for(started.wait(), timeout=1.0)
                self.assertEqual(observed_states, [PinState.EXTRACTING])
                gate.set()

                # 2) Rejected, prefilter-skipped, and failed verdicts -> unpin immediately, 0 extractor calls
                for ev_id, status, clip in [
                    ("ev-noclip", AnalysisStatus.ANALYZED, False),
                    ("ev-prefilt", AnalysisStatus.SKIPPED_PREFILTER, False),
                    ("ev-failed", AnalysisStatus.FAILED, False),
                ]:
                    ev = _sample_event(ev_id, trigger=1012.0)
                    sink(ev)
                    self.assertEqual(mgr.get_pin(ev_id).state, PinState.PENDING_ANALYSIS)
                    self.assertFalse(worker.submit(_analyzed(ev, status=status, should_clip=clip)))
                    self.assertEqual(mgr.get_pin(ev_id).state, PinState.RELEASED)
            finally:
                await worker.stop(drain_timeout=2.0)

            self.assertEqual(extractor.calls, ["ev-good"])
            self.assertEqual(mgr.get_pin("ev-good").state, PinState.RELEASED)
            self.assertEqual(worker.extracted_count, 1)
            self.assertEqual(worker.rejected_unpinned_count, 3)

    async def test_extractor_failure_isolated_and_releases_pin_in_finally(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_wk_err_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root / "buf", clips_output_dir=root / "clips")
            mgr = RollingBufferManager(cfg)
            p = root / "s1_seg_00000.ts"
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
                    size_bytes=100,
                )
            )

            class _FlakyExtractor:
                def __init__(self) -> None:
                    self.calls: list[str] = []

                async def extract(self, analysis, plan, selection) -> ClipExtractionResult:
                    self.calls.append(analysis.event_id)
                    if analysis.event_id == "ev-boom":
                        raise ClipExtractionError("ffmpeg crashed")
                    return ClipExtractionResult(status="extracted", event_id=analysis.event_id)

            extractor = _FlakyExtractor()
            worker = ClipExtractionWorker(extractor, mgr, trailing_wait_seconds=0.0, max_concurrency=1)
            sink = Stage5PinAndSubmitSink(lambda _ev: True, ClipWindowPlanner(cfg), mgr)

            await worker.start()
            try:
                ev1 = _sample_event("ev-boom", trigger=1010.0)
                ev2 = _sample_event("ev-after", trigger=1010.0)
                sink(ev1)
                sink(ev2)
                worker.submit(_analyzed(ev1, should_clip=True))
                worker.submit(_analyzed(ev2, should_clip=True))
            finally:
                await worker.stop(drain_timeout=2.0)

            self.assertEqual(extractor.calls, ["ev-boom", "ev-after"])
            self.assertEqual(worker.failed_count, 1)
            self.assertEqual(worker.extracted_count, 1)
            # Both pins released cleanly even after failure!
            self.assertEqual(mgr.get_pin("ev-boom").state, PinState.RELEASED)
            self.assertEqual(mgr.get_pin("ev-after").state, PinState.RELEASED)

    async def test_waits_briefly_for_trailing_segment_before_extracting(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_wk_trail_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(
                buffer_dir=root / "buf",
                clips_output_dir=root / "clips",
                reaction_offset_seconds=0.0,
                clip_pre_seconds=4.0,
                clip_post_seconds=8.0,
            )
            mgr = RollingBufferManager(cfg)
            # Only [1000..1012] exists initially; trigger=1010 needs up to 1018!
            for i in range(3):
                p = root / f"s1_seg_{i:05d}.ts"
                p.write_bytes(b"ts")
                mgr.register_segment(
                    VideoSegment(
                        segment_id=p.name,
                        path=p,
                        session_id=1,
                        wall_closed=1004.0 + i * 4.0,
                        mono_closed=104.0 + i * 4.0,
                        media_duration=4.0,
                        ffmpeg_start_pts=i * 4.0,
                        ffmpeg_end_pts=(i + 1) * 4.0,
                        size_bytes=100,
                    )
                )

            captured_end: list[float | None] = []

            class _CaptureExtractor:
                async def extract(self, analysis, plan, selection) -> ClipExtractionResult:
                    captured_end.append(selection.coverage.actual_approx_end_wall)
                    return ClipExtractionResult(status="extracted", event_id=analysis.event_id)

            worker = ClipExtractionWorker(
                _CaptureExtractor(),
                mgr,
                trailing_wait_seconds=1.0,
                trailing_poll_seconds=0.02,
            )
            sink = Stage5PinAndSubmitSink(lambda _ev: True, ClipWindowPlanner(cfg), mgr)

            await worker.start()
            try:
                ev = _sample_event("ev-trail", trigger=1010.0)
                sink(ev)
                worker.submit(_analyzed(ev, should_clip=True))

                # Simulate trailing segments arriving 50ms after Gemini verdict
                await asyncio.sleep(0.05)
                for i in (3, 4):
                    p = root / f"s1_seg_{i:05d}.ts"
                    p.write_bytes(b"ts")
                    mgr.register_segment(
                        VideoSegment(
                            segment_id=p.name,
                            path=p,
                            session_id=1,
                            wall_closed=1004.0 + i * 4.0,
                            mono_closed=104.0 + i * 4.0,
                            media_duration=4.0,
                            ffmpeg_start_pts=i * 4.0,
                            ffmpeg_end_pts=(i + 1) * 4.0,
                            size_bytes=100,
                        )
                    )
            finally:
                await worker.stop(drain_timeout=2.0)

            self.assertEqual(len(captured_end), 1)
            self.assertGreaterEqual(captured_end[0], 1018.0)

    def test_pin_and_submit_sink_skips_pin_when_analysis_rejects_and_survives_capacity_exhaustion(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_sink_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_dir=root, max_active_pins=1)
            mgr = RollingBufferManager(cfg)
            planner = ClipWindowPlanner(cfg)

            # 1) Analysis worker rejects event (returns False) -> must NOT pin!
            sink_rej = Stage5PinAndSubmitSink(lambda _ev: False, planner, mgr)
            self.assertFalse(sink_rej(_sample_event("ev-dup")))
            self.assertIsNone(mgr.get_pin("ev-dup"))
            self.assertEqual(sink_rej.analysis_rejected_count, 1)

            # 2) Buffer pin capacity exhausted (max_active_pins=1) -> analysis STILL receives event!
            submitted_to_gemini: list[str] = []
            sink_ok = Stage5PinAndSubmitSink(
                lambda ev: (submitted_to_gemini.append(ev.event_id) or True),
                planner,
                mgr,
            )
            self.assertTrue(sink_ok(_sample_event("ev-1")))
            self.assertTrue(sink_ok(_sample_event("ev-2")))  # pin fails with BufferCapacityError
            self.assertEqual(submitted_to_gemini, ["ev-1", "ev-2"])
            self.assertEqual(sink_ok.pinned_count, 1)
            self.assertEqual(sink_ok.pin_rejected_capacity_count, 1)
            self.assertIsNotNone(mgr.get_pin("ev-1"))
            self.assertIsNone(mgr.get_pin("ev-2"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
