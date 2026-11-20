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
    extract_youtube_id,
    fetch_video_info,
    is_valid_youtube_url,
)
from app.clipper.extractor import extract_clip
from app.clipper.models import ImportClip, ImportJob, TranscriptSegment
from app.clipper.service import ClipperService


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
        self.app = create_app()
        self.client = TestClient(self.app)

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


if __name__ == "__main__":
    unittest.main()
