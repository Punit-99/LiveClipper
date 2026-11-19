"""Unit and media integration tests for OpenShorts bridge and duration adapter.

Tests duration clamping, FFmpeg 9:16 vertical reframing, banner overlays,
Docker bridge command construction, engine fallback behavior, and directory structure.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.shorts.duration_adapter import (
    adapt_clip_duration,
    calculate_trim_window,
    probe_video_duration,
)
from app.shorts.openshorts_bridge import (
    EngineUnavailableError,
    OpenShortsBridge,
    OpenShortsBridgeError,
)
from app.shorts.text_overlay import create_hook_banner_bmp


def _create_synthetic_clip(path: Path, duration: float = 2.0, size: str = "1280x720") -> Path:
    """Generate a quick synthetic 16:9 test pattern video using FFmpeg."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={size}:rate=30",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-t",
        str(duration),
        str(path),
    ]
    subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15.0)
    return path


class TestDurationAdapter(unittest.TestCase):
    """Tests for duration clamping, target_pts centering, and FFmpeg trimming."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_calculate_trim_window_centered(self):
        start, end = calculate_trim_window(
            duration=90.0,
            target_pts=50.0,
            target_duration=55.0,
        )
        self.assertEqual(start, 22.5)
        self.assertEqual(end, 77.5)
        self.assertEqual(round(end - start, 1), 55.0)

    def test_calculate_trim_window_clamps_near_start(self):
        start, end = calculate_trim_window(
            duration=90.0,
            target_pts=10.0,
            target_duration=55.0,
        )
        self.assertEqual(start, 0.0)
        self.assertEqual(end, 55.0)

    def test_calculate_trim_window_clamps_near_end(self):
        start, end = calculate_trim_window(
            duration=90.0,
            target_pts=85.0,
            target_duration=55.0,
        )
        self.assertEqual(start, 35.0)
        self.assertEqual(end, 90.0)

    def test_calculate_trim_window_absolute_epoch_target(self):
        start, end = calculate_trim_window(
            duration=90.0,
            target_pts=1795026347.0,
            target_duration=55.0,
            clip_start_wall=1795026300.0,  # Rel target is 47.0s
        )
        self.assertEqual(start, 19.5)
        self.assertEqual(end, 74.5)

    def test_clip_under_60s_returns_original_path_unchanged(self):
        clip = _create_synthetic_clip(self.work_dir / "short_clip.mp4", duration=2.0)
        res_path, was_trimmed, meta = adapt_clip_duration(
            clip_path=clip,
            max_duration=60.0,
            target_duration=55.0,
        )
        self.assertEqual(res_path, clip)
        self.assertFalse(was_trimmed)
        self.assertEqual(meta["final_duration"], 2.0)

    def test_clip_over_threshold_produces_trimmed_file(self):
        # Create a 4.0s clip, adapt with max_duration=2.5s and target_duration=2.0s
        clip = _create_synthetic_clip(self.work_dir / "long_clip.mp4", duration=4.0)
        res_path, was_trimmed, meta = adapt_clip_duration(
            clip_path=clip,
            max_duration=2.5,
            target_duration=2.0,
            output_dir=self.work_dir / "trimmed",
        )
        self.assertTrue(was_trimmed)
        self.assertNotEqual(res_path, clip)
        self.assertTrue(res_path.is_file())
        final_dur = probe_video_duration(res_path)
        self.assertAlmostEqual(final_dur, 2.0, delta=0.3)


class TestFFmpegFallbackEngine(unittest.TestCase):
    """Media integration tests for pure FFmpeg 9:16 vertical conversion."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.bridge = OpenShortsBridge(
            output_dir=self.work_dir / "shorts",
            engine="ffmpeg_fallback",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_vertical_reframing_produces_valid_1080x1920_mp4(self):
        clip = _create_synthetic_clip(self.work_dir / "test_16x9.mp4", duration=1.5, size="1280x720")
        sidecar = {
            "clip_id": "test01",
            "streamer_name": "kaicenat",
            "analysis": {"title": "Funny Drift Moment"},
        }

        short_vid, short_json, meta = self.bridge.process_clip(
            clip_path=clip,
            sidecar_path_or_data=sidecar,
            channel="kaicenat",
        )

        self.assertTrue(short_vid.is_file())
        self.assertTrue(short_json.is_file())
        self.assertEqual(meta["engine"], "ffmpeg_fallback")

        # Verify output resolution using ffprobe
        probe_cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name",
            "-of",
            "csv=p=0",
            str(short_vid),
        ]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        codec, width, height = res.stdout.strip().split(",")
        self.assertEqual(codec, "h264")
        self.assertEqual(width, "1080")
        self.assertEqual(height, "1920")

    def test_text_overlay_bmp_generation(self):
        bmp_path = self.work_dir / "test_hook.bmp"
        res = create_hook_banner_bmp(
            text="CHAT LOSES IT OVER BUG",
            output_path=bmp_path,
            width=960,
            height=200,
            scale=4,
        )
        self.assertTrue(res.is_file())
        # Check BMP header size (54 header + 40 DIB + 960*200*4 bytes)
        expected_size = 54 + (960 * 200 * 4)
        self.assertEqual(res.stat().st_size, expected_size)


class TestOpenShortsBridgeEngines(unittest.TestCase):
    """Tests for engine resolution, Docker command building, and fallback handling."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_invalid_engine_raises_value_error(self):
        with self.assertRaises(ValueError):
            OpenShortsBridge(output_dir=self.work_dir, engine="invalid_engine")

    def test_openshorts_unavailable_raises_in_openshorts_mode(self):
        bridge = OpenShortsBridge(output_dir=self.work_dir, engine="openshorts")
        clip = _create_synthetic_clip(self.work_dir / "clip.mp4", duration=1.0)

        with patch.object(bridge, "can_use_openshorts", return_value=False):
            with self.assertRaises(EngineUnavailableError):
                bridge.process_clip(clip)

    def test_openshorts_docker_call_arguments(self):
        bridge = OpenShortsBridge(output_dir=self.work_dir, engine="openshorts")
        clip = _create_synthetic_clip(self.work_dir / "clip_docker.mp4", duration=1.0)
        sidecar = {
            "clip_id": "dock01",
            "streamer_name": "xqc",
            "analysis": {"title": "Epic Win"},
        }

        real_run = subprocess.run

        def _fake_run(cmd, **kwargs):
            if cmd and cmd[0] == "docker":
                out_path = bridge.output_dir / "xqc" / "dock01_short.mp4"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"fake_video_bytes")
                return MagicMock(returncode=0)
            return real_run(cmd, **kwargs)

        with patch.object(bridge, "is_docker_available", return_value=True), \
             patch("subprocess.run", side_effect=_fake_run) as mock_run:
            with patch("app.shorts.openshorts_bridge.probe_video_duration", return_value=1.0):
                bridge.process_clip(clip, sidecar_path_or_data=sidecar)

            # Find the call made to docker
            docker_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "docker"]
            self.assertTrue(len(docker_calls) >= 1)
            called_cmd = docker_calls[0][0][0]
            self.assertEqual(called_cmd[0], "docker")
            self.assertEqual(called_cmd[1], "run")
            self.assertIn("--skip-analysis", called_cmd)
            self.assertIn("AUTO_HOOK=1", " ".join(called_cmd))
            self.assertIn("VIRAL_HOOK_TEXT=Epic Win", " ".join(called_cmd))

    def test_auto_engine_falls_back_to_ffmpeg_when_docker_fails(self):
        bridge = OpenShortsBridge(output_dir=self.work_dir, engine="auto")
        clip = _create_synthetic_clip(self.work_dir / "clip_fallback.mp4", duration=1.0)
        sidecar = {
            "clip_id": "auto01",
            "streamer_name": "xqc",
            "analysis": {"title": "Wild Encounter"},
        }

        # Mock docker available, but execution throws SubprocessError
        with patch.object(bridge, "can_use_openshorts", return_value=True), \
             patch.object(bridge, "_render_with_openshorts", side_effect=subprocess.SubprocessError("Container exited 1")):
            short_vid, short_json, meta = bridge.process_clip(clip, sidecar)
            self.assertEqual(meta["engine"], "ffmpeg_fallback")
            self.assertTrue(short_vid.is_file())

    def test_openshorts_uses_internal_video_when_docker_unavailable(self):
        bridge = OpenShortsBridge(output_dir=self.work_dir, engine="openshorts", subtitle_style="none")
        clip = _create_synthetic_clip(self.work_dir / "clip_internal.mp4", duration=1.0)
        sidecar = {
            "clip_id": "int01",
            "streamer_name": "xqc",
            "analysis": {"title": "Internal Render"},
        }

        with patch.object(bridge, "is_docker_available", return_value=False):
            short_vid, short_sidecar, meta = bridge.process_clip(clip, sidecar)
            self.assertEqual(meta["engine"], "openshorts")
            self.assertTrue(short_vid.is_file())


class TestOutputStructureAndSidecar(unittest.TestCase):
    """Tests for channel folder structure and sidecar metadata enrichment."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.tmp_dir.name)
        self.bridge = OpenShortsBridge(
            output_dir=self.work_dir / "shorts",
            engine="ffmpeg_fallback",
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_output_directory_structure_and_sidecar_enrichment(self):
        clip = _create_synthetic_clip(
            self.work_dir / "clips" / "kaicenat" / "20261118_182427_ce5e4f32_funny.mp4",
            duration=1.0,
        )
        orig_sidecar = {
            "clip_id": "ce5e4f32",
            "streamer_name": "kaicenat",
            "analysis": {
                "title": "Chat Explodes Over Glitch",
                "highlight_score": 88,
            },
        }

        short_vid, short_sidecar, meta = self.bridge.process_clip(clip, orig_sidecar)

        # Expected path: shorts/kaicenat/ce5e4f32_short.mp4
        expected_vid = self.work_dir / "shorts" / "kaicenat" / "ce5e4f32_short.mp4"
        expected_json = self.work_dir / "shorts" / "kaicenat" / "ce5e4f32_short.json"

        self.assertEqual(short_vid, expected_vid)
        self.assertEqual(short_sidecar, expected_json)

        # Verify sidecar contains both original metadata and new processing section
        with open(short_sidecar, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        self.assertEqual(data["clip_id"], "ce5e4f32")
        self.assertEqual(data["streamer_name"], "kaicenat")
        self.assertEqual(data["analysis"]["highlight_score"], 88)
        self.assertIn("processing", data)
        proc = data["processing"]
        self.assertEqual(proc["engine"], "ffmpeg_fallback")
        self.assertEqual(proc["output_format"], "9:16 vertical (1080x1920)")
        self.assertEqual(proc["hook_title"], "Chat Explodes Over Glitch")
        self.assertIn("processed_at_iso", proc)

    def test_stage8_docker_parameters_and_metadata(self):
        bridge = OpenShortsBridge(
            output_dir=self.work_dir / "shorts",
            engine="openshorts",
            docker_image="liveclipper/openshorts:latest",
            docker_timeout=180.0,
            subtitle_style="hormozi",
            reframe_mode="split",
        )
        self.assertEqual(bridge.docker_image, "liveclipper/openshorts:latest")
        self.assertEqual(bridge.docker_timeout, 180.0)
        self.assertEqual(bridge.subtitle_style, "hormozi")
        self.assertEqual(bridge.reframe_mode, "split")

        clip = _create_synthetic_clip(self.work_dir / "clip_stage8.mp4", duration=1.0)
        sidecar = {
            "clip_id": "st8_01",
            "streamer_name": "kaicenat",
            "analysis": {"title": "Wild Reaction"},
        }

        real_run = subprocess.run

        def _fake_docker_run(cmd, **kwargs):
            if cmd and cmd[0] == "docker":
                out_path = bridge.output_dir / "kaicenat" / "st8_01_short.mp4"
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(b"fake_stage8_bytes")
                return MagicMock(returncode=0, stderr="", stdout="")
            return real_run(cmd, **kwargs)

        with patch.object(bridge, "is_docker_available", return_value=True), \
             patch("subprocess.run", side_effect=_fake_docker_run) as mock_run:
            with patch("app.shorts.openshorts_bridge.probe_video_duration", return_value=1.0):
                short_vid, short_sidecar, meta = bridge.process_clip(clip, sidecar_path_or_data=sidecar)

            docker_runs = [c for c in mock_run.call_args_list if c[0][0][:2] == ["docker", "run"]]
            self.assertEqual(len(docker_runs), 1)
            called_cmd = docker_runs[0][0][0]
            self.assertIn("--skip-analysis", called_cmd)
            self.assertIn("--hook-text", called_cmd)
            self.assertIn("Wild Reaction", called_cmd)
            self.assertIn("--subtitle-style", called_cmd)
            self.assertIn("hormozi", called_cmd)
            self.assertIn("--reframe-mode", called_cmd)
            self.assertIn("split", called_cmd)
            self.assertIn("liveclipper_hf_cache:/app/.cache/huggingface", " ".join(called_cmd))

            # Verify sidecar processing metadata
            self.assertEqual(meta["engine"], "openshorts")
            self.assertEqual(meta["subtitle_style"], "hormozi")
            self.assertEqual(meta["reframe_mode"], "split")
            self.assertEqual(meta["docker_image"], "liveclipper/openshorts:latest")

    def test_stage8_docker_timeout_fallback_in_auto_mode(self):
        bridge = OpenShortsBridge(
            output_dir=self.work_dir / "shorts",
            engine="auto",
            docker_timeout=1.0,
        )
        clip = _create_synthetic_clip(self.work_dir / "clip_timeout.mp4", duration=1.0)
        sidecar = {
            "clip_id": "time01",
            "streamer_name": "xqc",
            "analysis": {"title": "Timeout Test"},
        }

        real_run = subprocess.run

        def _timeout_docker(cmd, **kwargs):
            if cmd and cmd[0] == "docker":
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=1.0)
            return real_run(cmd, **kwargs)

        with patch.object(bridge, "is_docker_available", return_value=True), \
             patch("subprocess.run", side_effect=_timeout_docker), \
             patch.object(bridge, "_render_with_ffmpeg") as mock_ffmpeg:
            with patch("app.shorts.openshorts_bridge.probe_video_duration", return_value=1.0):
                def _fake_ffmpeg(in_v, out_v, title, temp_d):
                    out_v.parent.mkdir(parents=True, exist_ok=True)
                    out_v.write_bytes(b"ffmpeg_fallback_bytes")
                mock_ffmpeg.side_effect = _fake_ffmpeg

                short_vid, short_sidecar, meta = bridge.process_clip(clip, sidecar_path_or_data=sidecar)

            self.assertEqual(meta["engine"], "ffmpeg_fallback")
            mock_ffmpeg.assert_called_once()


if __name__ == "__main__":
    unittest.main()
