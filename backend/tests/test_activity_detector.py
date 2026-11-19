"""Deterministic unit tests for ActivityDetector.

No real time is used: every message is fed with an explicit timestamp, so the
tests are fast and reproducible.
"""

from __future__ import annotations

import unittest

from app.detection import ActivityDetector, ActivityDetectorConfig, SpikeEvent


def feed_rate(
    det: ActivityDetector,
    start: float,
    duration: float,
    msgs_per_sec: int,
    step: float = 1.0,
) -> list[SpikeEvent]:
    """Feed a steady rate, returning any spikes that fired.

    At each tick we add `msgs_per_sec * step` messages stamped at that tick.
    Returns the final time via det state; events are collected and returned.
    """
    events: list[SpikeEvent] = []
    t = start
    per_tick = int(msgs_per_sec * step)
    end = start + duration
    while t < end - 1e-9:
        for _ in range(per_tick):
            ev = det.add_message(t)
            if ev is not None:
                events.append(ev)
        t += step
    return events


class TestActivityDetector(unittest.TestCase):
    def _config(self, **overrides) -> ActivityDetectorConfig:
        base = dict(
            window_seconds=10.0,
            spike_multiplier=3.0,
            min_messages=10,
            cooldown_seconds=15.0,
            warmup_seconds=30.0,
            baseline_alpha=0.3,
            baseline_bin_seconds=1.0,
            baseline_freeze_ratio=1.5,
        )
        base.update(overrides)
        return ActivityDetectorConfig(**base)

    # 1) Steady, normal activity should never trigger a spike.
    def test_normal_activity_no_spike(self):
        det = ActivityDetector(self._config())
        events = feed_rate(det, start=0.0, duration=80.0, msgs_per_sec=10)
        self.assertEqual(events, [])
        # Baseline should have settled near the steady rate.
        self.assertAlmostEqual(det.baseline, 10.0, delta=1.0)

    # 2) A sudden burst after a calm baseline triggers exactly one spike.
    def test_sudden_spike_detected(self):
        det = ActivityDetector(self._config())
        feed_rate(det, start=0.0, duration=40.0, msgs_per_sec=10)   # baseline ~10
        events = feed_rate(det, start=40.0, duration=10.0, msgs_per_sec=40)
        self.assertGreaterEqual(len(events), 1)
        spike = events[0]
        self.assertGreaterEqual(spike.multiplier, 3.0)
        self.assertAlmostEqual(spike.baseline, 10.0, delta=1.5)
        self.assertGreaterEqual(spike.messages_per_sec, spike.baseline * 3)
        self.assertGreaterEqual(spike.message_count, 10)

    # 3) No spike during warmup even if activity is huge.
    def test_no_spike_during_warmup(self):
        det = ActivityDetector(self._config(warmup_seconds=30.0))
        # Huge burst at t=5s, well before warmup completes.
        events = feed_rate(det, start=0.0, duration=10.0, msgs_per_sec=100)
        self.assertEqual(events, [])

    # 4) Cooldown: sustained high activity yields spaced-out alerts, not a flood.
    def test_cooldown_between_spikes(self):
        det = ActivityDetector(self._config(cooldown_seconds=15.0))
        feed_rate(det, start=0.0, duration=40.0, msgs_per_sec=10)   # baseline ~10
        events = feed_rate(det, start=40.0, duration=45.0, msgs_per_sec=40)
        self.assertGreaterEqual(len(events), 2)
        # Consecutive alerts must be at least the cooldown apart.
        for earlier, later in zip(events, events[1:]):
            self.assertGreaterEqual(later.timestamp - earlier.timestamp, 15.0)

    # 5) Expired messages are removed from the rolling window.
    def test_expired_messages_removed(self):
        det = ActivityDetector(self._config(window_seconds=10.0))
        for t in range(10):           # 10 messages at t=0..9
            det.add_message(float(t))
        self.assertEqual(det.message_count, 10)
        # Jump far ahead: all previous messages are now expired.
        det.add_message(100.0)
        self.assertEqual(det.message_count, 1)
        self.assertEqual(det.current_rate(100.0), 1 / 10.0)

    # 6) min_messages guards against false positives in low-volume chats.
    def test_min_messages_prevents_false_positive(self):
        # baseline ~1/s (window holds ~10 msgs). A 4x jump in RATIO, but the
        # absolute count stays below min_messages=50 -> no spike.
        det = ActivityDetector(self._config(min_messages=50))
        feed_rate(det, start=0.0, duration=40.0, msgs_per_sec=1)    # baseline ~1
        events = feed_rate(det, start=40.0, duration=10.0, msgs_per_sec=4)
        self.assertEqual(events, [])

    # 7) Activity below the multiplier threshold never spikes.
    def test_below_threshold_no_spike(self):
        det = ActivityDetector(self._config(spike_multiplier=3.0))
        feed_rate(det, start=0.0, duration=40.0, msgs_per_sec=10)   # baseline ~10
        # 2.5x the baseline -> under the 3x threshold.
        events = feed_rate(det, start=40.0, duration=15.0, msgs_per_sec=25)
        self.assertEqual(events, [])

    # 8) A fired spike carries the (monotonic) timestamp it fired at.
    def test_spike_event_has_timestamp(self):
        det = ActivityDetector(self._config())
        feed_rate(det, start=0.0, duration=40.0, msgs_per_sec=10)
        events = feed_rate(det, start=40.0, duration=10.0, msgs_per_sec=40)
        self.assertTrue(events)
        self.assertGreaterEqual(events[0].timestamp, 40.0)
        self.assertLessEqual(events[0].timestamp, 50.0)

    # 9) Monotonic `now` and wall-clock `wall_time` are tracked independently:
    #    intervals use `now`; the event's wall_time is preserved for logs.
    def test_wall_time_preserved_separately_from_monotonic(self):
        det = ActivityDetector(self._config())
        # Monotonic starts at 0; wall clock is some large epoch, offset by +1000.
        wall_offset = 1_700_000_000.0
        last = None
        t = 0.0
        while t < 50.0 - 1e-9:
            rate = 10 if t < 40.0 else 40
            for _ in range(rate):
                ev = det.add_message(t, wall_time=wall_offset + t)
                if ev is not None:
                    last = ev
            t += 1.0
        self.assertIsNotNone(last)
        # Monotonic timestamp stays small; wall_time reflects the epoch we passed.
        self.assertLess(last.timestamp, 60.0)
        self.assertAlmostEqual(last.wall_time, wall_offset + last.timestamp, delta=1.0)

    # 10) Invalid configuration is rejected (prevents divide-by-zero etc.).
    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            ActivityDetectorConfig(window_seconds=0)
        with self.assertRaises(ValueError):
            ActivityDetectorConfig(spike_multiplier=0)
        with self.assertRaises(ValueError):
            ActivityDetectorConfig(min_messages=0)

    # 11) An empty window never raises and reports a zero rate.
    def test_empty_window_is_safe(self):
        det = ActivityDetector(self._config())
        self.assertEqual(det.message_count, 0)
        self.assertEqual(det.current_rate(5.0), 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
