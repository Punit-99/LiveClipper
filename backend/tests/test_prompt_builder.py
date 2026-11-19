"""Deterministic offline unit tests for Stage 4 prompt engineering & formatting."""

from __future__ import annotations

import unittest

from app.analysis.prompt_builder import SYSTEM_INSTRUCTION, build_user_prompt
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent


def _event_with(messages: list[ChatMessage], trigger_ts: float = 1000.0) -> HighlightEvent:
    return HighlightEvent(
        stream_id="gta6_streamer",
        streamer_name="gta6_streamer",
        event_id="abcdef1234567890",
        start_timestamp=trigger_ts - 10.0,
        end_timestamp=trigger_ts + 20.0,
        trigger_timestamp=trigger_ts,
        messages=messages,
        message_count=len(messages),
        unique_chatters=len({m.username for m in messages}),
        emote_count=sum(m.emote_count for m in messages),
        activity_score=25.5,
        detection_signals=DetectionSignals(
            messages_per_sec=12.0,
            baseline_rate=3.0,
            rate_ratio=4.0,
            unique_chatters=len({m.username for m in messages}),
            emote_count=15,
            emote_rate=1.2,
            repeat_ratio=0.1,
            top_user_share=0.1,
            sustained_seconds=4.0,
            triggered=["rate_spike", "emote_burst"],
        ),
    )


class TestPromptBuilder(unittest.TestCase):
    def test_system_instruction_enforces_chat_only_and_injection_safety(self):
        lower = SYSTEM_INSTRUCTION.lower()
        self.assertIn("cannot see gameplay video", lower)
        self.assertIn("chat-based highlight candidate", lower)
        self.assertIn("untrusted", lower)
        self.assertIn("should_clip", lower)

    def test_relative_timestamps_and_signals_formatted(self):
        msgs = [
            ChatMessage("alice", "taking off now", 995.0),
            ChatMessage("bob", "NO WAY HE CRASHED", 1000.5, emote_count=2),
            ChatMessage("carol", "LMAOOO CLIP IT", 1004.0),
        ]
        prompt = build_user_prompt(_event_with(msgs, trigger_ts=1000.0))
        self.assertIn("Stream: gta6_streamer", prompt)
        self.assertIn("[-5.0s] alice: taking off now", prompt)
        self.assertIn("[+0.5s] bob: NO WAY HE CRASHED", prompt)
        self.assertIn("[+4.0s] carol: LMAOOO CLIP IT", prompt)
        self.assertIn("<chat_transcript>", prompt)
        self.assertIn("</chat_transcript>", prompt)
        self.assertIn("tags=[rate_spike, emote_burst]", prompt)

    def test_consecutive_duplicates_compressed_with_counts(self):
        msgs = [
            ChatMessage("u1", "KEKW", 1000.0),
            ChatMessage("u2", "kekw", 1000.2),
            ChatMessage("u3", "  KEKW  ", 1000.4),
            ChatMessage("u4", "WHAT A FAIL", 1001.0),
        ]
        prompt = build_user_prompt(_event_with(msgs, trigger_ts=1000.0))
        self.assertIn("repeated 3x consecutively by 3 users", prompt)
        self.assertIn("[+1.0s] u4: WHAT A FAIL", prompt)

    def test_large_transcript_truncated_preserving_pre_peak_and_post(self):
        msgs: list[ChatMessage] = []
        # 40 pre-spike messages (-10s..-0.2s)
        for i in range(40):
            msgs.append(ChatMessage(f"pre{i}", f"pre line {i}", 990.0 + i * 0.24))
        # 100 peak messages (0.0s..5.0s)
        for i in range(100):
            msgs.append(ChatMessage(f"pk{i}", f"peak reaction {i}", 1000.0 + i * 0.05))
        # 60 post-spike messages (5.5s..20.0s)
        for i in range(60):
            msgs.append(ChatMessage(f"post{i}", f"aftermath {i}", 1005.5 + i * 0.24))

        prompt = build_user_prompt(_event_with(msgs, trigger_ts=1000.0), max_messages=30)
        self.assertIn("additional entries omitted", prompt)
        # Pre-spike closest to trigger preserved
        self.assertIn("pre line 39", prompt)
        # Peak preserved
        self.assertIn("peak reaction", prompt)
        # Post-spike preserved
        self.assertIn("aftermath", prompt)
        # Total transcript lines inside <chat_transcript> <= 30
        body = prompt.split("<chat_transcript>\n", 1)[1].split("\n</chat_transcript>", 1)[0]
        self.assertLessEqual(len(body.splitlines()), 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
