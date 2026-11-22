"""Comprehensive unit tests for YouTube Video Clipper (app.clipper and /api/import/*)."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from app.api.server import create_app
from app.clipper.analyzer import (
    MomentCandidate,
    detect_and_rank_moments,
    parse_subtitles_file,
)
from app.clipper.audio_energy import (
    analyze_clip_audio_energy,
    compute_audio_energy_profile,
    detect_audio_spikes,
)
from app.clipper.downloader import (
    VideoMetadata,
    classify_youtube_error,
    download_video,
    extract_youtube_id,
    fetch_video_info,
    is_valid_youtube_url,
    parse_timestamp_seconds,
)
from app.clipper.extractor import extract_clip
from app.clipper.models import DownloadedVideo, ImportClip, ImportJob, TranscriptSegment
from app.clipper.service import ClipperService, get_clipper_service


class TestClipperDownloader(unittest.TestCase):
    """Test URL parsing, ID extraction, and video info fetching."""

    def test_extract_youtube_id_various_formats(self):
        urls = [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("http://youtube.com/v/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://gaming.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?feature=shared&v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ]
        for url, expected_id in urls:
            with self.subTest(url=url):
                self.assertTrue(is_valid_youtube_url(url))
                self.assertEqual(extract_youtube_id(url), expected_id)

    def test_invalid_youtube_urls(self):
        invalid = [
            "https://twitch.tv/kaicenat",
            "https://vimeo.com/123456",
            "not a url",
            "http://example.com/video.mp4",
            "",
        ]
        for url in invalid:
            with self.subTest(url=url):
                self.assertFalse(is_valid_youtube_url(url))
                self.assertIsNone(extract_youtube_id(url))

    @mock.patch("yt_dlp.YoutubeDL")
    def test_fetch_video_info_success(self, mock_ydl_cls):
        mock_ydl = mock.MagicMock()
        mock_ydl_cls.return_value.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = {
            "id": "dQw4w9WgXcQ",
            "title": "GTA 6 Official Gameplay",
            "channel": "RockstarGames",
            "uploader": "Rockstar Games",
            "duration": 360.0,
            "view_count": 50000000,
            "like_count": 4000000,
            "description": "Watch the brand new trailer",
            "thumbnail": "https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            "upload_date": "20261118",
            "subtitles": {"en": [{"ext": "vtt"}]},
            "automatic_captions": {},
            "chapters": [{"title": "Intro", "start_time": 0.0, "end_time": 60.0}],
        }

        meta = fetch_video_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(meta.video_id, "dQw4w9WgXcQ")
        self.assertEqual(meta.title, "GTA 6 Official Gameplay")
        self.assertEqual(meta.channel, "Rockstar Games")
        self.assertEqual(meta.duration, 360.0)
        self.assertEqual(meta.view_count, 50000000)
        self.assertEqual(len(meta.chapters), 1)

    @mock.patch("yt_dlp.YoutubeDL")
    def test_download_video_subtitles_rate_limit_fallback(self, mock_ydl_cls):
        """Verify that when subtitle download hits 429 Too Many Requests, video download continues without error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)

            call_count = [0]
            def _mock_download(urls):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("HTTP Error 429: Too Many Requests on subtitle segment")
                # Second call (without subtitles) succeeds and creates source.mp4
                (out_dir / "source.mp4").write_bytes(b"dummy_mp4_bytes")

            mock_ydl = mock.MagicMock()
            mock_ydl.download.side_effect = _mock_download
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

            source_mp4, sub_path = download_video(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir=out_dir,
            )

            self.assertTrue(source_mp4.is_file())
            self.assertIsNone(sub_path)  # Subtitles gracefully skipped
            self.assertGreaterEqual(call_count[0], 2)

    def test_classify_youtube_error(self):
        self.assertIn("private", classify_youtube_error(RuntimeError("Private video. Sign in")).lower())
        self.assertIn("age-restricted", classify_youtube_error(RuntimeError("Sign in to confirm your age")).lower())
        self.assertIn("unavailable", classify_youtube_error(RuntimeError("This video is unavailable")).lower())
        self.assertIn("region", classify_youtube_error(RuntimeError("not made this video available in your country")).lower())
        self.assertIn("429", classify_youtube_error(RuntimeError("HTTP Error 429: Too Many Requests")).lower())
        self.assertIn("bot", classify_youtube_error(RuntimeError("Sign in to confirm you're not a bot")).lower())

    @mock.patch("yt_dlp.YoutubeDL")
    def test_download_video_with_time_range(self, mock_ydl_cls):
        """Verify that start_time and end_time configure download_ranges in yt-dlp."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            captured_opts = {}

            def _init_mock(opts):
                captured_opts.update(opts)
                m = mock.MagicMock()
                def _dl(urls):
                    (out_dir / "source.mp4").write_bytes(b"clipped_mp4_bytes")
                m.download.side_effect = _dl
                m.__enter__.return_value = m
                return m

            mock_ydl_cls.side_effect = _init_mock

            source_mp4, _ = download_video(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir=out_dir,
                start_time="1:00",
                end_time="2:30",
            )
            self.assertTrue(source_mp4.is_file())
            self.assertIn("download_ranges", captured_opts)
            self.assertTrue(captured_opts.get("force_keyframes_at_cuts"))

    @mock.patch("subprocess.run")
    @mock.patch("yt_dlp.YoutubeDL")
    def test_download_video_remux_webm_to_mp4(self, mock_ydl_cls, mock_subproc):
        """Verify non-mp4 downloaded files (e.g. source.webm) are remuxed to source.mp4."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)

            def _mock_download(urls):
                (out_dir / "source.webm").write_bytes(b"webm_bytes")

            mock_ydl = mock.MagicMock()
            mock_ydl.download.side_effect = _mock_download
            mock_ydl_cls.return_value.__enter__.return_value = mock_ydl

            def _fake_ffmpeg(cmd, **kwargs):
                (out_dir / "source.mp4").write_bytes(b"converted_mp4_bytes")
                return mock.MagicMock(returncode=0)

            mock_subproc.side_effect = _fake_ffmpeg

            source_mp4, _ = download_video(
                url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                output_dir=out_dir,
            )
            self.assertEqual(source_mp4.name, "source.mp4")
            self.assertTrue(source_mp4.is_file())

    def test_downloaded_video_source_path_property(self):
        dv = DownloadedVideo(
            video_id="test1234",
            title="Test",
            channel="TestCh",
            duration=60.0,
            file_size_mb=10.5,
            source_video_path="/path/to/source.mp4",
        )
        self.assertEqual(dv.source_path, "/path/to/source.mp4")
        self.assertEqual(dv.to_dict()["source_path"], "/path/to/source.mp4")


class TestClipperAudioEnergy(unittest.TestCase):
    """Test audio energy computation, spike detection, and audio context profiling."""

    def test_detect_audio_spikes(self):
        # 30 windows: mostly baseline (-35 dBFS), with spike at 10.0-12.0s (-10 dBFS)
        windows = []
        for i in range(40):
            t = i * 0.5
            dbfs = -10.0 if 10.0 <= t <= 12.0 else -35.0
            windows.append({"time": t, "rms": 1000 if dbfs == -10.0 else 100, "dbfs": dbfs})

        spikes = detect_audio_spikes(windows, min_spike_db=15.0)
        self.assertGreater(len(spikes), 0)
        spike = spikes[0]
        self.assertLessEqual(spike["start_time"], 10.0)
        self.assertGreaterEqual(spike["end_time"], 12.0)
        self.assertGreater(spike["score"], 0)

    def test_analyze_clip_audio_energy_missing_file(self):
        res = analyze_clip_audio_energy(Path("/nonexistent/file.ts"))
        self.assertFalse(res["available"])
        self.assertEqual(res["mean_dbfs"], -96.0)


class TestClipperAnalyzer(unittest.TestCase):
    """Test subtitle parsing, heuristics, and multi-signal moment detection."""

    def test_parse_vtt_subtitles(self):
        with tempfile.TemporaryDirectory() as tmp:
            vtt_path = Path(tmp) / "subs.en.vtt"
            vtt_content = """WEBVTT
Kind: captions
Language: en

00:00:05.000 --> 00:00:08.500
Look at that stunt right there!

00:00:09.000 --> 00:00:14.000
He is jumping off the building, oh my god!
"""
            vtt_path.write_text(vtt_content, encoding="utf-8")
            segments = parse_subtitles_file(vtt_path)
            self.assertEqual(len(segments), 2)
            self.assertEqual(segments[0].start, 5.0)
            self.assertEqual(segments[0].end, 8.5)
            self.assertIn("stunt", segments[0].text)
            self.assertEqual(segments[1].start, 9.0)
            self.assertEqual(segments[1].end, 14.0)

    def test_detect_and_rank_moments_fallback_heuristic(self):
        # Test moment detection fallback when Gemini API key is absent
        with tempfile.TemporaryDirectory() as tmp:
            vtt_path = Path(tmp) / "subs.en.vtt"
            vtt_content = """WEBVTT

00:00:10.000 --> 00:00:15.000
OMG THIS IS INSANE NO WAY!

00:00:40.000 --> 00:00:45.000
Just driving normally down the street.

00:01:20.000 --> 00:01:25.000
WTF BRO THAT WAS THE CRAZIEST CLUTCH EVER!
"""
            vtt_path.write_text(vtt_content, encoding="utf-8")
            meta = VideoMetadata(
                video_id="test_vid",
                title="Insane GTA 6 Highlights",
                channel="GamerChannel",
                duration=180.0,
                view_count=100000,
            )

            moments = detect_and_rank_moments(
                media_path=Path(tmp) / "source.mp4",
                metadata=meta,
                subtitle_path=vtt_path,
                max_clips=2,
                target_clip_duration=45.0,
            )

            self.assertLessEqual(len(moments), 2)
            if moments:
                self.assertGreater(moments[0].score, 0)
                self.assertTrue(moments[0].duration > 0)


class TestClipperExtractor(unittest.TestCase):
    """Test clip cutter and snapshot generation with mocked FFmpeg."""

    @mock.patch("subprocess.run")
    def test_extract_clip_segment_success(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "clips"
            src_file = Path(tmp) / "source.mp4"
            src_file.write_bytes(b"dummy video")

            meta = VideoMetadata(
                video_id="vid999",
                title="Grand Theft Auto Highlights",
                channel="TestStreamer",
                duration=300.0,
            )

            moment = MomentCandidate(
                moment_id="mom123",
                start_time=10.0,
                end_time=45.0,
                duration=35.0,
                score=88,
                reason="High audio volume and crazy chat reaction",
                suggested_title="Epic Heist",
                transcript_excerpt="Look at that getaway!",
            )

            clip = extract_clip(
                source_video_path=src_file,
                moment=moment,
                metadata=meta,
                output_dir=out_dir,
                clip_index=1,
            )

            self.assertIsNotNone(clip)
            self.assertEqual(clip.suggested_title, "Epic Heist")
            self.assertEqual(clip.video_id, "vid999")
            self.assertTrue(Path(clip.sidecar_path).is_file())

            # Verify sidecar JSON
            sidecar = json.loads(Path(clip.sidecar_path).read_text(encoding="utf-8"))
            self.assertEqual(sidecar["is_imported"], True)
            self.assertEqual(sidecar["source_type"], "imported")
            self.assertEqual(sidecar["source_video_id"], "vid999")


class TestClipperAPI(unittest.TestCase):
    """Test FastAPI /api/import/* endpoints."""

    def setUp(self):
        self.tmp_imports = tempfile.TemporaryDirectory()
        self.patcher = mock.patch("app.api.routes.imports.get_clipper_service")
        self.mock_get_service = self.patcher.start()
        self.test_service = ClipperService(imports_dir=Path(self.tmp_imports.name))
        self.mock_get_service.return_value = self.test_service
        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self):
        self.patcher.stop()
        self.tmp_imports.cleanup()

    @mock.patch("app.api.routes.imports.fetch_video_info")
    def test_import_info_endpoint(self, mock_fetch):
        mock_fetch.return_value = VideoMetadata(
            video_id="dQw4w9WgXcQ",
            title="Never Gonna Give You Up",
            channel="RickAstleyVEVO",
            duration=212.0,
            view_count=1400000000,
            like_count=16000000,
            description="The official music video",
            thumbnail_url="https://img.youtube.com/vi/dQw4w9WgXcQ/default.jpg",
        )

        resp = self.client.post("/api/import/info", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(data["title"], "Never Gonna Give You Up")
        self.assertEqual(data["channel"], "RickAstleyVEVO")
        self.assertEqual(data["duration"], 212.0)

    def test_import_info_invalid_url(self):
        resp = self.client.post("/api/import/info", json={"url": "https://twitch.tv/kaicenat"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid YouTube", resp.json()["detail"])

    def test_import_jobs_and_clips_endpoints(self):
        resp_jobs = self.client.get("/api/import/jobs")
        self.assertEqual(resp_jobs.status_code, 200)
        self.assertIsInstance(resp_jobs.json(), list)

        resp_clips = self.client.get("/api/import/clips")
        self.assertEqual(resp_clips.status_code, 200)
        self.assertIsInstance(resp_clips.json(), list)

    def test_parse_timestamp_seconds(self):
        self.assertEqual(parse_timestamp_seconds("01:23:45"), 5025.0)
        self.assertEqual(parse_timestamp_seconds("5:30"), 330.0)
        self.assertEqual(parse_timestamp_seconds("45"), 45.0)
        self.assertEqual(parse_timestamp_seconds(120), 120.0)
        self.assertIsNone(parse_timestamp_seconds(None))
        self.assertIsNone(parse_timestamp_seconds(""))

    def test_cancel_nonexistent_job(self):
        resp = self.client.post("/api/import/jobs/nonexistent_job_123/cancel")
        self.assertEqual(resp.status_code, 404)

    def test_cleanup_confirmation_required(self):
        resp = self.client.delete("/api/import/clips")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("confirm=true", resp.json()["detail"])

        resp_ok = self.client.delete("/api/import/clips?confirm=true")
        self.assertEqual(resp_ok.status_code, 200)
        self.assertIn("freed_mb", resp_ok.json())

    def test_library_endpoints(self):
        resp_lib = self.client.get("/api/import/library")
        self.assertEqual(resp_lib.status_code, 200)
        self.assertIsInstance(resp_lib.json(), list)

    def test_quota_reset_endpoint(self):
        resp = self.client.post("/api/upload/quota/reset")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["uploads_today"], 0)
        self.assertIn("resets_in_human", data)
        self.assertIn("resets_in_seconds", data)

    def test_import_clip_preview_and_thumbnail_range_streaming(self):
        """Verify /api/import/clips/{clip_id}/preview returns HTTP 206 and video/mp4 without error."""
        service = self.test_service
        test_clip_id = "imp_test_vid_01_10"
        clip_dir = service.imports_dir / "test_vid" / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)
        mp4_file = clip_dir / f"{test_clip_id}.mp4"
        thumb_file = clip_dir / f"{test_clip_id}.jpg"
        mp4_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"A" * 1024)
        thumb_file.write_bytes(b"\xff\xd8\xff\xe0" + b"J" * 256)

        # Test HEAD request on preview
        head_resp = self.client.head(f"/api/import/clips/{test_clip_id}/preview")
        self.assertIn(head_resp.status_code, (200, 206))
        self.assertEqual(head_resp.headers.get("Content-Type"), "video/mp4")
        self.assertEqual(head_resp.headers.get("Accept-Ranges"), "bytes")

        # Test GET Range request
        range_resp = self.client.get(
            f"/api/import/clips/{test_clip_id}/preview",
            headers={"Range": "bytes=0-500"},
        )
        self.assertEqual(range_resp.status_code, 206)
        self.assertEqual(range_resp.headers.get("Content-Type"), "video/mp4")
        self.assertIn("bytes 0-500/", range_resp.headers.get("Content-Range", ""))

        # Test HEAD and GET on thumbnail
        head_thumb = self.client.head(f"/api/import/clips/{test_clip_id}/thumbnail")
        self.assertEqual(head_thumb.status_code, 200)
        self.assertEqual(head_thumb.headers.get("Content-Type"), "image/jpeg")


if __name__ == "__main__":
    unittest.main()
