"""Stage 6.3: Batched Helix channel liveness monitoring for multi-stream sessions.

Periodically queries Twitch Helix in batches of up to 100 channels per request
via `StreamFinder.check_live_channels`. Detects online/offline transitions,
triggers graceful session shutdown when a channel goes offline, and optional
restart when it returns online.

Reliability guarantees:
- Transient network errors or Twitch HTTP 429 rate limits NEVER falsely mark
  channels as offline.
- Polling runs in background threads (`asyncio.to_thread`) without blocking
  the asyncio event loop.
- Loop sleeps interruptibly on an `asyncio.Event` so shutdown is immediate.
- Callback exceptions are isolated so they cannot crash the liveness loop.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.discovery.stream_finder import DiscoveryError, LiveStream, RateLimitError, StreamFinder
from app.logging_setup import get_logger
from app.multistream.router import normalize_channel

log = get_logger(__name__)

CheckLiveFn = Callable[[list[str]], dict[str, LiveStream | None]]
OfflineCallback = Callable[[str], Any]
OnlineCallback = Callable[[str, LiveStream], Any]


@dataclass(frozen=True)
class LivenessStatusSnapshot:
    """Inspectable status of the channel liveness checker."""

    active_channels: list[str]
    last_check_mono: float | None
    last_check_wall: float | None
    consecutive_errors: int
    online_count: int
    offline_count: int
    is_running: bool


class ChannelLivenessChecker:
    """Periodic batched Helix liveness supervisor for multiple Twitch channels."""

    def __init__(
        self,
        stream_finder: StreamFinder | CheckLiveFn,
        *,
        poll_interval_seconds: float = 60.0,
        rate_limit_backoff_seconds: float = 30.0,
        mono_clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        on_channel_offline: OfflineCallback | None = None,
        on_channel_online: OnlineCallback | None = None,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")

        if isinstance(stream_finder, StreamFinder):
            self._check_fn: CheckLiveFn = stream_finder.check_live_channels
        elif callable(stream_finder):
            self._check_fn = stream_finder
        else:
            raise TypeError("stream_finder must be a StreamFinder or callable check function")

        self._poll_interval = poll_interval_seconds
        self._rate_limit_backoff = max(1.0, rate_limit_backoff_seconds)
        self._mono_clock = mono_clock
        self._wall_clock = wall_clock
        self._on_offline = on_channel_offline
        self._on_online = on_channel_online

        self._watched_channels: set[str] = set()
        self._known_states: dict[str, bool] = {}  # channel -> is_live

        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._running = False
        self._lock = asyncio.Lock()

        # Operational metrics.
        self._last_check_mono: float | None = None
        self._last_check_wall: float | None = None
        self._consecutive_errors: int = 0
        self.checks_performed: int = 0
        self.offline_events_detected: int = 0
        self.online_events_detected: int = 0

    # -- configuration & watched channels --------------------------------------

    def set_watched_channels(
        self, channels: list[str], *, initial_live: bool = False
    ) -> None:
        """Update the set of channels to poll for live status."""
        new_set = {normalize_channel(c) for c in channels if normalize_channel(c)}
        self._watched_channels = new_set
        if initial_live:
            for ch in new_set:
                self._known_states.setdefault(ch, True)
        # Prune known states for removed channels.
        for removed in list(self._known_states.keys()):
            if removed not in new_set:
                del self._known_states[removed]

    def add_channel(self, channel: str, *, initial_live: bool = False) -> None:
        key = normalize_channel(channel)
        if key:
            self._watched_channels.add(key)
            if initial_live:
                self._known_states.setdefault(key, True)

    def remove_channel(self, channel: str) -> None:
        key = normalize_channel(channel)
        self._watched_channels.discard(key)
        self._known_states.pop(key, None)

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def snapshot(self) -> LivenessStatusSnapshot:
        online = sum(1 for is_live in self._known_states.values() if is_live)
        offline = sum(1 for is_live in self._known_states.values() if not is_live)
        return LivenessStatusSnapshot(
            active_channels=sorted(self._watched_channels),
            last_check_mono=self._last_check_mono,
            last_check_wall=self._last_check_wall,
            consecutive_errors=self._consecutive_errors,
            online_count=online,
            offline_count=offline,
            is_running=self.is_running,
        )

    # -- execution & lifecycle -------------------------------------------------

    async def start(self) -> None:
        """Start the background liveness polling loop."""
        async with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._poll_loop(), name="helix-liveness-checker")
            log.info(
                "ChannelLivenessChecker started (poll_interval=%.1fs, watching=%d channel(s)).",
                self._poll_interval,
                len(self._watched_channels),
            )

    async def stop(self) -> None:
        """Stop background liveness polling immediately."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
            if self._stop_event is not None:
                self._stop_event.set()
            task = self._task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self._task = None
            log.info("ChannelLivenessChecker stopped.")

    async def check_now(
        self, channels: list[str] | None = None
    ) -> dict[str, LiveStream | None]:
        """Perform one batched live check in a thread and dispatch transition callbacks."""
        targets = (
            [normalize_channel(c) for c in channels if normalize_channel(c)]
            if channels is not None
            else sorted(self._watched_channels)
        )
        if not targets:
            return {}

        results = await asyncio.to_thread(self._check_fn, targets)
        now_mono = self._mono_clock()
        now_wall = self._wall_clock()
        self._last_check_mono = now_mono
        self._last_check_wall = now_wall
        self.checks_performed += 1
        self._consecutive_errors = 0

        # Detect online / offline transitions.
        for ch in targets:
            stream = results.get(ch)
            was_live = self._known_states.get(ch)
            is_live = stream is not None

            self._known_states[ch] = is_live

            if was_live is True and not is_live:
                self.offline_events_detected += 1
                log.warning("Twitch channel #%s went OFFLINE.", ch)
                await self._dispatch_offline(ch)
            elif was_live is False and is_live and stream is not None:
                self.online_events_detected += 1
                log.info(
                    "Twitch channel #%s came back ONLINE (viewers=%d).",
                    ch,
                    stream.viewer_count,
                )
                await self._dispatch_online(ch, stream)

        return results

    # -- internal polling loop -------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running:
            # Sleep first so startup doesn't stall with redundant immediate Helix calls.
            if await self._interruptible_sleep(self._poll_interval):
                break

            if not self._watched_channels:
                continue

            try:
                await self.check_now()
            except RateLimitError as exc:
                self._consecutive_errors += 1
                backoff = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else self._rate_limit_backoff
                )
                log.warning(
                    "Twitch Helix rate limited (HTTP 429); pausing liveness check for %.1fs (%s).",
                    backoff,
                    exc,
                )
                if await self._interruptible_sleep(backoff):
                    break
            except (DiscoveryError, Exception) as exc:  # noqa: BLE001
                self._consecutive_errors += 1
                log.warning("Transient error checking channel liveness (will retry): %s", exc)
                if await self._interruptible_sleep(min(15.0, self._poll_interval)):
                    break

    async def _interruptible_sleep(self, seconds: float) -> bool:
        """Sleep for `seconds` or until `self.stop()` is called. Returns True if stopped."""
        if not self._running or seconds <= 0:
            return not self._running
        stop_ev = self._stop_event
        if stop_ev is None:
            await asyncio.sleep(seconds)
            return not self._running
        try:
            await asyncio.wait_for(stop_ev.wait(), timeout=seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _dispatch_offline(self, channel: str) -> None:
        if self._on_offline is None:
            return
        try:
            res = self._on_offline(channel)
            if asyncio.iscoroutine(res):
                await res
        except Exception:  # noqa: BLE001
            log.exception("on_channel_offline callback raised for channel #%s.", channel)

    async def _dispatch_online(self, channel: str, stream: LiveStream) -> None:
        if self._on_online is None:
            return
        try:
            res = self._on_online(channel, stream)
            if asyncio.iscoroutine(res):
                await res
        except Exception:  # noqa: BLE001
            log.exception("on_channel_online callback raised for channel #%s.", channel)
