"""Rolling buffer of recent chat, so an event can include PRE-spike context.

`RollingChatBuffer` keeps the last `horizon` seconds of messages (keyed on a
monotonic clock for correct windowing) alongside their wall-clock `ChatMessage`
form. When a spike fires, the detector asks the buffer for everything since
`trigger - pre_seconds` to seed the event with what led up to the moment.

Pure logic, no networking.
"""

from __future__ import annotations

from collections import deque

from app.detection.highlight_event import ChatMessage


class RollingChatBuffer:
    """Keeps recent messages within a time horizon for pre-spike context."""

    def __init__(self, horizon_seconds: float) -> None:
        if horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be > 0")
        self._horizon = horizon_seconds
        # Each entry: (mono_ts, ChatMessage)
        self._entries: deque[tuple[float, ChatMessage]] = deque()

    def add(self, mono_ts: float, message: ChatMessage) -> None:
        self._entries.append((mono_ts, message))
        self._prune(mono_ts)

    def since(self, mono_start: float) -> list[ChatMessage]:
        """Return messages with monotonic timestamp >= `mono_start`, in order."""
        return [msg for ts, msg in self._entries if ts >= mono_start]

    def _prune(self, now: float) -> None:
        cutoff = now - self._horizon
        entries = self._entries
        while entries and entries[0][0] < cutoff:
            entries.popleft()

    def __len__(self) -> int:
        return len(self._entries)
