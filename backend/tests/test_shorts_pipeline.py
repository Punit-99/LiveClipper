"""Unit tests for ShortsPipeline, manifest lifecycle, and CLI tools.

All tests are 100% offline using mocked bridge and YouTube uploader.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.shorts.openshorts_bridge import OpenShortsBridge
from app.shorts.pipeline import (
    STATE_APPROVED,
    STATE_DISCOVERED,
    STATE_FAILED,
    STATE_PROCESSED,
    STATE_REJECTED,
    STATE_UPLOADED,
    ShortsManifest,
    ShortsPipeline,
)
from app.shorts.youtube_uploader import DailyQuotaTracker, QuotaExceededError, YouTubeUploader


class TestShortsManifest(unittest.TestCase):
    """Tests for ShortsManifest persistence and lifecycle state transitions."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.manifest_path = Path(self.tmp_dir.name) / "manifest.jsonl"
        self.manifest = ShortsManifest(self.manifest_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_lifecycle_transitions(self):
        # 1. Discovered
        self.manifest.update_record(
            clip_id="c100",
            channel="kaicenat",
            state=STATE_DISCOVERED,
            highlight_score=85,
        )
        rec = self.manifest.get("c100")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["state"], STATE_DISCOVERED)

        # 2. Processed
        self.manifest.update_record(
            clip_id="c100",
            state=STATE_PROCESSED,
            short_video_path="/path/to/c100_short.mp4",
        )
        rec = self.manifest.get("c100")
        self.assertEqual(rec["state"], STATE_PROCESSED)
        self.assertEqual(rec["short_video_path"], "/path/to/c100_short.mp4")

        # 3. Approved
        self.manifest.update_record(clip_id="c100", state=STATE_APPROVED)
        self.assertEqual(self.manifest.get("c100")["state"], STATE_APPROVED)

        # 4. Uploaded
        self.manifest.update_record(
            clip_id="c100",
            state=STATE_UPLOADED,
            youtube_video_id="yt_vid_abc",
        )
        rec = self.manifest.get("c100")
        self.assertEqual(rec["state"], STATE_UPLOADED)
        self.assertEqual(rec["youtube_video_id"], "yt_vid_abc")

    def test_persistence_across_instances(self):
        self.manifest.update_record(clip_id="p1", channel="xqc", state=STATE_PROCESSED)
        self.manifest.update_record(clip_id="p2", channel="kaicenat", state=STATE_DISCOVERED)

        # Load fresh manifest instance pointing to same file
        reloaded = ShortsManifest(self.manifest_path)
        all_recs = reloaded.all_records()
        self.assertEqual(len(all_recs), 2)
        self.assertEqual(reloaded.get("p1")["state"], STATE_PROCESSED)
        self.assertEqual(reloaded.get("p2")["state"], STATE_DISCOVERED)


class TestShortsPipelineProcessing(unittest.TestCase):
    """Tests for scanning, batch processing, single clip, idempotent skips, and failure isolation."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.clips_dir = self.work_dir / "clips"
        self.shorts_dir = self.work_dir / "shorts"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.shorts_dir.mkdir(parents=True, exist_ok=True)

        # Create dummy clips
        self._create_dummy_clip("kaicenat", "clip_kai_1", score=85, title="Kai Funny Moment")
        self._create_dummy_clip("xqc", "clip_xqc_2", score=90, title="XQC Fail")
        self._create_dummy_clip("xqc", "clip_xqc_3", score=50, title="Low Score Chat Spike")

        self.mock_bridge = MagicMock(spec=OpenShortsBridge)
        # Mock bridge process_clip returns (Path, Path, dict)
        def _fake_process(clip_path, sidecar_path_or_data=None, channel=None, **kwargs):
            p = Path(clip_path)
            ch = channel or "streamer"
            short_mp4 = self.shorts_dir / ch / f"{p.stem}_short.mp4"
            short_json = self.shorts_dir / ch / f"{p.stem}_short.json"
            short_mp4.parent.mkdir(parents=True, exist_ok=True)
            short_mp4.touch()
            short_json.touch()
            return short_mp4, short_json, {
                "engine": "ffmpeg_fallback",
                "final_duration_seconds": 45.0,
            }

        self.mock_bridge.process_clip.side_effect = _fake_process

        self.mock_viral = MagicMock()
        mock_meta = MagicMock()
        mock_meta.to_dict.return_value = {
            "titles": [{"title": "Viral Mock Title #Shorts", "hook_type": "question"}],
            "description": "Mock description",
            "tags": ["shorts", "stream"],
        }
        mock_meta.titles = [{"title": "Viral Mock Title #Shorts", "hook_type": "question"}]
        self.mock_viral.generate_metadata.return_value = mock_meta
        self.mock_viral.generate_viral_metadata.return_value = {
            "title": "Viral Mock Title #Shorts",
            "description": "Mock description",
            "tags": ["shorts", "stream"],
            "model": "mock",
        }

        self.pipeline = ShortsPipeline(
            bridge=self.mock_bridge,
            clips_dir=self.clips_dir,
            shorts_dir=self.shorts_dir,
            viral_generator=self.mock_viral,
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_dummy_clip(self, channel: str, clip_id: str, score: int, title: str) -> Path:
        ch_dir = self.clips_dir / channel
        ch_dir.mkdir(parents=True, exist_ok=True)
        mp4_path = ch_dir / f"20261118_180000_{clip_id}_funny.mp4"
        mp4_path.touch()

        sidecar_path = mp4_path.with_suffix(".json")
        data = {
            "clip_id": clip_id,
            "streamer_name": channel,
            "analysis": {
                "title": title,
                "highlight_score": score,
                "category": "funny_moment",
            },
        }
        with open(sidecar_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return mp4_path

    def test_scan_clips_discovers_all(self):
        discovered = self.pipeline.scan_clips()
        self.assertEqual(len(discovered), 3)
        cids = {d["clip_id"] for d in discovered}
        self.assertEqual(cids, {"clip_kai_1", "clip_xqc_2", "clip_xqc_3"})

    def test_batch_processing_processes_all_candidates(self):
        summary = self.pipeline.process_pending_shorts()
        self.assertEqual(summary["found"], 3)
        self.assertEqual(summary["processed"], 3)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(self.mock_bridge.process_clip.call_count, 3)

        # Check manifest state
        for cid in ("clip_kai_1", "clip_xqc_2", "clip_xqc_3"):
            rec = self.pipeline.manifest.get(cid)
            self.assertEqual(rec["state"], STATE_PROCESSED)

    def test_idempotent_rerun_skips_processed_clips(self):
        # First run processes 3
        self.pipeline.process_pending_shorts()
        self.assertEqual(self.mock_bridge.process_clip.call_count, 3)

        # Second run should skip all 3
        summary = self.pipeline.process_pending_shorts()
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(summary["skipped"], 3)
        self.assertEqual(self.mock_bridge.process_clip.call_count, 3)  # No new calls

    def test_single_clip_mode(self):
        summary = self.pipeline.process_pending_shorts(clip_id_or_file="clip_kai_1")
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(self.mock_bridge.process_clip.call_count, 1)

        kai_rec = self.pipeline.manifest.get("clip_kai_1")
        self.assertEqual(kai_rec["state"], STATE_PROCESSED)
        xqc_rec = self.pipeline.manifest.get("clip_xqc_2")
        self.assertEqual(xqc_rec["state"], STATE_DISCOVERED)

    def test_channel_filter(self):
        summary = self.pipeline.process_pending_shorts(channel="kaicenat")
        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["candidates"], 1)

    def test_failure_isolation(self):
        # Clip 2 fails during processing
        def _faulty_process(clip_path, sidecar_path_or_data=None, channel=None, **kwargs):
            if "clip_xqc_2" in str(clip_path):
                raise RuntimeError("FFmpeg decode crash")
            p = Path(clip_path)
            ch = channel or "streamer"
            short_mp4 = self.shorts_dir / ch / f"{p.stem}_short.mp4"
            short_mp4.parent.mkdir(parents=True, exist_ok=True)
            short_mp4.touch()
            return short_mp4, short_mp4.with_suffix(".json"), {"engine": "ffmpeg"}

        self.mock_bridge.process_clip.side_effect = _faulty_process

        summary = self.pipeline.process_pending_shorts()
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["failed"], 1)

        self.assertEqual(self.pipeline.manifest.get("clip_kai_1")["state"], STATE_PROCESSED)
        self.assertEqual(self.pipeline.manifest.get("clip_xqc_2")["state"], STATE_FAILED)
        self.assertIn("FFmpeg decode crash", self.pipeline.manifest.get("clip_xqc_2")["error"])
        self.assertEqual(self.pipeline.manifest.get("clip_xqc_3")["state"], STATE_PROCESSED)

    def test_dry_run_mode_has_no_side_effects(self):
        summary = self.pipeline.process_pending_shorts(dry_run=True)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["candidates"], 3)
        self.assertEqual(summary["processed"], 0)
        self.assertEqual(self.mock_bridge.process_clip.call_count, 0)
        self.assertEqual(self.pipeline.manifest.get("clip_kai_1")["state"], STATE_DISCOVERED)


class TestShortsPipelineUpload(unittest.TestCase):
    """Tests for score filtering, interactive/auto upload, and quota exhaustion."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.clips_dir = self.work_dir / "clips"
        self.shorts_dir = self.work_dir / "shorts"
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.shorts_dir.mkdir(parents=True, exist_ok=True)

        self.mock_uploader = MagicMock(spec=YouTubeUploader)
        self.mock_quota = MagicMock(spec=DailyQuotaTracker)
        self.mock_quota.can_upload.return_value = (True, "OK")
        self.mock_quota.get_status.return_value = {
            "daily_limit": 5,
            "uploads_today": 0,
            "units_used": 0,
            "max_units": 10000,
        }
        self.mock_uploader.quota_tracker = self.mock_quota

        self.mock_viral = MagicMock()
        mock_meta = MagicMock()
        mock_meta.to_dict.return_value = {
            "titles": [{"title": "Test Title #Shorts", "hook_type": "question"}],
            "description": "Test Desc",
            "tags": ["shorts"],
        }
        mock_meta.titles = [{"title": "Test Title #Shorts", "hook_type": "question"}]
        self.mock_viral.generate_metadata.return_value = mock_meta
        self.mock_viral.generate_viral_metadata.return_value = {
            "title": "Test Title #Shorts",
            "description": "Test Desc",
            "tags": ["shorts"],
        }

        self.pipeline = ShortsPipeline(
            uploader=self.mock_uploader,
            clips_dir=self.clips_dir,
            shorts_dir=self.shorts_dir,
            viral_generator=self.mock_viral,
        )

        # Setup 2 processed shorts: 1 high score (88), 1 low score (65)
        self.vid1 = self.shorts_dir / "kaicenat" / "c1_short.mp4"
        self.vid1.parent.mkdir(parents=True, exist_ok=True)
        self.vid1.touch()

        self.vid2 = self.shorts_dir / "xqc" / "c2_short.mp4"
        self.vid2.parent.mkdir(parents=True, exist_ok=True)
        self.vid2.touch()

        self.pipeline.manifest.update_record(
            clip_id="c1",
            channel="kaicenat",
            highlight_score=88,
            title="High Score Highlight",
            state=STATE_PROCESSED,
            short_video_path=str(self.vid1),
        )
        self.pipeline.manifest.update_record(
            clip_id="c2",
            channel="xqc",
            highlight_score=65,
            title="Low Score Moment",
            state=STATE_PROCESSED,
            short_video_path=str(self.vid2),
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_score_filtering_excludes_low_scores(self):
        # min_score = 75
        candidates = self.pipeline.list_upload_candidates(min_score=75)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["clip_id"], "c1")

    def test_upload_auto_mode(self):
        self.mock_uploader.upload_video.return_value = {
            "video_id": "yt_xyz_1",
            "url": "https://www.youtube.com/shorts/yt_xyz_1",
        }

        summary = self.pipeline.upload_shorts(min_score=75, auto_upload=True)
        self.assertEqual(summary["uploaded"], 1)
        self.assertEqual(self.mock_uploader.upload_video.call_count, 1)

        rec = self.pipeline.manifest.get("c1")
        self.assertEqual(rec["state"], STATE_UPLOADED)
        self.assertEqual(rec["youtube_video_id"], "yt_xyz_1")

    def test_interactive_approval_and_rejection(self):
        self.mock_uploader.upload_video.return_value = {
            "video_id": "yt_appr_1",
            "url": "https://www.youtube.com/shorts/yt_appr_1",
        }

        # User approves c1
        summary = self.pipeline.upload_shorts(
            min_score=75,
            interactive=True,
            prompt_fn=lambda cand: "a",
        )
        self.assertEqual(summary["uploaded"], 1)
        self.assertEqual(self.pipeline.manifest.get("c1")["state"], STATE_UPLOADED)

        # User rejects c2 (when min_score=60)
        summary2 = self.pipeline.upload_shorts(
            min_score=60,
            clip_id="c2",
            interactive=True,
            prompt_fn=lambda cand: "r",
        )
        self.assertEqual(summary2["rejected"], 1)
        self.assertEqual(self.pipeline.manifest.get("c2")["state"], STATE_REJECTED)

    def test_quota_exhaustion_stops_uploads(self):
        self.mock_quota.can_upload.return_value = (False, "Daily upload limit reached (5/5).")
        summary = self.pipeline.upload_shorts(min_score=75, auto_upload=True)
        self.assertEqual(summary["uploaded"], 0)
        self.mock_uploader.upload_video.assert_not_called()


class TestCLIEntryPoints(unittest.TestCase):
    """Tests for CLI arguments and invocation of process_shorts and upload_shorts."""

    def test_process_shorts_help(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = "backend"
        res = subprocess.run(
            [sys.executable, "backend/process_shorts.py", "--help"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--channel", res.stdout)
        self.assertIn("--dry-run", res.stdout)
        self.assertIn("--engine", res.stdout)

    def test_upload_shorts_help(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = "backend"
        res = subprocess.run(
            [sys.executable, "backend/upload_shorts.py", "--help"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("--channel", res.stdout)
        self.assertIn("--list", res.stdout)
        self.assertIn("--auto", res.stdout)
        self.assertIn("--min-score", res.stdout)


if __name__ == "__main__":
    unittest.main()
