"""Offline integration tests for monitor_chat.py wiring (RECORDING_ENABLED=false & true)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import monitor_chat
from app.analysis import CompositeHighlightSink
from app.config import Settings
from app.recording import RecorderDependencyError, Stage5PinAndSubmitSink


def _make_settings(
    root: Path,
    *,
    gemini_enabled: bool = True,
    gemini_api_key: str | None = "fake-key",
    recording_enabled: bool = False,
) -> Settings:
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
        gemini_api_key=gemini_api_key,
        gemini_enabled=gemini_enabled,
        highlights_output_path=root / "highlights.jsonl",
        recording_enabled=recording_enabled,
        recording_buffer_dir=root / ".buffer",
        clips_output_dir=root / "clips",
    )


class TestMonitorChatWiring(unittest.TestCase):
    def test_recording_disabled_keeps_stage4_wiring_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _make_settings(root, recording_enabled=False)

            rec = monitor_chat._build_recording_runtime(settings, "kaicenat", gemini_active=True)
            self.assertIsNone(rec)

            worker = monitor_chat._build_worker(settings, clip_worker=None)
            self.assertIsNotNone(worker)
            pipeline = monitor_chat._build_pipeline(settings, "kaicenat", worker=worker, recording=None)
            self.assertIsInstance(pipeline._sink, CompositeHighlightSink)
            # Second sink is the Gemini worker directly (not Stage5PinAndSubmitSink)
            self.assertIs(pipeline._sink._sinks[1], worker)

    def test_recording_enabled_requires_gemini_active_and_falls_back_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _make_settings(root, gemini_api_key=None, recording_enabled=True)

            rec = monitor_chat._build_recording_runtime(settings, "kaicenat", gemini_active=False)
            self.assertIsNone(rec)
            worker = monitor_chat._build_worker(settings)
            self.assertIsNone(worker)

    def test_recording_enabled_wires_isolated_run_dir_pin_sink_and_clip_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _make_settings(root, recording_enabled=True)

            rec = monitor_chat._build_recording_runtime(
                settings,
                "kaicenat",
                gemini_active=True,
                streamlink_bin="/fake/streamlink",
                ffmpeg_bin="/fake/ffmpeg",
            )
            self.assertIsNotNone(rec)
            assert rec is not None
            # Isolated per-run directory: .buffer/kaicenat/<run_id>
            self.assertEqual(rec.run_dir.parent, root / ".buffer" / "kaicenat")
            self.assertNotEqual(rec.run_dir.name, "kaicenat")

            worker = monitor_chat._build_worker(settings, clip_worker=rec.clip_worker)
            self.assertIsNotNone(worker)
            assert worker is not None
            # CompositeAnalysisSink includes logging, JSONL, AND clip_worker
            self.assertIn(rec.clip_worker, worker._sink._sinks)

            pipeline = monitor_chat._build_pipeline(settings, "kaicenat", worker=worker, recording=rec)
            self.assertIsInstance(pipeline._sink, CompositeHighlightSink)
            self.assertIsInstance(pipeline._sink._sinks[1], Stage5PinAndSubmitSink)

            # Cleanup helper removes empty run dir & finished CSVs
            rec.run_dir.mkdir(parents=True, exist_ok=True)
            (rec.run_dir / "s1_segments.csv").write_text("s1_seg_00000.ts,0,4\n")
            monitor_chat._cleanup_run_buffer_dir(rec)
            self.assertFalse(rec.run_dir.exists())
            self.assertFalse((root / ".buffer" / "kaicenat").exists())

    def test_recording_enabled_missing_binaries_raises_dependency_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _make_settings(root, recording_enabled=True)
            with mock.patch(
                "monitor_chat.find_streamlink_binary",
                side_effect=RecorderDependencyError("missing streamlink"),
            ):
                with self.assertRaises(RecorderDependencyError):
                    monitor_chat._build_recording_runtime(settings, "kaicenat", gemini_active=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
