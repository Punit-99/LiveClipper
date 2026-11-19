"""Deterministic offline unit tests for the Stage 4 local pre-filter."""

from __future__ import annotations

import unittest

from app.analysis.prefilter import evaluate_prefilter
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent


def _make_event(
    messages: list[ChatMessage],
    *,
    unique_chatters: int | None = None,
    event_id: str = "evt-test-1",
) -> HighlightEvent:
    chatters = (
        unique_chatters
        if unique_chatters is not None
        else len({m.username for m in messages if m.username})
    )
    return HighlightEvent(
        stream_id="test_stream",
        streamer_name="test_streamer",
        event_id=event_id,
        start_timestamp=1000.0,
        end_timestamp=1030.0,
        trigger_timestamp=1010.0,
        messages=messages,
        message_count=len(messages),
        unique_chatters=chatters,
        emote_count=sum(m.emote_count for m in messages),
        activity_score=10.0,
        detection_signals=DetectionSignals(
            messages_per_sec=2.0,
            baseline_rate=0.5,
            rate_ratio=4.0,
            unique_chatters=chatters,
            triggered=["rate_spike"],
        ),
    )


class TestPrefilter(unittest.TestCase):
    def test_empty_event_rejected(self):
        ev = _make_event([])
        res = evaluate_prefilter(ev)
        self.assertFalse(res.should_analyze)
        self.assertEqual(res.reason, "empty_event")

    def test_whitespace_only_messages_rejected(self):
        msgs = [ChatMessage("u1", "   ", 1010.0 + i) for i in range(6)]
        res = evaluate_prefilter(_make_event(msgs))
        self.assertFalse(res.should_analyze)
        self.assertEqual(res.reason, "no_text_content")

    def test_small_stream_one_or_two_chatters_passes(self):
        # Adjustment #2: small streams with < 3 unique chatters must NOT be rejected.
        msgs = [
            ChatMessage("viewer1", "wait is he really doing that", 1005.0),
            ChatMessage("viewer2", "NO WAY HE JUST JUMPED THE BRIDGE", 1010.0),
            ChatMessage("viewer1", "LMAOOOO WHAT WAS THAT PHYSICS", 1011.0),
            ChatMessage("viewer2", "CLIP THAT RIGHT NOW", 1012.0),
            ChatMessage("viewer1", "best GTA 6 moment all night", 1014.0),
        ]
        res = evaluate_prefilter(_make_event(msgs, unique_chatters=2))
        self.assertTrue(res.should_analyze)
        self.assertIsNone(res.reason)

    def test_single_chatter_varied_reactions_passes(self):
        msgs = [
            ChatMessage("loyal_viewer", f"omg reaction {i}", 1010.0 + i)
            for i in range(12)
        ]
        res = evaluate_prefilter(_make_event(msgs, unique_chatters=1))
        self.assertTrue(res.should_analyze)

    def test_crowd_chant_many_unique_chatters_passes(self):
        # 25 different viewers all typing "NO WAY" is a genuine Twitch reaction, not bot spam.
        msgs = [ChatMessage(f"user{i}", "NO WAY", 1010.0 + i * 0.1) for i in range(25)]
        res = evaluate_prefilter(_make_event(msgs, unique_chatters=25))
        self.assertTrue(res.should_analyze)

    def test_bot_command_spam_rejected(self):
        msgs = [ChatMessage(f"u{i}", "!raffle", 1010.0 + i * 0.2) for i in range(10)]
        res = evaluate_prefilter(_make_event(msgs))
        self.assertFalse(res.should_analyze)
        self.assertEqual(res.reason, "bot_command_spam")

    def test_url_spam_flood_rejected(self):
        msgs = [
            ChatMessage("bot1", "FREE FOLLOWERS https://scam.example/win", 1010.0 + i)
            for i in range(8)
        ]
        res = evaluate_prefilter(_make_event(msgs))
        self.assertFalse(res.should_analyze)
        self.assertEqual(res.reason, "url_spam_flood")

    def test_repetitive_bot_copypasta_flood_rejected(self):
        msgs = [
            ChatMessage("bot1" if i % 2 == 0 else "bot2", "BUY CHEAP VIEWERS NOW", 1010.0 + i * 0.2)
            for i in range(20)
        ]
        res = evaluate_prefilter(_make_event(msgs, unique_chatters=2))
        self.assertFalse(res.should_analyze)
        self.assertEqual(res.reason, "repetitive_spam_flood")


if __name__ == "__main__":
    unittest.main(verbosity=2)
