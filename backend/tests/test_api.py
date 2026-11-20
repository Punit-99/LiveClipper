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

    def test_channel_resolution_endpoint(self) -> None:
        """GET /api/channels/resolve?id=... resolves known channel IDs and handles unknowns."""
        # 1. Known ID
        resp = self.client.get("/api/channels/resolve?id=UCWsDFcIhY2DBi3GB5uykGXA")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], "UCWsDFcIhY2DBi3GB5uykGXA")
        self.assertEqual(data["name"], "IShowSpeed")
        self.assertTrue(data["resolved"])

        # 2. Unknown ID with no credentials / failure returns resolved=False
        with patch("app.discovery.youtube_finder.requests.Session.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_get.return_value = mock_resp

            resp_un = self.client.get("/api/channels/resolve?id=UCUnknownTestID123")
            self.assertEqual(resp_un.status_code, 200)
            data_un = resp_un.json()
            self.assertEqual(data_un["id"], "UCUnknownTestID123")
            self.assertFalse(data_un["resolved"])

        # 3. Dynamic resolution via API mock
        with patch("app.discovery.youtube_finder.YouTubeStreamFinder._get_headers_or_params", return_value=({}, {})):
            with patch("app.discovery.youtube_finder.requests.Session.get") as mock_get:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {
                    "items": [{"snippet": {"title": "Mocked Channel Official"}}]
                }
                mock_get.return_value = mock_resp

                resp_mock = self.client.get("/api/channels/resolve?id=UCMockedChannel999")
                self.assertEqual(resp_mock.status_code, 200)
                data_mock = resp_mock.json()
                self.assertEqual(data_mock["id"], "UCMockedChannel999")
                self.assertEqual(data_mock["name"], "Mocked Channel Official")
                self.assertTrue(data_mock["resolved"])

    def test_settings_excluded_channels(self) -> None:
        """POST /api/settings persists excluded_youtube_channels and excluded_target_channels."""
        payload = {
            "target_youtube_channels": "UCWsDFcIhY2DBi3GB5uykGXA",
            "excluded_youtube_channels": "UC0IWRLai-BAwci_e9MylNGw,UC7cs8q-gJRlGwj4A8OmCmXg",
            "target_channels": "kaicenat,xqc",
            "excluded_target_channels": "shroud,tarik",
            "target_languages": "en,hi,es",
        }
        resp = self.client.post("/api/settings", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["target_youtube_channels"], "UCWsDFcIhY2DBi3GB5uykGXA")
        self.assertEqual(data["excluded_youtube_channels"], "UC0IWRLai-BAwci_e9MylNGw,UC7cs8q-gJRlGwj4A8OmCmXg")
        self.assertEqual(data["target_channels"], "kaicenat,xqc")
        self.assertEqual(data["excluded_target_channels"], "shroud,tarik")
        self.assertEqual(data["target_languages"], "en,hi,es")

    def test_websocket_processing_events(self) -> None:
        """Processing a single clip broadcasts processing_started, progress, and complete events."""
        from app.api.events import ws_manager

        broadcast_events: list[tuple[str, dict]] = []

        def fake_broadcast(event_type: str, data: dict = None):
            broadcast_events.append((event_type, data or {}))

        with patch.object(ws_manager, "broadcast_sync", side_effect=fake_broadcast):
            mock_pipeline = MagicMock()
            mock_pipeline.manifest.get.return_value = {"title": "Awesome Highlight"}

            def fake_process_pending_shorts(*args, **kwargs):
                cb = kwargs.get("progress_callback")
                if cb:
                    cb("test_clip_1", "trimming", 15)
                    cb("test_clip_1", "reframing", 35)
                    cb("test_clip_1", "transcribing", 55)
                    cb("test_clip_1", "subtitles", 75)
                    cb("test_clip_1", "hooks", 90)
                    cb("test_clip_1", "encoding", 100)
                return {"processed": 1, "failed": 0, "items": []}

            mock_pipeline.process_pending_shorts = fake_process_pending_shorts

            with patch("app.api.routes.shorts.get_shorts_pipeline", return_value=mock_pipeline):
                resp = self.client.post("/api/shorts/test_clip_1/process", json={})
                self.assertEqual(resp.status_code, 200)

        # Check emitted event types
        event_types = [ev[0] for ev in broadcast_events]
        self.assertIn("processing_started", event_types)
        self.assertIn("processing_progress", event_types)
        self.assertIn("processing_complete", event_types)

        # Verify progress stages
        progress_events = [ev[1] for ev in broadcast_events if ev[0] == "processing_progress"]
        stages = [p["stage"] for p in progress_events]
        self.assertEqual(stages, ["trimming", "reframing", "transcribing", "subtitles", "hooks", "encoding"])
        self.assertEqual(progress_events[-1]["percentage"], 100)

    def test_websocket_broadcast_cleans_up_disconnected_clients(self) -> None:
        """Broadcast gracefully handles and removes disconnected/errored WebSocket clients."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from starlette.websockets import WebSocketState
        from app.api.events import ConnectionManager

        mgr = ConnectionManager()
        # Mock a disconnected websocket
        disconnected_ws = MagicMock()
        disconnected_ws.client_state = WebSocketState.DISCONNECTED
        disconnected_ws.send_text = AsyncMock()

        # Mock an errored websocket that raises ConnectionResetError
        errored_ws = MagicMock()
        errored_ws.client_state = WebSocketState.CONNECTED
        errored_ws.send_text = AsyncMock(side_effect=ConnectionResetError("Socket broken"))

        # Mock a healthy websocket
        healthy_ws = MagicMock()
        healthy_ws.client_state = WebSocketState.CONNECTED
        healthy_ws.send_text = AsyncMock()

        mgr.active_connections = {disconnected_ws, errored_ws, healthy_ws}

        # Run broadcast
        asyncio.run(mgr.broadcast("ping_event", {"hello": "world"}))

        # Healthy client should receive message
        healthy_ws.send_text.assert_awaited_once()
        # Disconnected client should not have send_text called
        disconnected_ws.send_text.assert_not_awaited()
        # Dead clients should be removed from active_connections
        self.assertNotIn(disconnected_ws, mgr.active_connections)
        self.assertNotIn(errored_ws, mgr.active_connections)
        self.assertIn(healthy_ws, mgr.active_connections)

    def test_channels_live_status_endpoint(self) -> None:
        """GET /api/channels/live-status returns live/offline status for configured channels."""
        with patch(
            "app.api.routes.channels.check_youtube_channel_live_status",
            return_value={
                "id": "UCWsDFcIhY2DBi3GB5uykGXA",
                "name": "IShowSpeed",
                "platform": "youtube",
                "is_live": True,
                "viewers": 45000,
                "title": "Playing GTA 6 Live!",
                "category": "Gaming",
                "language": "en",
                "video_id": "vid123",
            },
        ):
            resp = self.client.get("/api/channels/live-status")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("channels", data)
            self.assertIsInstance(data["channels"], list)
            ch = next((c for c in data["channels"] if c["id"] == "UCWsDFcIhY2DBi3GB5uykGXA"), None)
            self.assertIsNotNone(ch)
            self.assertTrue(ch["is_live"])
            self.assertEqual(ch["viewers"], 45000)
            self.assertEqual(ch["platform"], "youtube")

    def test_monitor_fallback_approval_and_wait_endpoints(self) -> None:
        """POST /api/monitor/approve/{channel_id} and POST /api/monitor/wait-target work correctly."""
        from unittest.mock import AsyncMock

        with patch("app.api.routes.monitor.supervisor.approve_fallback", new_callable=AsyncMock) as mock_approve:
            mock_approve.return_value = {"status": "started", "channel": "test_streamer", "is_fallback": True}
            resp = self.client.post("/api/monitor/approve/test_streamer")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "started")
            mock_approve.assert_awaited_once_with("test_streamer")

        with patch("app.api.routes.monitor.supervisor.wait_for_targets", new_callable=AsyncMock) as mock_wait:
            mock_wait.return_value = {"status": "waiting_for_targets", "message": "Polling targets"}
            resp = self.client.post("/api/monitor/wait-target")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "waiting_for_targets")
            mock_wait.assert_awaited_once()

    def test_clips_recorded_date_included(self) -> None:
        """GET /api/clips returns recorded_at_iso date field for all clips."""
        resp = self.client.get("/api/clips")
        self.assertEqual(resp.status_code, 200)
        clips = resp.json()
        if clips:
            self.assertIn("recorded_at_iso", clips[0])
            self.assertIn("duration", clips[0])

    def test_shorts_recorded_and_processed_dates_included(self) -> None:
        """GET /api/shorts returns recorded_at_iso and processed_at_iso fields."""
        resp = self.client.get("/api/shorts")
        self.assertEqual(resp.status_code, 200)
        shorts = resp.json()
        if shorts:
            self.assertIn("recorded_at_iso", shorts[0])
            self.assertIn("processed_at_iso", shorts[0])

