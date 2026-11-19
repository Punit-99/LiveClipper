"""Unit tests for Stage 9: YouTube Metadata Optimization Engine.

Tests:
1. Title generation rules: 3 ranked options, <= 100 chars, includes #Shorts.
2. Description optimizer: < 500 chars, hook, streamer credit, game, CTA, hashtags.
3. Tag optimizer: streamer/game variations, category tags, <= 480 chars, deduplicated.
4. Emoji toggle (VIRAL_TITLE_EMOJI true vs false).
5. Tonal adaptation across categories (funny, fail, hype, unexpected).
6. Deterministic offline fallback when Gemini is disabled or unconfigured.
7. Gemini structured JSON parsing and sanitization.
8. Fault-tolerant fallback when Gemini API fails.
9. Interactive title selection (option 1, 2, 3, and custom title).
10. Auto-upload top-ranked title selection.
11. Integration with format_youtube_snippet and sidecar/manifest persistence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.shorts.metadata import format_youtube_snippet
from app.shorts.pipeline import STATE_PROCESSED, STATE_UPLOADED, ShortsPipeline
from app.shorts.viral_metadata import (
    ViralMetadata,
    ViralMetadataGenerator,
    ViralTitleOption,
    generate_fallback_description,
    generate_fallback_tags,
    generate_fallback_titles,
    sanitize_viral_description,
    sanitize_viral_tags,
    sanitize_viral_title,
    strip_emojis,
)
from app.shorts.youtube_uploader import DailyQuotaTracker, YouTubeUploader


class TestViralMetadataSanitizers(unittest.TestCase):
    """Test string sanitizers, limits, and emoji stripping."""

    def test_strip_emojis(self):
        text = "HE DID NOT JUST DO THAT 💀💀 #Shorts"
        cleaned = strip_emojis(text)
        self.assertEqual(cleaned, "HE DID NOT JUST DO THAT #Shorts")

        mixed = "🔥 INSANE PLAY! 🎮 Best GTA 6 moment ✨"
        cleaned_mixed = strip_emojis(mixed)
        self.assertEqual(cleaned_mixed, "INSANE PLAY! Best GTA 6 moment")

    def test_sanitize_viral_title_length_and_shorts_tag(self):
        long_title = (
            "THIS IS AN EXTREMELY LONG TITLE THAT GOES ON AND ON AND ON AND SHOULD DEFINITELY "
            "EXCEED THE ONE HUNDRED CHARACTER LIMIT IMPOSED BY YOUTUBE SHORTS PLATFORM SO IT MUST CUT"
        )
        sanitized = sanitize_viral_title(long_title)
        self.assertLessEqual(len(sanitized), 100)
        self.assertTrue(sanitized.endswith("#Shorts"))

    def test_sanitize_viral_title_emoji_flag(self):
        title = "BRO REALLY THOUGHT HE COULD ESCAPE 💀😂 #Shorts"
        # Emoji enabled
        with_emoji = sanitize_viral_title(title, allow_emoji=True)
        self.assertIn("💀", with_emoji)

        # Emoji disabled
        no_emoji = sanitize_viral_title(title, allow_emoji=False)
        self.assertNotIn("💀", no_emoji)
        self.assertNotIn("😂", no_emoji)
        self.assertTrue(no_emoji.endswith("#Shorts"))

    def test_sanitize_viral_description_length(self):
        long_body = (
            "This is a viral hook sentence!\n\n"
            "🎮 Streamer: @kaicenat\n🎮 Game: GTA 6\n\n"
            + ("Extra details about the stream. " * 30)
            + "\n\n#Shorts #kaicenat #GTA6"
        )
        sanitized = sanitize_viral_description(
            long_body,
            max_chars=500,
        )
        self.assertLessEqual(len(sanitized), 500)
        self.assertIn("🎮 Streamer: @kaicenat", sanitized)
        self.assertIn("🎮 Game: GTA 6", sanitized)

    def test_sanitize_viral_tags_deduplication_and_limit(self):
        tags = [
            "KaiCenat", "kaicenat", "GTA 6", "gta 6", "Gaming",
            "Twitch", "twitch clips", "Funny", "Epic", "Clutch"
        ]
        sanitized = sanitize_viral_tags(tags, max_total_chars=480)
        # Check deduplication
        lowered = [t.lower() for t in sanitized]
        self.assertEqual(len(lowered), len(set(lowered)))
        # Total comma-separated length <= 480
        total_len = sum(len(t) for t in sanitized) + max(0, len(sanitized) - 1)
        self.assertLessEqual(total_len, 480)


class TestRuleBasedFallbacks(unittest.TestCase):
    """Test deterministic rule-based viral metadata generation across categories."""

    def test_category_tonal_adaptation(self):
        categories = ["funny_moment", "streamer_fail", "hype_moment", "unexpected_moment"]
        for cat in categories:
            titles = generate_fallback_titles(
                streamer="xqc",
                category=cat,
                analytical_title=f"Sample {cat} highlight",
                key_quotes=["NO WAY", "OMEGALUL"],
                allow_emoji=True,
            )
            self.assertEqual(len(titles), 3)
            for opt in titles:
                t = opt["title"]
                self.assertLessEqual(len(t), 100)
                self.assertIn("#Shorts", t)
                self.assertTrue(len(opt["style"]) > 0)

    def test_fallback_description_structure(self):
        desc = generate_fallback_description(
            streamer="kaicenat",
            category="funny_moment",
            analytical_title="Kai reacts to NPC crash",
            reaction_summary="The chat exploded with laughing emotes after Kai crashed.",
            key_quotes=["BRO WHAT", "LOOK AT THE CAR"],
        )
        self.assertLessEqual(len(desc), 500)
        self.assertIn("🎮 Streamer: @kaicenat", desc)
        self.assertIn("🎮 Game: GTA 6", desc)
        self.assertIn("Follow for more daily highlights!", desc)
        self.assertIn("#Shorts", desc)

    def test_fallback_tags_structure(self):
        tags = generate_fallback_tags(
            streamer="kaicenat",
            game="GTA 6",
            category="hype_moment",
        )
        self.assertIsInstance(tags, list)
        self.assertIn("kaicenat", [t.lower() for t in tags])
        self.assertIn("gta 6", [t.lower() for t in tags])
        total_len = sum(len(t) for t in tags) + len(tags) - 1
        self.assertLessEqual(total_len, 480)


class TestViralMetadataGenerator(unittest.TestCase):
    """Test ViralMetadataGenerator with offline fallback and mocked Gemini client."""

    def setUp(self):
        self.sidecar = {
            "clip_id": "c_kai_99",
            "streamer_name": "kaicenat",
            "analysis": {
                "title": "Kai crashes into helicopter",
                "highlight_score": 92,
                "category": "funny_moment",
                "reaction_summary": "Kai couldn't stop laughing as helicopter fell.",
                "key_quotes": ["BROOO", "HOW IS THAT REAL"],
            },
        }

    def test_generator_offline_when_no_api_key(self):
        gen = ViralMetadataGenerator(api_key="", enabled=True)
        meta = gen.generate_metadata(self.sidecar)
        self.assertIsInstance(meta, ViralMetadata)
        self.assertEqual(len(meta.titles), 3)
        self.assertLessEqual(len(meta.titles[0]["title"]), 100)
        self.assertTrue(meta.titles[0]["title"].endswith("#Shorts"))
        self.assertLessEqual(len(meta.description), 500)
        self.assertIn("@kaicenat", meta.description)
        self.assertTrue(len(meta.tags) > 3)

    def test_generator_disabled_setting(self):
        gen = ViralMetadataGenerator(api_key="dummy_key", enabled=False)
        meta = gen.generate_metadata(self.sidecar)
        self.assertIsInstance(meta, ViralMetadata)
        self.assertEqual(len(meta.titles), 3)

    def test_gemini_client_mock_success(self):
        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "titles": [
                {
                    "title": "Wait till the end... 💀 #Shorts",
                    "style": "curiosity_gap",
                    "predicted_impact": "High retention",
                },
                {
                    "title": "Kai didn't expect THIS to happen! 😂 #Shorts",
                    "style": "reaction_bait",
                    "predicted_impact": "Viral CTR",
                },
                {
                    "title": "'HOW IS THAT REAL' Kai screams #Shorts",
                    "style": "quote_hook",
                    "predicted_impact": "Strong shareability",
                },
            ],
            "description": (
                "Kai Cenat had the funniest moment live on stream! 😂\n\n"
                "🎮 Streamer: @kaicenat (https://twitch.tv/kaicenat)\n"
                "🎮 Game: GTA 6\n"
                "👉 Follow for more daily highlights!\n\n"
                "#Shorts #kaicenat #GTA6 #TwitchClips"
            ),
            "tags": [
                "kaicenat", "kai cenat", "GTA 6", "gta 6 clips",
                "funny moments", "Shorts", "twitch"
            ],
            "category": "funny_moment",
            "hook_used": "Curiosity gap and unexpected physics reaction",
        })

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response

        gen = ViralMetadataGenerator(
            api_key="fake_key",
            client=mock_client,
            enabled=True,
            allow_emoji=True,
        )

        meta = gen.generate_metadata(self.sidecar)
        self.assertEqual(len(meta.titles), 3)
        self.assertEqual(meta.titles[0]["title"], "Wait till the end... 💀 #Shorts")
        self.assertEqual(meta.titles[0]["style"], "curiosity_gap")
        self.assertIn("@kaicenat", meta.description)
        self.assertIn("kaicenat", meta.tags)

    def test_gemini_client_failure_falls_back_gracefully(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API Rate Limit 429")

        gen = ViralMetadataGenerator(
            api_key="fake_key",
            client=mock_client,
            enabled=True,
        )

        meta = gen.generate_metadata(self.sidecar)
        self.assertIsInstance(meta, ViralMetadata)
        self.assertEqual(len(meta.titles), 3)
        self.assertTrue(meta.titles[0]["title"].endswith("#Shorts"))


class TestPipelineViralIntegration(unittest.TestCase):
    """Test ShortsPipeline interactive and auto upload with viral metadata."""

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

        # Setup 1 processed short
        self.short_vid = self.shorts_dir / "kaicenat" / "c_test_short.mp4"
        self.short_vid.parent.mkdir(parents=True, exist_ok=True)
        self.short_vid.touch()

        self.short_sidecar = self.short_vid.with_suffix(".json")
        sidecar_content = {
            "clip_id": "c_test",
            "streamer_name": "kaicenat",
            "analysis": {
                "title": "Analytical Kai Highlight",
                "highlight_score": 89,
                "category": "funny_moment",
                "reaction_summary": "Chat was laughing uncontrollably.",
                "key_quotes": ["BRO WHAT"],
            },
        }
        with open(self.short_sidecar, "w", encoding="utf-8") as fh:
            json.dump(sidecar_content, fh)

        # Mock viral generator
        self.mock_viral_gen = MagicMock(spec=ViralMetadataGenerator)
        self.mock_viral_gen.generate_metadata.return_value = ViralMetadata(
            titles=[
                {"title": "Title Option 1 #Shorts", "style": "curiosity_gap", "predicted_impact": "High"},
                {"title": "Title Option 2 #Shorts", "style": "reaction_bait", "predicted_impact": "Medium"},
                {"title": "Title Option 3 #Shorts", "style": "quote_hook", "predicted_impact": "High"},
            ],
            description="Viral Description Hook!\n🎮 Streamer: @kaicenat\n🎮 Game: GTA 6\n#Shorts",
            tags=["kaicenat", "gta 6", "shorts"],
            category="funny_moment",
        )

        self.pipeline = ShortsPipeline(
            uploader=self.mock_uploader,
            clips_dir=self.clips_dir,
            shorts_dir=self.shorts_dir,
            viral_generator=self.mock_viral_gen,
        )
        self.pipeline.manifest.update_record(
            clip_id="c_test",
            channel="kaicenat",
            highlight_score=89,
            title="Analytical Kai Highlight",
            state=STATE_PROCESSED,
            short_video_path=str(self.short_vid),
            short_sidecar_path=str(self.short_sidecar),
        )

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_auto_upload_selects_top_ranked_viral_title(self):
        self.mock_uploader.upload_video.return_value = {
            "video_id": "yt_auto_1",
            "url": "https://www.youtube.com/shorts/yt_auto_1",
        }

        res = self.pipeline.upload_shorts(min_score=75, auto_upload=True)
        self.assertEqual(res["uploaded"], 1)

        # Verify upload_video was passed enriched sidecar_data
        call_kwargs = self.mock_uploader.upload_video.call_args[1]
        sidecar_data = call_kwargs.get("sidecar_data")
        self.assertIsNotNone(sidecar_data)
        self.assertEqual(sidecar_data.get("selected_viral_title"), "Title Option 1 #Shorts")

        # Verify manifest persistence
        rec = self.pipeline.manifest.get("c_test")
        self.assertEqual(rec["state"], STATE_UPLOADED)
        self.assertEqual(rec["selected_viral_title"], "Title Option 1 #Shorts")
        self.assertEqual(rec["title"], "Title Option 1 #Shorts")
        self.assertEqual(rec["analytical_title"], "Analytical Kai Highlight")

        # Verify sidecar file on disk was updated
        with open(self.short_sidecar, "r", encoding="utf-8") as fh:
            disk_sidecar = json.load(fh)
        self.assertEqual(disk_sidecar["selected_viral_title"], "Title Option 1 #Shorts")
        self.assertIn("viral_metadata", disk_sidecar)

    def test_interactive_selection_of_option_2(self):
        self.mock_uploader.upload_video.return_value = {
            "video_id": "yt_int_2",
            "url": "https://www.youtube.com/shorts/yt_int_2",
        }

        # User chooses option 2, then approves
        res = self.pipeline.upload_shorts(
            min_score=75,
            interactive=True,
            prompt_fn=lambda cand: "a",
            title_prompt_fn=lambda cand, titles: "2",
        )
        self.assertEqual(res["uploaded"], 1)

        rec = self.pipeline.manifest.get("c_test")
        self.assertEqual(rec["selected_viral_title"], "Title Option 2 #Shorts")

    def test_interactive_custom_title_input(self):
        self.mock_uploader.upload_video.return_value = {
            "video_id": "yt_cust_3",
            "url": "https://www.youtube.com/shorts/yt_cust_3",
        }

        # User enters custom title
        res = self.pipeline.upload_shorts(
            min_score=75,
            interactive=True,
            prompt_fn=lambda cand: "a",
            title_prompt_fn=lambda cand, titles: "My Custom Viral Title",
        )
        self.assertEqual(res["uploaded"], 1)

        rec = self.pipeline.manifest.get("c_test")
        # Ensure #Shorts is appended and sanitized
        self.assertEqual(rec["selected_viral_title"], "My Custom Viral Title #Shorts")


class TestSnippetFormattingWithViralMetadata(unittest.TestCase):
    """Test format_youtube_snippet in metadata.py prioritizes viral metadata."""

    def test_snippet_prefers_selected_viral_title_and_metadata(self):
        sidecar = {
            "streamer_name": "xqc",
            "analysis": {
                "title": "Raw Analytical Title",
                "reaction_summary": "Raw summary",
            },
            "selected_viral_title": "Best XQC Clutch Ever #Shorts",
            "viral_metadata": {
                "description": "Viral Description for XQC!\n🎮 Streamer: @xqc\n#Shorts",
                "tags": ["xqc", "gta 6", "viral gaming"],
            },
        }

        snippet = format_youtube_snippet(sidecar)
        self.assertEqual(snippet["title"], "Best XQC Clutch Ever #Shorts")
        self.assertIn("Viral Description for XQC!", snippet["description"])
        self.assertEqual(snippet["tags"], ["xqc", "gta 6", "viral gaming"])

    def test_snippet_falls_back_when_viral_metadata_absent(self):
        sidecar = {
            "streamer_name": "xqc",
            "analysis": {
                "title": "Raw Analytical Title",
                "reaction_summary": "Raw reaction summary",
                "category": "funny_moment",
            },
        }

        snippet = format_youtube_snippet(sidecar)
        self.assertEqual(snippet["title"], "Raw Analytical Title #Shorts")
        self.assertIn("Raw reaction summary", snippet["description"])
        self.assertIn("Streamer: xqc", snippet["description"])


if __name__ == "__main__":
    unittest.main()
