"""Rolling multi-signal analysis of chat activity.

The `SignalAnalyzer` maintains a rolling window of recent messages (keyed on a
monotonic clock) and, on demand, reports the richer signals the hybrid detector
cares about beyond raw rate: unique chatters, emote usage, repetition/spam, and
the busiest-user share. It also computes the per-message WEIGHT that lets emote
reactions amplify the trigger core.

Pure logic, no networking, no clock calls of its own.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass


def emote_count_from_tags(tags: dict[str, str]) -> int:
    """Count Twitch emotes in a PRIVMSG's `emotes` tag.

    Format: "25:0-4,6-10/1902:12-16" -> each comma-separated range is one emote
    use. Third-party emotes (BTTV/7TV) are not in this tag and are not counted.
    """
    raw = (tags or {}).get("emotes", "")
    if not raw:
        return 0
    total = 0
    for group in raw.split("/"):
        if not group:
            continue
        _, _, ranges = group.partition(":")
        if ranges:
            total += len([r for r in ranges.split(",") if r])
    return total


def normalize_text(text: str) -> str:
    """Normalize a message for repetition comparison (lowercase, collapse space)."""
    return " ".join(text.lower().split())


@dataclass(frozen=True)
class SignalSnapshot:
    """Instantaneous signal readings over the current window."""

    message_count: int
    messages_per_sec: float
    unique_chatters: int
    emote_count: int
    emote_rate: float
    repeat_ratio: float
    top_user_share: float


class SignalAnalyzer:
    """Rolling-window analyzer for the non-rate chat signals."""

    def __init__(self, window_seconds: float, emote_weight: float = 0.5) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._window = window_seconds
        self._emote_weight = max(0.0, emote_weight)
        # Each entry: (mono_ts, username, normalized_text, emote_count)
        self._entries: deque[tuple[float, str, str, int]] = deque()

    def weight_for(self, emote_count: int) -> float:
        """Emote-aware weight for the trigger core (1.0 for a plain message)."""
        # Cap the emote contribution so a single spammy emote wall can't dominate.
        return 1.0 + self._emote_weight * min(emote_count, 10)

    def add(self, now: float, username: str, text: str, emote_count: int) -> None:
        self._entries.append((now, username, normalize_text(text), emote_count))
        self._evict(now)

    def snapshot(self, now: float) -> SignalSnapshot:
        self._evict(now)
        entries = self._entries
        count = len(entries)
        if count == 0:
            return SignalSnapshot(0, 0.0, 0, 0, 0.0, 0.0, 0.0)

        usernames = Counter(e[1] for e in entries)
        texts = {e[2] for e in entries if e[2]}
        emote_total = sum(e[3] for e in entries)
        top_user = max(usernames.values())

        return SignalSnapshot(
            message_count=count,
            messages_per_sec=count / self._window,
            unique_chatters=len(usernames),
            emote_count=emote_total,
            emote_rate=emote_total / count,
            repeat_ratio=1.0 - (len(texts) / count),
            top_user_share=top_user / count,
        )

    def _evict(self, now: float) -> None:
        cutoff = now - self._window
        entries = self._entries
        while entries and entries[0][0] <= cutoff:
            entries.popleft()
