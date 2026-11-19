"""Stage 10: Super Upgrade Comprehensive Unit Tests.

Covers:
1. Video quality configuration and Lanczos filtergraph construction.
2. YouTube Live Stream discovery (Mode A priority channels, Mode B category search).
3. YouTube Live Chat monitor (polling, parsing, quota backoff, highlight detector integration).
4. Language filter (English, Hindi Devanagari, Hinglish, non-target rejection, fallback accept).
5. Multi-platform StreamSession & clip directory hierarchy (platform field in sidecars).
6. CLI argument parsing for unified multi-platform flags.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.chat.youtube_chat import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    QUOTA_COST_PER_POLL,
    YouTubeChatMonitor,
    count_emojis,
    parse_iso_to_epoch,
)
from app.config import ConfigError, Settings, load_settings
from app.detection.activity_detector import ActivityDetectorConfig
from app.detection.highlight_detector import HighlightConfig, StreamHighlightDetector
from app.detection.highlight_event import ChatMessage, HighlightEvent
from app.discovery.youtube_finder import (
    DEFAULT_PRIORITY_CHANNELS,
    YouTubeFinderError,
    YouTubeLiveStream,
    YouTubeStreamFinder,
)
from app.filters.language_filter import (
    DEFAULT_TARGET_LANGUAGES,
    LanguageFilter,
    analyze_chat_sample,
)
from app.multistream.session import SessionState, StreamSession
from app.recording.clip_extractor import build_clip_output_paths
from app.recording.models import (
    ClipWindowPlan,
    ExtractedClipMetadata,
    PinCoverageReport,
    RecordingConfig,
    SegmentSelection,
)
from app.recording.stream_recorder import build_streamlink_command
from app.shorts.openshorts_bridge import OpenShortsBridge
from monitor_streams import build_parser


def _mock_settings(root: Path, **overrides) -> Settings:
    defaults = {
        "client_id": "test_cid",
        "client_secret": "test_sec",
        "redirect_uri": "http://localhost:3000",
        "category_name": "Grand Theft Auto VI",
        "max_streams": 3,
        "stream_language": None,
        "spike_window_seconds": 10.0,
        "spike_multiplier": 3.0,
        "spike_min_messages": 10,
        "spike_cooldown_seconds": 15.0,
        "spike_warmup_seconds": 30.0,
        "event_pre_seconds": 10.0,
        "event_post_seconds": 20.0,
        "emote_weight": 0.5,
        "max_event_seconds": 120.0,
        "recording_enabled": True,
        "recording_quality": "1080p60,1080p,720p60,best",
        "recording_buffer_dir": root / ".buffer",
        "clips_output_dir": root / "clips",
        "shorts_output_dir": root / "shorts",
        "monitor_platform": "all",
        "target_youtube_channels": DEFAULT_PRIORITY_CHANNELS,
        "language_filter_enabled": True,
        "target_languages": ("en", "hi"),
        "shorts_output_bitrate": "6M",
        "shorts_output_crf": 18,
        "youtube_chat_poll_interval": 2.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class TestVideoQuality(unittest.TestCase):
    """Part 1: Video quality settings and Lanczos filtergraph validation."""

    def test_recording_config_and_settings_defaults(self):
        rec_cfg = RecordingConfig()
        self.assertEqual(rec_cfg.quality, "1080p60,1080p,720p60,best")

        with tempfile.TemporaryDirectory() as tmp:
            st = _mock_settings(Path(tmp))
            self.assertEqual(st.recording_quality, "1080p60,1080p,720p60,best")
            self.assertEqual(st.shorts_output_bitrate, "6M")
            self.assertEqual(st.shorts_output_crf, 18)

    def test_bridge_filtergraph_and_encode_params(self):
        bridge = OpenShortsBridge(
            output_dir="/tmp/shorts",
            engine="ffmpeg_fallback",
            output_bitrate="6M",
            output_crf=18,
        )
        self.assertEqual(bridge.output_bitrate, "6M")
        self.assertEqual(bridge.output_crf, 18)

        # Mock subprocess.run to inspect FFmpeg command line
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                in_vid = t / "clip.mp4"
                in_vid.write_bytes(b"dummy")
                out_vid = t / "out.mp4"
                bridge._render_with_ffmpeg(in_vid, out_vid, "Hook Title", t)

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            # Verify lanczos filtergraph
            self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos", cmd_str)
            self.assertIn("scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos", cmd_str)
            # Verify bitrate and CRF flags
            self.assertIn("-b:v 6M", cmd_str)
            self.assertIn("-crf 18", cmd_str)
            self.assertIn("-preset slow", cmd_str)

    def test_docker_command_includes_quality_env_vars(self):
        bridge = OpenShortsBridge(
            output_dir="/tmp/shorts",
            engine="openshorts",
            output_bitrate="6M",
            output_crf=18,
        )
        with patch.object(bridge, "is_docker_available", return_value=True), patch(
            "subprocess.run"
        ) as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with tempfile.TemporaryDirectory() as tmp:
                t = Path(tmp)
                in_vid = t / "clip.mp4"
                in_vid.write_bytes(b"dummy")
                out_vid = t / "out.mp4"
                bridge._render_with_openshorts(in_vid, out_vid, "Hook Title")

            cmd = mock_run.call_args[0][0]
            cmd_str = " ".join(cmd)
            self.assertIn("DELIVERY_MIN_WIDTH=1080", cmd_str)
            self.assertIn("OUTPUT_CRF=18", cmd_str)
            self.assertIn("OUTPUT_BITRATE=6M", cmd_str)


class TestYouTubeFinder(unittest.TestCase):
    """Part 2: YouTube Live stream discovery tests."""

    def test_check_priority_channels_mode_a(self):
        finder = YouTubeStreamFinder(api_key="fake_key", priority_channels=["UC7cs8q-gJRlGwj4A8OmCmXg"])

        search_resp = {
            "items": [{"id": {"videoId": "yt_vid_123"}}]
        }
        videos_resp = {
            "items": [
                {
                    "id": "yt_vid_123",
                    "snippet": {
                        "channelId": "UC7cs8q-gJRlGwj4A8OmCmXg",
                        "channelTitle": "CarryMinati",
                        "title": "GTA 6 LIVE STREAMING",
                        "defaultAudioLanguage": "hi",
                    },
                    "liveStreamingDetails": {
                        "concurrentViewers": "154200",
                        "activeLiveChatId": "chat_carry_01",
                        "actualStartTime": "2026-11-19T04:00:00Z",
                    },
                    "statistics": {"viewCount": "500000"},
                }
            ]
        }

        with patch.object(finder.session, "get") as mock_get:
            resp_search = MagicMock(status_code=200, json=lambda: search_resp)
            resp_videos = MagicMock(status_code=200, json=lambda: videos_resp)
            mock_get.side_effect = [resp_search, resp_videos]

            results = finder.check_priority_channels(["UC7cs8q-gJRlGwj4A8OmCmXg"])

        self.assertEqual(len(results), 1)
        stream = results[0]
        self.assertEqual(stream.channel_name, "CarryMinati")
        self.assertEqual(stream.video_id, "yt_vid_123")
        self.assertEqual(stream.viewer_count, 154200)
        self.assertEqual(stream.live_chat_id, "chat_carry_01")
        self.assertEqual(stream.language, "hi")
        self.assertEqual(stream.platform, "youtube")

    def test_discover_category_streams_mode_b(self):
        finder = YouTubeStreamFinder(api_key="fake_key")

        search_resp = {
            "items": [
                {"id": {"videoId": "vid_high"}},
                {"id": {"videoId": "vid_low"}},
            ]
        }
        videos_resp = {
            "items": [
                {
                    "id": "vid_high",
                    "snippet": {"channelId": "UC1", "channelTitle": "ProGamer", "title": "GTA 6", "defaultLanguage": "en"},
                    "liveStreamingDetails": {"concurrentViewers": "5000", "activeLiveChatId": "c1"},
                },
                {
                    "id": "vid_low",
                    "snippet": {"channelId": "UC2", "channelTitle": "TinyGamer", "title": "GTA 6", "defaultLanguage": "en"},
                    "liveStreamingDetails": {"concurrentViewers": "45", "activeLiveChatId": "c2"},
                },
            ]
        }

        with patch.object(finder.session, "get") as mock_get:
            mock_get.side_effect = [
                MagicMock(status_code=200, json=lambda: search_resp),
                MagicMock(status_code=200, json=lambda: videos_resp),
            ]
            # Minimum 100 viewers should filter out vid_low (45 viewers)
            results = finder.discover_category_streams(min_viewers=100)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].video_id, "vid_high")
        self.assertEqual(results[0].viewer_count, 5000)

    def test_missing_credentials_raises_error(self):
        finder = YouTubeStreamFinder(token_manager=None, api_key=None)
        with self.assertRaises(YouTubeFinderError):
            finder._get_headers_or_params({})


class TestYouTubeChatMonitor(unittest.TestCase):
    """Part 2: YouTube Live Chat monitor & quota backoff."""

    def test_emoji_counting_and_iso_parsing(self):
        self.assertEqual(count_emojis("Hello world! 😂🔥 GG"), 2)
        self.assertEqual(count_emojis("No emojis here"), 0)

        epoch = parse_iso_to_epoch("2026-11-19T04:00:00.000Z")
        self.assertGreater(epoch, 1700000000)

    def test_fetch_messages_once_and_quota_tracking(self):
        det_cfg = HighlightConfig(
            detector=ActivityDetectorConfig(
                window_seconds=10.0,
                spike_multiplier=3.0,
                min_messages=5,
                cooldown_seconds=15.0,
                warmup_seconds=0.0,
            )
        )
        detector = StreamHighlightDetector("yt_test", "TestStreamer", det_cfg)

        monitor = YouTubeChatMonitor(
            live_chat_id="chat_123",
            detector=detector,
            api_key="test_key",
            poll_interval=2.0,
        )

        mock_payload = {
            "pollingIntervalMillis": 2500,
            "nextPageToken": "page_token_2",
            "items": [
                {
                    "id": "msg_1",
                    "snippet": {
                        "displayMessage": "WHAT A SHOT! 🔥",
                        "publishedAt": "2026-11-19T04:00:00.000Z",
                    },
                    "authorDetails": {"displayName": "ViewerOne"},
                },
                {
                    "id": "msg_2",
                    "snippet": {
                        "displayMessage": "bhai kya khela",
                        "publishedAt": "2026-11-19T04:00:01.000Z",
                    },
                    "authorDetails": {"displayName": "ViewerTwo"},
                },
            ],
        }

        with patch.object(monitor.session, "get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200, json=lambda: mock_payload)
            msgs, interval_ms = monitor.fetch_messages_once()

        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].username, "ViewerOne")
        self.assertEqual(msgs[0].text, "WHAT A SHOT! 🔥")
        self.assertEqual(msgs[0].emote_count, 1)
        self.assertEqual(msgs[1].username, "ViewerTwo")
        self.assertEqual(monitor.next_page_token, "page_token_2")
        self.assertEqual(monitor.quota_used_units, QUOTA_COST_PER_POLL)
        self.assertEqual(interval_ms, 2500)

    def test_quota_adaptive_sleep_interval(self):
        det_cfg = HighlightConfig(
            detector=ActivityDetectorConfig(
                window_seconds=10.0,
                spike_multiplier=3.0,
                min_messages=5,
                cooldown_seconds=15.0,
                warmup_seconds=0.0,
            )
        )
        detector = StreamHighlightDetector("yt_test", "TestStreamer", det_cfg)
        monitor = YouTubeChatMonitor(
            live_chat_id="chat_123",
            detector=detector,
            api_key="test_key",
            poll_interval=2.0,
            daily_quota_limit=9500,
        )

        # Standard usage
        monitor.quota_used_units = 100
        self.assertEqual(monitor._calculate_sleep_interval(2000), 2.0)

        # High usage (>8000) backs off to 5s
        monitor.quota_used_units = 8500
        self.assertEqual(monitor._calculate_sleep_interval(2000), 5.0)

        # Ceiling reached (>=9500) backs off to 10s
        monitor.quota_used_units = 9600
        self.assertEqual(monitor._calculate_sleep_interval(2000), 10.0)


class TestLanguageFilter(unittest.TestCase):
    """Part 3: Language detection and filtering tests."""

    def test_english_chat_detection(self):
        messages = [
            "what a shot omg bro",
            "this game is so good lol",
            "gg nice play dude",
            "lets go win this stream",
        ]
        lang, conf = analyze_chat_sample(messages)
        self.assertEqual(lang, "en")
        self.assertGreaterEqual(conf, 0.6)

    def test_hindi_devanagari_chat_detection(self):
        messages = [
            "क्या बात है भाई",
            "ओपी खेल रहे हो",
            "मजा आ गया",
        ]
        lang, conf = analyze_chat_sample(messages)
        self.assertEqual(lang, "hi")
        self.assertGreaterEqual(conf, 0.7)

    def test_hinglish_chat_detection(self):
        messages = [
            "bhai kya hi khela yaar",
            "accha khel raha hai",
            "nahi bhai ruk jao",
            "op bhai op",
        ]
        lang, conf = analyze_chat_sample(messages)
        self.assertEqual(lang, "hi")
        self.assertGreaterEqual(conf, 0.6)

    def test_filter_acceptance_and_rejection(self):
        lf = LanguageFilter(target_languages=("en", "hi"), enabled=True)

        # English accepted
        ok, lang, _ = lf.is_stream_accepted(metadata_language="en-US", channel_name="xqc")
        self.assertTrue(ok)
        self.assertEqual(lang, "en")

        # Hindi accepted
        ok, lang, _ = lf.is_stream_accepted(metadata_language="hi", channel_name="carry")
        self.assertTrue(ok)
        self.assertEqual(lang, "hi")

        # Non-target language (Spanish) rejected
        ok, lang, _ = lf.is_stream_accepted(metadata_language="es", channel_name="ibai")
        self.assertFalse(ok)
        self.assertEqual(lang, "es")

        # Non-target language (Russian) rejected
        ok, lang, _ = lf.is_stream_accepted(metadata_language="ru", channel_name="buster")
        self.assertFalse(ok)
        self.assertEqual(lang, "ru")

    def test_undetermined_accepted_by_default_fallback(self):
        lf = LanguageFilter(target_languages=("en", "hi"), enabled=True)
        # Empty metadata and empty chat
        ok, lang, reason = lf.is_stream_accepted(metadata_language=None, chat_messages=[])
        self.assertTrue(ok)
        self.assertEqual(lang, "und")
        self.assertIn("default", reason)

    def test_disabled_filter_accepts_everything(self):
        lf = LanguageFilter(enabled=False)
        ok, _, reason = lf.is_stream_accepted(metadata_language="es")
        self.assertTrue(ok)
        self.assertEqual(reason, "language_filter_disabled")


class TestMultiPlatformStreamSession(unittest.TestCase):
    """Part 2 & Part 4: Multi-platform StreamSession & platform abstraction."""

    def test_session_properties_and_clips_channel_dir_for_youtube(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _mock_settings(root)
            session = StreamSession(
                channel="UC7cs8q-gJRlGwj4A8OmCmXg",
                settings=settings,
                platform="youtube",
                video_id="vid_yt_456",
                live_chat_id="chat_yt_456",
            )
            self.assertEqual(session.platform, "youtube")
            self.assertEqual(session.clips_channel_dir, root / "clips" / "youtube" / "UC7cs8q-gJRlGwj4A8OmCmXg")

    def test_session_properties_and_clips_channel_dir_for_twitch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = _mock_settings(root)
            session = StreamSession(
                channel="kaicenat",
                settings=settings,
                platform="twitch",
            )
            self.assertEqual(session.platform, "twitch")
            self.assertEqual(session.clips_channel_dir, root / "clips" / "kaicenat")

    def test_build_streamlink_command_for_youtube(self):
        cfg = RecordingConfig(quality="1080p60,best")
        cmd = build_streamlink_command(
            streamlink_bin="streamlink",
            channel="UC7cs8q-gJRlGwj4A8OmCmXg",
            config=cfg,
            platform="youtube",
            video_id="yt_abc123",
        )
        cmd_str = " ".join(cmd)
        self.assertIn("https://www.youtube.com/watch?v=yt_abc123", cmd_str)
        self.assertNotIn("--twitch-low-latency", cmd_str)

    def test_build_clip_output_paths_platform_separation(self):
        root = Path("/tmp/liveclipper_clips")

        # Twitch clip path (backward compatible)
        mp4_tw, json_tw = build_clip_output_paths(
            output_dir=root,
            channel="kaicenat",
            event_id="ev123456",
            trigger_timestamp=1700000000.0,
            category="funny",
            platform="twitch",
        )
        self.assertEqual(mp4_tw.parent, root / "kaicenat")

        # YouTube clip path
        mp4_yt, json_yt = build_clip_output_paths(
            output_dir=root,
            channel="carryminati",
            event_id="ev999999",
            trigger_timestamp=1700000000.0,
            category="clutch",
            platform="youtube",
        )
        self.assertEqual(mp4_yt.parent, root / "youtube" / "carryminati")

    def test_extracted_clip_metadata_platform_field(self):
        plan = ClipWindowPlan(
            event_id="ev_meta",
            stream_id="s1",
            streamer_name="carryminati",
            event_start_timestamp=100.0,
            event_trigger_timestamp=110.0,
            event_end_timestamp=120.0,
            event_merged_count=0,
            peak_chat_timestamp=110.0,
            reaction_offset_seconds=6.0,
            estimated_moment_start=100.0,
            estimated_moment_end=120.0,
            requested_clip_start=100.0,
            requested_clip_end=120.0,
        )
        coverage = PinCoverageReport(
            requested_start_wall=100.0,
            requested_end_wall=120.0,
            has_full_preroll=True,
            has_full_postroll=True,
            is_partial=False,
            segment_count=5,
            actual_approx_start_wall=100.0,
            actual_approx_end_wall=120.0,
            missing_leading_seconds=0.0,
            missing_trailing_seconds=0.0,
            total_media_duration=20.0,
            discontinuities=[],
        )
        selection = SegmentSelection(
            segments=[],
            approx_trim_start_seconds=0.0,
            target_duration_seconds=20.0,
            coverage=coverage,
        )
        meta = ExtractedClipMetadata(
            event_id="ev_meta",
            stream_id="s1",
            streamer_name="carryminati",
            clip_path="/tmp/carry.mp4",
            extracted_at=1700000000.0,
            extracted_at_iso="2026-11-19T04:00:00Z",
            clip_plan=plan,
            selection=selection,
            analysis={"score": 85},
            platform="youtube",
        )
        d = meta.to_dict()
        self.assertEqual(d["platform"], "youtube")


class TestMultiPlatformCliFlags(unittest.TestCase):
    """Part 4: CLI argument parser testing."""

    def test_parser_platform_and_youtube_flags(self):
        parser = build_parser()
        args = parser.parse_args([
            "--platform", "youtube",
            "--yt-channels", "UC7cs8q-gJRlGwj4A8OmCmXg,UCvQ0C-sIKKlFaxyHYRE1YSg",
            "--yt-category", "gta6",
            "--languages", "hi,en",
            "--no-language-filter",
        ])
        self.assertEqual(args.platform, "youtube")
        self.assertIn("UC7cs8q-gJRlGwj4A8OmCmXg", args.yt_channels)
        self.assertEqual(args.yt_category, "gta6")
        self.assertEqual(args.languages, "hi,en")
        self.assertTrue(args.no_language_filter)

    def test_parser_defaults(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.platform)
        self.assertEqual(args.yt_channels, "")
        self.assertEqual(args.yt_category, "gaming")
        self.assertIsNone(args.languages)
        self.assertFalse(args.no_language_filter)


if __name__ == "__main__":
    unittest.main()
