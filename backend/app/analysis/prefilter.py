"""Cheap, deterministic local pre-filter run BEFORE calling the Gemini API.

Why this exists:
- Saves API tokens and quota by catching clearly invalid events, bot command
  floods (`!drop`, `!raffle`, `!giveaway`), promotional link floods, and obvious
  repetitive bot/single-user copypasta locally at $0 cost.
- Deliberately does NOT reject small streams for having few unique chatters
  (e.g. 1-2 active viewers can still produce genuine highlights!).
- Deliberately does NOT reject genuine crowd chants (e.g. 30 distinct viewers
  all shouting "NO WAY" or "POG") because each chatter only sends ~1 message.

Pure logic: no networking, no clock calls, fully deterministic and unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.detection.highlight_event import HighlightEvent
from app.detection.signals import normalize_text

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


@dataclass(frozen=True)
class PrefilterResult:
    """Decision on whether a candidate event should be sent to Gemini."""

    should_analyze: bool
    reason: str | None = None


def evaluate_prefilter(event: HighlightEvent) -> PrefilterResult:
    """Return whether `event` is worth sending to Gemini.

    Rejects only:
    1. Empty or text-less events (`empty_event`, `no_text_content`).
    2. Repeated bot-command surges (`bot_command_spam`), e.g. >= 80% `!cmd`.
    3. Promotional URL spam floods (`url_spam_flood`).
    4. Obvious repetitive spam floods where a tiny number of accounts paste the
       same 1-2 lines over and over (`repetitive_spam_flood`).
    """
    if event.message_count <= 0 or not event.messages:
        return PrefilterResult(False, "empty_event")

    stripped_texts = [m.text.strip() for m in event.messages if m.text and m.text.strip()]
    total_non_empty = len(stripped_texts)
    if total_non_empty == 0:
        return PrefilterResult(False, "no_text_content")

    # 1) Repeated bot commands (e.g. !giveaway, !drop, !raffle, !enter).
    if total_non_empty >= 5:
        command_count = sum(1 for t in stripped_texts if t.startswith("!"))
        if (command_count / total_non_empty) >= 0.80:
            return PrefilterResult(False, "bot_command_spam")

    # 2) Promotional link / URL spam flood.
    if total_non_empty >= 5:
        url_count = sum(1 for t in stripped_texts if _URL_RE.search(t))
        if (url_count / total_non_empty) >= 0.70:
            return PrefilterResult(False, "url_spam_flood")

    # 3) Obvious repetitive spam flood (bots or trolls pasting identical lines).
    # Note: we require BOTH high repetition (<=2 unique texts, repeat_ratio >= 0.90)
    # AND high messages-per-chatter (>= 6.0). This ensures:
    # - Small streams (1-2 real viewers with varied reactions) PASS.
    # - Large crowd chants (30 distinct viewers each typing "NO WAY") PASS.
    # - 1-2 bots repeating the same copypasta line 10+ times are REJECTED.
    if total_non_empty >= 10:
        normalized = [normalize_text(t) for t in stripped_texts]
        unique_texts = len(set(normalized))
        repeat_ratio = 1.0 - (unique_texts / total_non_empty)
        unique_chatters = max(
            len({m.username for m in event.messages if m.username}),
            event.unique_chatters,
            1,
        )
        msgs_per_chatter = total_non_empty / unique_chatters
        if unique_texts <= 2 and repeat_ratio >= 0.90 and msgs_per_chatter >= 6.0:
            return PrefilterResult(False, "repetitive_spam_flood")

    return PrefilterResult(True, None)
