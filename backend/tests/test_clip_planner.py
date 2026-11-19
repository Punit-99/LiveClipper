"""Offline unit tests for Stage 5.1 ClipWindowPlanner, RecordingConfig, and timeout update."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.analysis.models import AnalysisConfig
from app.config import Settings
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent
from app.recording import (
    ClipWindowPlanner,
    ExtractedClipMetadata,
    RecordingConfig,
    SegmentIndex,
    VideoSegment,
    build_recording_config,
)


def _event(
    *,
    trigger: float = 1000.0,
    start: float | None = None,
    end: float | None = None,
    merged_count: int = 0,
    messages: list[ChatMessage] | None = None,
) -> HighlightEvent:
    st = trigger - 10.0 if start is None else start
    en = trigger + 20.0 if end is None else end
    msgs = messages if messages is not None else [
        ChatMessage("u1", "before", trigger - 4.0),
        ChatMessage("u2", "NO WAY", trigger + 0.5, emote_count=2),
        ChatMessage("u3", "CLIP IT", trigger + 1.5, emote_count=1),
    ]
    return HighlightEvent(
        stream_id="kaicenat",
        streamer_name="kaicenat",
        event_id="evt-plan-001",
        start_timestamp=st,
        end_timestamp=en,
        trigger_timestamp=trigger,
        messages=msgs,
        message_count=len(msgs),
        unique_chatters=len({m.username for m in msgs}),
        emote_count=sum(m.emote_count for m in msgs),
        activity_score=15.0,
        detection_signals=DetectionSignals(triggered=["rate_spike"]),
        merged_count=merged_count,
    )


class TestClipPlannerAndConfig(unittest.TestCase):
    def test_gemini_timeout_default_updated_to_30_seconds(self):
        self.assertEqual(AnalysisConfig().timeout_seconds, 30.0)
        dummy_settings = Settings(
            client_id="id",
            client_secret="sec",
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
        )
        self.assertEqual(dummy_settings.gemini_timeout_seconds, 30.0)
        rec_cfg = build_recording_config(dummy_settings)
        self.assertFalse(rec_cfg.enabled)
        self.assertEqual(rec_cfg.buffer_seconds, 300.0)
        self.assertEqual(rec_cfg.reaction_offset_seconds, 6.0)

    def test_recording_config_validation_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            RecordingConfig(segment_seconds=0)
        with self.assertRaises(ValueError):
            RecordingConfig(buffer_seconds=2.0, segment_seconds=4.0)
        with self.assertRaises(ValueError):
            RecordingConfig(max_active_pins=0)
        with self.assertRaises(ValueError):
            RecordingConfig(clip_max_duration_seconds=-5.0)

    def test_unmerged_event_applies_heuristic_offset_and_pre_post_margins(self):
        cfg = RecordingConfig(
            reaction_offset_seconds=6.0,
            clip_pre_seconds=12.0,
            clip_post_seconds=18.0,
            clip_max_duration_seconds=90.0,
        )
        planner = ClipWindowPlanner(cfg)
        plan = planner.plan(_event(trigger=1000.0, merged_count=0))

        # Estimated moment: 1000 - 6 = 994; clip: [982..1012] (30s)
        self.assertAlmostEqual(plan.estimated_moment_start, 994.0)
        self.assertAlmostEqual(plan.requested_clip_start, 982.0)
        self.assertAlmostEqual(plan.requested_clip_end, 1012.0)
        self.assertAlmostEqual(plan.requested_duration, 30.0)
        self.assertFalse(plan.truncated_to_peak)
        self.assertIn("reaction_offset_is_heuristic", plan.planning_notes)

    def test_merged_event_within_cap_preserves_full_merged_moment(self):
        # Mirrors our live #kaicenat event: trigger=1000, end=1038.4 (merged 1x, dur=48.4s)
        cfg = RecordingConfig(
            reaction_offset_seconds=6.0,
            clip_pre_seconds=12.0,
            clip_post_seconds=18.0,
            clip_max_duration_seconds=90.0,
        )
        msgs = [ChatMessage(f"u{i}", "hype", 1000.0 + i * 0.5) for i in range(40)]
        ev = _event(trigger=1000.0, start=990.0, end=1038.4, merged_count=1, messages=msgs)
        plan = ClipWindowPlanner(cfg).plan(ev)

        # Trigger moment = 994.0, active end ~1018.4 -> moment_end = 1012.4
        # Full clip = [982.0..1030.4] = 48.4s <= 90s cap
        self.assertFalse(plan.truncated_to_peak)
        self.assertAlmostEqual(plan.requested_clip_start, 982.0)
        self.assertAlmostEqual(plan.requested_clip_end, 1030.4, places=1)
        self.assertLessEqual(plan.requested_duration, 90.0)

    def test_long_clamped_merged_event_preserves_late_peak_not_first_window(self):
        # 120s clamped merged event where the real viral explosion happens at t=+85s!
        cfg = RecordingConfig(
            reaction_offset_seconds=6.0,
            clip_pre_seconds=12.0,
            clip_post_seconds=18.0,
            clip_max_duration_seconds=60.0,
        )
        msgs: list[ChatMessage] = []
        # Mild initial trigger cluster around t=1000..1010 (5 chatters)
        for i in range(5):
            msgs.append(ChatMessage(f"early{i}", "oh?", 1000.0 + i))
        # Quiet middle
        for i in range(5):
            msgs.append(ChatMessage(f"mid{i}", "wait", 1030.0 + i * 5))
        # Massive peak cluster at t=1082..1090 (40 distinct chatters with emotes!)
        for i in range(40):
            msgs.append(ChatMessage(f"peak{i}", "NOOOOOO CLIP IT", 1082.0 + i * 0.2, emote_count=3))
        # Trailing messages to 1110 (clamped at 120s from start=990)
        for i in range(5):
            msgs.append(ChatMessage(f"tail{i}", "lmaooo", 1095.0 + i * 3))

        ev = _event(trigger=1000.0, start=990.0, end=1110.0, merged_count=4, messages=msgs)
        plan = ClipWindowPlanner(cfg).plan(ev)

        self.assertTrue(plan.truncated_to_peak)
        self.assertAlmostEqual(plan.requested_duration, 60.0, places=1)
        # Peak was around ~1086 -> peak_moment ~1080; must NOT blindly take [982..1042]!
        self.assertGreater(plan.requested_clip_start, 1030.0)
        self.assertLess(plan.requested_clip_start, 1080.0)
        self.assertGreater(plan.requested_clip_end, 1080.0)
        self.assertIn("centered_on_peak_chat_activity", plan.planning_notes)

    def test_extracted_clip_metadata_json_round_trip(self):
        cfg = RecordingConfig()
        plan = ClipWindowPlanner(cfg).plan(_event(trigger=1000.0))
        idx = SegmentIndex()
        idx.add_segment(
            VideoSegment(
                segment_id="seg_00000.ts",
                path=Path("/tmp/seg_00000.ts"),
                session_id=1,
                wall_closed=1000.0,
                mono_closed=100.0,
                media_duration=4.0,
                ffmpeg_start_pts=0.0,
                ffmpeg_end_pts=4.0,
                size_bytes=1024,
            )
        )
        sel = idx.query_range(plan.requested_clip_start, plan.requested_clip_end)
        meta = ExtractedClipMetadata(
            event_id=plan.event_id,
            stream_id=plan.stream_id,
            streamer_name=plan.streamer_name,
            clip_path="clips/kaicenat/clip.mp4",
            extracted_at=1050.0,
            extracted_at_iso="2026-11-18T15:00:00+00:00",
            clip_plan=plan,
            selection=sel,
            analysis={"highlight_score": 85, "should_clip": True},
        )
        parsed = json.loads(meta.to_json())
        self.assertEqual(parsed["event_id"], plan.event_id)
        self.assertIn("clip_plan", parsed)
        self.assertIn("selection", parsed)
        self.assertIn("segments_used", parsed["selection"])
        self.assertIn("coverage", parsed["selection"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
