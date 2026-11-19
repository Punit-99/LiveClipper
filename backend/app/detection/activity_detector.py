"""Detect sudden spikes in chat activity (the trigger core of highlight detection).

This module is deliberately PURE: it has no networking, no I/O, and does not call
the clock itself. The caller passes a timestamp (`now`, in seconds) with every
message. That design gives us two things:

1. Production code passes `time.monotonic()`  immune to wall-clock jumps.
2. Tests pass fixed numbers  fully deterministic, no sleeps.

How detection works
-------------------
- We keep a rolling window of recent messages (default 10s) in a deque. Each
  entry carries a WEIGHT (default 1.0). Old entries are evicted.
      current rate = sum(weights in window) / window_seconds
  With the default weight of 1.0 this is simply messages-per-second. Callers can
  pass a larger weight (e.g. for emote-heavy messages) so those reactions count
  for more  this is how the hybrid detector folds the emote signal into the
  same, well-tested baseline machinery.

- The BASELINE ("normal" rate) is learned from completed one-second bins via an
  exponential moving average (EMA). Using 1-second bins (not the 10s window)
  makes the baseline converge in about a second and keeps its units in rate.

- Two protections keep the baseline honest:
    * It is FROZEN during the post-spike cooldown.
    * It ignores any second already elevated (>= freeze_ratio x baseline), so a
      spike's own ramp-up cannot inflate the baseline and mask the spike.

- A spike fires when, past warmup and outside cooldown, the current windowed
  rate is >= multiplier x baseline AND there are at least `min_messages` actual
  messages in the window (the count guard prevents false positives in quiet
  chats; it counts messages, not weight).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ActivityDetectorConfig:
    """Tunable thresholds. All durations are in seconds."""

    window_seconds: float = 10.0          # rolling window for "current" rate
    spike_multiplier: float = 3.0         # rate >= this * baseline => candidate spike
    min_messages: int = 10                # need at least this many msgs in window
    cooldown_seconds: float = 15.0        # min gap between spike alerts
    warmup_seconds: float = 30.0          # ignore spikes until this much data seen
    baseline_alpha: float = 0.3           # EMA smoothing (0..1); higher = faster
    baseline_bin_seconds: float = 1.0     # size of each baseline sampling bin
    baseline_freeze_ratio: float = 1.5    # don't learn from seconds busier than this

    def __post_init__(self) -> None:
        # Guard against misconfiguration that would divide by zero or never fire.
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.baseline_bin_seconds <= 0:
            raise ValueError("baseline_bin_seconds must be > 0")
        if self.spike_multiplier <= 0:
            raise ValueError("spike_multiplier must be > 0")
        if self.min_messages < 1:
            raise ValueError("min_messages must be >= 1")
        if not 0.0 < self.baseline_alpha <= 1.0:
            raise ValueError("baseline_alpha must be in (0, 1]")


@dataclass(frozen=True)
class SpikeEvent:
    """A detected spike.

    `timestamp` is the MONOTONIC clock value when it fired (used internally for
    spacing; not a wall-clock time). `wall_time` is epoch seconds for display.
    `messages_per_sec` is the (possibly weighted) activity rate at the trigger.
    """

    timestamp: float       # monotonic seconds (opaque; for spacing/tests only)
    messages_per_sec: float
    baseline: float
    multiplier: float
    message_count: int
    wall_time: float = 0.0  # epoch seconds, for human-readable logs


class ActivityDetector:
    """Rolling-window chat spike detector. Feed it messages via `add_message`."""

    def __init__(self, config: ActivityDetectorConfig | None = None) -> None:
        self.config = config or ActivityDetectorConfig()
        self._entries: deque[tuple[float, float]] = deque()  # (timestamp, weight)
        self._weight_sum: float = 0.0
        self._baseline: float = 0.0
        self._started_at: float | None = None
        self._last_spike_at: float | None = None
        # Current baseline-sampling bin (accumulates weight, not raw count):
        self._bin_start: float = 0.0
        self._bin_weight: float = 0.0
        self.spikes: list[SpikeEvent] = []

    # -- public API -------------------------------------------------------------

    def add_message(
        self, now: float, wall_time: float | None = None, weight: float = 1.0
    ) -> SpikeEvent | None:
        """Record one message and return a SpikeEvent if one fires.

        Args:
            now: a MONOTONIC clock reading (seconds). Must be non-decreasing.
            wall_time: optional epoch seconds carried onto the SpikeEvent for
                human-readable logging. Defaults to `now` if omitted.
            weight: how much this message contributes to the activity rate
                (default 1.0 == one message). Emote-heavy messages use >1.0.
        """
        if weight <= 0:
            weight = 1.0
        if self._started_at is None:
            self._started_at = now
            self._bin_start = now

        self._roll_bins(now)       # close out any finished 1s bins -> learn baseline
        self._bin_weight += weight
        self._entries.append((now, weight))
        self._weight_sum += weight
        self._evict(now)
        return self._detect(now, wall_time if wall_time is not None else now)

    @property
    def baseline(self) -> float:
        """Current baseline rate."""
        return self._baseline

    @property
    def message_count(self) -> int:
        """Number of messages currently inside the rolling window (unweighted)."""
        return len(self._entries)

    def current_rate(self, now: float) -> float:
        """Weighted rate over the window as of `now` (evicts expired entries)."""
        self._evict(now)
        return self._rate()

    # -- internals --------------------------------------------------------------

    def _evict(self, now: float) -> None:
        """Drop entries older than the rolling window, adjusting the weight sum."""
        cutoff = now - self.config.window_seconds
        entries = self._entries
        while entries and entries[0][0] <= cutoff:
            _, w = entries.popleft()
            self._weight_sum -= w
        if not entries:
            self._weight_sum = 0.0  # guard against float drift

    def _rate(self) -> float:
        return self._weight_sum / self.config.window_seconds

    def _roll_bins(self, now: float) -> None:
        """Close each elapsed one-second bin and feed its rate to the baseline."""
        bin_size = self.config.baseline_bin_seconds
        # Guard against pathological huge gaps rolling thousands of empty bins.
        max_bins = int(self.config.warmup_seconds / bin_size) + 2
        rolled = 0
        while now - self._bin_start >= bin_size and rolled < max_bins:
            completed_weight = self._bin_weight
            self._bin_start += bin_size
            self._bin_weight = 0.0
            self._learn_baseline(now, completed_weight / bin_size)
            rolled += 1
        # If we hit the cap (a long silence/gap), resync the bin to 'now'.
        if now - self._bin_start >= bin_size:
            self._bin_start = now
            self._bin_weight = 0.0

    def _learn_baseline(self, now: float, rate: float) -> None:
        """Update the baseline EMA from one completed second's rate."""
        cfg = self.config
        in_cooldown = (
            self._last_spike_at is not None
            and (now - self._last_spike_at) < cfg.cooldown_seconds
        )
        if in_cooldown:
            return
        if self._baseline == 0.0:
            self._baseline = rate          # seed from the first completed bin
            return
        if rate >= cfg.baseline_freeze_ratio * self._baseline:
            return                         # elevated second: don't learn from it
        a = cfg.baseline_alpha
        self._baseline = (1.0 - a) * self._baseline + a * rate

    def _detect(self, now: float, wall_time: float) -> SpikeEvent | None:
        cfg = self.config
        count = len(self._entries)
        rate = self._rate()

        assert self._started_at is not None  # set in add_message
        warmed_up = (now - self._started_at) >= cfg.warmup_seconds
        in_cooldown = (
            self._last_spike_at is not None
            and (now - self._last_spike_at) < cfg.cooldown_seconds
        )

        is_spike = (
            warmed_up
            and not in_cooldown
            and self._baseline > 0.0
            and count >= cfg.min_messages
            and rate >= cfg.spike_multiplier * self._baseline
        )
        if not is_spike:
            return None

        event = SpikeEvent(
            timestamp=now,
            messages_per_sec=rate,
            baseline=self._baseline,
            multiplier=rate / self._baseline,
            message_count=count,
            wall_time=wall_time,
        )
        self._last_spike_at = now
        self.spikes.append(event)
        return event
