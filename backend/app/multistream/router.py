"""Stage 6.2: Thread- and async-safe analysis & drop router for multi-stream sessions.

Routes completed `AnalyzedHighlight` verdicts (and dropped `HighlightEvent`
notifications) from the shared `HighlightAnalysisWorker` back to the owning
`StreamSession` based on normalized Twitch channel / stream identifier.

Design guarantees:
- Thread-safe registration, unregistration, and lookup via `threading.RLock`.
- Lock is ALWAYS released before invoking downstream handlers (no lock-order
  inversions or re-entrant deadlocks).
- Verdicts or drops arriving for a stopped or unregistered session are logged
  and discarded cleanly — NEVER raising into the shared Gemini worker.
- Exceptions raised by one session's handler are caught and isolated so other
  active streams are unaffected.
- Optional `expected=` guard on `unregister()` prevents a slow teardown of an
  older session instance from unregistering a freshly restarted session for
  the same channel.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable

from app.analysis.models import AnalysisSink, AnalyzedHighlight
from app.detection.highlight_event import HighlightEvent
from app.logging_setup import get_logger

log = get_logger(__name__)

DropCallback = Callable[[HighlightEvent, str], None]


def normalize_channel(channel: str) -> str:
    """Normalize a Twitch channel / stream identifier to a canonical lowercase key."""
    return channel.lstrip("#").strip().lower()


@dataclass(frozen=True)
class _RouteEntry:
    target: Any
    analysis_handler: AnalysisSink
    drop_handler: DropCallback | None = None


class StreamRoutingAnalysisSink:
    """Thread-safe `AnalysisSink` dispatching Gemini results to per-stream sessions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._routes: dict[str, _RouteEntry] = {}

        # Operational counters (protected by self._lock).
        self.routed_count: int = 0
        self.discarded_count: int = 0
        self.dropped_routed_count: int = 0
        self.dropped_discarded_count: int = 0
        self.error_count: int = 0

    # -- registration ----------------------------------------------------------

    def register(
        self,
        channel: str,
        target: Any,
        *,
        on_dropped: DropCallback | None = None,
    ) -> str:
        """Register a `StreamSession` or `AnalysisSink` callable for `channel`.

        Returns the normalized channel key.
        """
        key = normalize_channel(channel)
        if not key:
            raise ValueError("channel must be a non-empty stream/channel identifier")

        if hasattr(target, "handle_analysis_result") and callable(
            target.handle_analysis_result
        ):
            analysis_handler: AnalysisSink = target.handle_analysis_result
        elif callable(target):
            analysis_handler = target
        else:
            raise TypeError(
                "target must be callable or provide a callable handle_analysis_result(result)"
            )

        if on_dropped is not None:
            drop_handler: DropCallback | None = on_dropped
        else:
            candidate = getattr(target, "handle_dropped_event", None)
            drop_handler = candidate if callable(candidate) else None

        with self._lock:
            self._routes[key] = _RouteEntry(
                target=target,
                analysis_handler=analysis_handler,
                drop_handler=drop_handler,
            )
        return key

    def unregister(self, channel: str, *, expected: Any | None = None) -> bool:
        """Remove the route for `channel`.

        When `expected` is provided, removes the route ONLY if the currently
        registered target is `expected` (protecting restarted sessions).
        """
        key = normalize_channel(channel)
        if not key:
            return False
        with self._lock:
            entry = self._routes.get(key)
            if entry is None:
                return False
            if expected is not None and entry.target is not expected:
                return False
            del self._routes[key]
            return True

    def has_route(self, channel: str) -> bool:
        key = normalize_channel(channel)
        with self._lock:
            return key in self._routes

    def registered_channels(self) -> list[str]:
        with self._lock:
            return sorted(self._routes.keys())

    # -- synchronous AnalysisSink & drop routing -------------------------------

    def __call__(self, result: AnalyzedHighlight) -> None:
        """`AnalysisSink` interface: route `result` without blocking or raising."""
        self.route(result)

    def route(self, result: AnalyzedHighlight) -> bool:
        """Route one `AnalyzedHighlight` to its owning stream session.

        Returns True if dispatched to an active route, False if discarded or errored.
        """
        raw_id = result.stream_id or result.streamer_name
        key = normalize_channel(raw_id) if raw_id else ""

        with self._lock:
            entry = self._routes.get(key)
            if entry is None:
                self.discarded_count += 1

        if entry is None:
            log.info(
                "Discarding Gemini verdict for stopped/unregistered stream #%s "
                "(event=%s status=%s).",
                key or "unknown",
                result.event_id[:8],
                result.status.value,
            )
            return False

        try:
            accepted = entry.analysis_handler(result)
        except Exception:  # noqa: BLE001  isolate per-session faults from shared worker
            with self._lock:
                self.error_count += 1
            log.exception(
                "Analysis route handler raised for stream #%s (event=%s).",
                key,
                result.event_id[:8],
            )
            return False

        # If a StreamSession returned False explicitly because it had already
        # closed in a race right after lookup, count as discarded; otherwise routed.
        if accepted is False and hasattr(entry.target, "handle_analysis_result"):
            with self._lock:
                self.discarded_count += 1
            return False

        with self._lock:
            self.routed_count += 1
        return True

    def handle_dropped_event(self, event: HighlightEvent, reason: str) -> bool:
        """Route a dropped `HighlightEvent` notification to its owning stream session.

        Safe to pass directly as `on_event_dropped=` on `HighlightAnalysisWorker`.
        """
        raw_id = event.stream_id or event.streamer_name
        key = normalize_channel(raw_id) if raw_id else ""

        with self._lock:
            entry = self._routes.get(key)
            if entry is None or entry.drop_handler is None:
                self.dropped_discarded_count += 1

        if entry is None or entry.drop_handler is None:
            log.info(
                "Discarding dropped-event cleanup for stopped/unregistered stream #%s "
                "(event=%s reason=%s).",
                key or "unknown",
                event.event_id[:8],
                reason,
            )
            return False

        try:
            entry.drop_handler(event, reason)
        except Exception:  # noqa: BLE001  isolate drop callback faults
            with self._lock:
                self.error_count += 1
            log.exception(
                "Drop route handler raised for stream #%s (event=%s reason=%s).",
                key,
                event.event_id[:8],
                reason,
            )
            return False

        with self._lock:
            self.dropped_routed_count += 1
        return True
