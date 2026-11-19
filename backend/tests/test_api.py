"""Unit tests for LiveClipper FastAPI dashboard routes."""

import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from app.api.server import FRONTEND_DIST_DIR, create_app


class TestDashboardAPI(TestCase):
    """Test suite covering the FastAPI dashboard backend wrapper."""

    def setUp(self) -> None:
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health_endpoint(self) -> None:
        """GET /api/health returns 200 OK."""
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_spa_static_serving(self) -> None:
        """GET / serves the React single-page application index.html."""
        if not FRONTEND_DIST_DIR.is_dir():
            self.skipTest("Frontend dist directory not packaged in container (served via Nginx).")
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("LiveClipper Dashboard", resp.text)
        self.assertIn("<div id=\"root\"></div>", resp.text)

    def test_status_endpoint(self) -> None:
        """GET /api/status returns monitoring status, disk breakdown, and gemini stats."""
        resp = self.client.get("/api/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("is_monitoring", data)
        self.assertIn("disk", data)
        self.assertIn("gemini", data)
        self.assertIn("clips_bytes", data["disk"])
        self.assertIn("buffer_bytes", data["disk"])

    def test_highlights_endpoint(self) -> None:
        """GET /api/highlights returns recent highlight event objects."""
        resp = self.client.get("/api/highlights?limit=10")
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.json(), list)

    def test_clips_endpoint(self) -> None:
        """GET /api/clips returns list of extracted clips."""
        resp = self.client.get("/api/clips")
        self.assertEqual(resp.status_code, 200)
        clips = resp.json()
        self.assertIsInstance(clips, list)
        if clips:
            clip = clips[0]
            self.assertIn("id", clip)
            self.assertIn("score", clip)
            self.assertIn("title", clip)
            self.assertIn("channel", clip)
            self.assertIn("platform", clip)

    def test_shorts_endpoint(self) -> None:
        """GET /api/shorts returns list of processed shorts with viral metadata."""
        resp = self.client.get("/api/shorts")
        self.assertEqual(resp.status_code, 200)
        shorts = resp.json()
        self.assertIsInstance(shorts, list)
        if shorts:
            short = shorts[0]
            self.assertIn("id", short)
            self.assertIn("score", short)
            self.assertIn("selected_title", short)
            self.assertIn("viral_titles", short)

    def test_clip_range_preview(self) -> None:
        """GET /api/clips/{id}/preview returns 206 Partial Content when Range header sent."""
        # Find an existing clip id
        resp = self.client.get("/api/clips")
        clips = resp.json()
        if not clips:
            self.skipTest("No clips available for preview test")

        clip_id = clips[0]["id"]
        # Request first 100 bytes
        preview_resp = self.client.get(
            f"/api/clips/{clip_id}/preview",
            headers={"Range": "bytes=0-99"},
        )
        self.assertEqual(preview_resp.status_code, 206)
        self.assertEqual(preview_resp.headers.get("Accept-Ranges"), "bytes")
        self.assertIn("bytes 0-99/", preview_resp.headers.get("Content-Range", ""))
        self.assertEqual(len(preview_resp.content), 100)

    def test_short_range_preview(self) -> None:
        """GET /api/shorts/{id}/preview returns 206 Partial Content when Range header sent."""
        resp = self.client.get("/api/shorts")
        shorts = resp.json()
        if not shorts:
            self.skipTest("No shorts available for preview test")

        short_id = shorts[0]["id"]
        preview_resp = self.client.get(
            f"/api/shorts/{short_id}/preview",
            headers={"Range": "bytes=0-99"},
        )
        self.assertEqual(preview_resp.status_code, 206)
        self.assertEqual(preview_resp.headers.get("Accept-Ranges"), "bytes")
        self.assertIn("bytes 0-99/", preview_resp.headers.get("Content-Range", ""))
        self.assertEqual(len(preview_resp.content), 100)

    def test_upload_quota_endpoint(self) -> None:
        """GET /api/upload/quota returns daily upload limits and unit counts."""
        resp = self.client.get("/api/upload/quota")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("uploads_today", data)
        self.assertIn("daily_limit", data)
        self.assertIn("can_upload", data)

    def test_settings_get_and_post(self) -> None:
        """GET and POST /api/settings reads and updates configuration values."""
        resp = self.client.get("/api/settings")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("platform", data)
        self.assertIn("shorts_engine", data)

        # Update test setting
        prev_quality = data.get("recording_quality")
        update_resp = self.client.post("/api/settings", json={"recording_quality": "720p60,best"})
        self.assertEqual(update_resp.status_code, 200)
        self.assertEqual(update_resp.json()["recording_quality"], "720p60,best")

        # Restore
        if prev_quality:
            self.client.post("/api/settings", json={"recording_quality": prev_quality})

    def test_delete_clip_single_and_nonexistent(self) -> None:
        """DELETE /api/clips/{id} removes files and returns 200; 404 for nonexistent."""
        # Nonexistent -> 404
        resp_404 = self.client.delete("/api/clips/nonexistent_id_99999")
        self.assertEqual(resp_404.status_code, 404)

        with tempfile.TemporaryDirectory() as tmp_clips:
            clip_path = Path(tmp_clips) / "channel1" / "20261119_testc01_moment.mp4"
            sidecar_path = Path(tmp_clips) / "channel1" / "20261119_testc01_moment.json"
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            clip_path.write_bytes(b"dummy video")
            sidecar_path.write_text(json.dumps({"clip_id": "testc01", "event_id": "testc01"}))

            with patch("app.api.routes.clips.load_settings") as mock_settings:
                cfg = MagicMock()
                cfg.clips_output_dir = Path(tmp_clips)
                mock_settings.return_value = cfg

                resp = self.client.delete("/api/clips/testc01")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json(), {"deleted": True, "id": "testc01"})
                self.assertFalse(clip_path.exists())
                self.assertFalse(sidecar_path.exists())

    def test_delete_all_clips_confirm_required(self) -> None:
        """DELETE /api/clips requires confirm=true parameter."""
        resp_bad = self.client.delete("/api/clips")
        self.assertEqual(resp_bad.status_code, 400)
        self.assertIn("confirm=true", resp_bad.json()["detail"])

        with tempfile.TemporaryDirectory() as tmp_clips:
            for i in range(3):
                p = Path(tmp_clips) / f"clip_{i}.mp4"
                p.write_bytes(b"video")
                s = Path(tmp_clips) / f"clip_{i}.json"
                s.write_text("{}")

            with patch("app.api.routes.clips.load_settings") as mock_settings:
                cfg = MagicMock()
                cfg.clips_output_dir = Path(tmp_clips)
                mock_settings.return_value = cfg

                resp = self.client.delete("/api/clips?confirm=true")
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.json()["deleted"])
                self.assertEqual(resp.json()["count"], 6)
                self.assertEqual(len(list(Path(tmp_clips).glob("*"))), 0)

    def test_delete_short_updates_manifest_and_logs_warning_if_uploaded(self) -> None:
        """DELETE /api/shorts/{id} removes files, updates manifest, and warns if uploaded."""
        # Nonexistent -> 404
        resp_404 = self.client.delete("/api/shorts/nonexistent_short_9999")
        self.assertEqual(resp_404.status_code, 404)

        with tempfile.TemporaryDirectory() as tmp_shorts:
            from app.shorts.pipeline import ShortsManifest, ShortsPipeline

            manifest_path = Path(tmp_shorts) / "manifest.jsonl"
            manifest = ShortsManifest(manifest_path)
            pipeline = ShortsPipeline(manifest=manifest, shorts_dir=Path(tmp_shorts))

            # Create dummy Short
            short_file = Path(tmp_shorts) / "ch1" / "s01_short.mp4"
            short_sidecar = Path(tmp_shorts) / "ch1" / "s01_short.json"
            short_file.parent.mkdir(parents=True, exist_ok=True)
            short_file.write_bytes(b"short video")
            short_sidecar.write_text(json.dumps({"title": "Short"}))

            manifest.update_record(
                clip_id="s01",
                channel="ch1",
                state="UPLOADED",
                youtube_id="yt_vid_abc123",
                short_video_path=str(short_file),
                short_sidecar_path=str(short_sidecar),
            )
            self.assertIsNotNone(manifest.get("s01"))

            with patch("app.api.routes.shorts.load_settings") as mock_settings, \
                 patch("app.api.routes.shorts.get_shorts_pipeline", return_value=pipeline):
                cfg = MagicMock()
                cfg.shorts_output_dir = Path(tmp_shorts)
                mock_settings.return_value = cfg

                with self.assertLogs("app.api.routes.shorts", level="WARNING") as cm:
                    resp = self.client.delete("/api/shorts/s01")
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.json(), {"deleted": True, "id": "s01"})

                # Verify warning was logged
                warning_found = any("already uploaded to YouTube" in log_msg for log_msg in cm.output)
                self.assertTrue(warning_found, f"Expected upload warning in logs, got: {cm.output}")

                # Verify files removed and manifest updated
                self.assertFalse(short_file.exists())
                self.assertFalse(short_sidecar.exists())
                self.assertIsNone(manifest.get("s01"))

    def test_delete_all_shorts_requires_confirm_and_clears_all(self) -> None:
        """DELETE /api/shorts requires confirm=true and removes all local shorts."""
        resp_bad = self.client.delete("/api/shorts")
        self.assertEqual(resp_bad.status_code, 400)

        with tempfile.TemporaryDirectory() as tmp_shorts:
            from app.shorts.pipeline import ShortsManifest, ShortsPipeline

            manifest_path = Path(tmp_shorts) / "manifest.jsonl"
            manifest = ShortsManifest(manifest_path)
            pipeline = ShortsPipeline(manifest=manifest, shorts_dir=Path(tmp_shorts))

            for i in range(2):
                vid = Path(tmp_shorts) / f"s{i}_short.mp4"
                vid.write_bytes(b"vid")
                manifest.update_record(clip_id=f"s{i}", short_video_path=str(vid))

            self.assertEqual(len(manifest.all_records()), 2)

            with patch("app.api.routes.shorts.load_settings") as mock_settings, \
                 patch("app.api.routes.shorts.get_shorts_pipeline", return_value=pipeline):
                cfg = MagicMock()
                cfg.shorts_output_dir = Path(tmp_shorts)
                mock_settings.return_value = cfg

                resp = self.client.delete("/api/shorts?confirm=true")
                self.assertEqual(resp.status_code, 200)
                self.assertTrue(resp.json()["deleted"])
                self.assertEqual(resp.json()["count"], 2)
                self.assertEqual(len(manifest.all_records()), 0)
