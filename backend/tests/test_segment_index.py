"""Offline unit tests for Stage 5.1 VideoSegment, SegmentIndex, discontinuities, and pins."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.recording import (
    PinCapacityError,
    PinState,
    SegmentIndex,
    SegmentPinnedError,
    VideoSegment,
)


def _seg(
    idx: int,
    *,
    session_id: int = 1,
    wall_closed: float,
    mono_closed: float,
    duration: float = 4.0,
    pts_start: float | None = None,
    size_bytes: int = 1_500_000,
) -> VideoSegment:
    ps = float(idx * 4.0) if pts_start is None else pts_start
    return VideoSegment(
        segment_id=f"s{session_id}_seg_{idx:05d}.ts",
        path=Path(f"/tmp/buffer/s{session_id}_seg_{idx:05d}.ts"),
        session_id=session_id,
        wall_closed=wall_closed,
        mono_closed=mono_closed,
        media_duration=duration,
        ffmpeg_start_pts=ps,
        ffmpeg_end_pts=ps + duration,
        size_bytes=size_bytes,
    )


class TestSegmentIndex(unittest.TestCase):
    def test_video_segment_preserves_raw_timestamps_separately(self):
        seg = _seg(0, wall_closed=1004.2, mono_closed=54.2, duration=4.0, pts_start=0.0)
        self.assertEqual(seg.wall_closed, 1004.2)
        self.assertEqual(seg.mono_closed, 54.2)
        self.assertEqual(seg.media_duration, 4.0)
        self.assertEqual(seg.ffmpeg_start_pts, 0.0)
        self.assertEqual(seg.ffmpeg_end_pts, 4.0)
        self.assertAlmostEqual(seg.approx_start_wall, 1000.2)
        self.assertAlmostEqual(seg.approx_end_wall, 1004.2)
        d = seg.to_dict()
        for key in (
            "wall_closed",
            "mono_closed",
            "media_duration",
            "ffmpeg_start_pts",
            "ffmpeg_end_pts",
            "approx_start_wall",
            "approx_end_wall",
        ):
            self.assertIn(key, d)

    def test_contiguous_segments_and_initial_hls_burst_no_false_discontinuity(self):
        index = SegmentIndex(gap_tolerance_seconds=1.5)
        # seg 0 closes at mono 100.0 (dur 4s)
        self.assertIsNone(index.add_segment(_seg(0, wall_closed=1004.0, mono_closed=100.0)))
        # seg 1 closes only 3.2s later (HLS prefetch catch-up burst observed in Step 5.0)
        self.assertIsNone(index.add_segment(_seg(1, wall_closed=1007.2, mono_closed=103.2)))
        # seg 2 normal steady state (~4.0s later)
        self.assertIsNone(index.add_segment(_seg(2, wall_closed=1011.2, mono_closed=107.2)))
        self.assertEqual(len(index.discontinuities), 0)

    def test_detects_monotonic_gap_session_restart_and_pts_jump(self):
        index = SegmentIndex(gap_tolerance_seconds=1.5, pts_tolerance_seconds=1.5)
        index.add_segment(_seg(0, session_id=1, wall_closed=1004.0, mono_closed=104.0, pts_start=0.0))

        # 1) 12-second monotonic gap (e.g., ad pause where Streamlink paused stdout)
        disc_gap = index.add_segment(
            _seg(1, session_id=1, wall_closed=1020.0, mono_closed=120.0, pts_start=4.0)
        )
        self.assertIsNotNone(disc_gap)
        self.assertEqual(disc_gap.reason, "monotonic_gap")
        self.assertAlmostEqual(disc_gap.mono_gap_seconds, 12.0)

        # 2) Session restart (Streamlink/FFmpeg restarted)
        disc_restart = index.add_segment(
            _seg(0, session_id=2, wall_closed=1026.0, mono_closed=126.0, pts_start=0.0)
        )
        self.assertIsNotNone(disc_restart)
        self.assertEqual(disc_restart.reason, "session_restart")

        # 3) PTS discontinuity within same session
        disc_pts = index.add_segment(
            _seg(1, session_id=2, wall_closed=1030.0, mono_closed=130.0, pts_start=25.0)
        )
        self.assertIsNotNone(disc_pts)
        self.assertEqual(disc_pts.reason, "pts_discontinuity")
        self.assertEqual(len(index.discontinuities), 3)

    def test_query_range_multi_segment_and_mid_segment_trim(self):
        index = SegmentIndex()
        for i in range(6):
            # Approx wall ranges: [1000..1004], [1004..1008], ..., [1020..1024]
            index.add_segment(_seg(i, wall_closed=1004.0 + i * 4.0, mono_closed=104.0 + i * 4.0))

        sel = index.query_range(1005.5, 1017.5)
        self.assertEqual(len(sel.segments), 4)  # seg 1, 2, 3, 4
        self.assertAlmostEqual(sel.approx_trim_start_seconds, 1.5)
        self.assertAlmostEqual(sel.target_duration_seconds, 12.0)
        self.assertTrue(sel.coverage.has_full_preroll)
        self.assertTrue(sel.coverage.has_full_postroll)
        self.assertFalse(sel.coverage.is_partial)
        self.assertEqual(sel.coverage.discontinuities, [])

    def test_query_range_start_end_boundaries_and_partial_coverage(self):
        index = SegmentIndex()
        # Buffer covers [1010..1022]
        for i in range(3):
            index.add_segment(_seg(i, wall_closed=1014.0 + i * 4.0, mono_closed=104.0 + i * 4.0))

        # Request earlier than recording start and later than current end
        sel = index.query_range(1000.0, 1028.0)
        self.assertEqual(len(sel.segments), 3)
        self.assertAlmostEqual(sel.approx_trim_start_seconds, 0.0)
        self.assertAlmostEqual(sel.target_duration_seconds, 12.0)
        self.assertFalse(sel.coverage.has_full_preroll)
        self.assertFalse(sel.coverage.has_full_postroll)
        self.assertTrue(sel.coverage.is_partial)
        self.assertAlmostEqual(sel.coverage.missing_leading_seconds, 10.0)
        self.assertAlmostEqual(sel.coverage.missing_trailing_seconds, 6.0)

    def test_query_range_reports_internal_missing_segment_discontinuity(self):
        index = SegmentIndex(gap_tolerance_seconds=1.5)
        index.add_segment(_seg(0, wall_closed=1004.0, mono_closed=104.0, pts_start=0.0))
        index.add_segment(_seg(1, wall_closed=1008.0, mono_closed=108.0, pts_start=4.0))
        # Skip [1008..1016] (8s gap)
        index.add_segment(_seg(3, wall_closed=1020.0, mono_closed=120.0, pts_start=12.0))
        index.add_segment(_seg(4, wall_closed=1024.0, mono_closed=124.0, pts_start=16.0))

        sel = index.query_range(1002.0, 1022.0)
        self.assertEqual(len(sel.segments), 4)
        self.assertTrue(sel.coverage.is_partial)
        self.assertEqual(len(sel.coverage.discontinuities), 1)
        self.assertEqual(sel.coverage.discontinuities[0].reason, "monotonic_gap")
        # Available concatenated media is 16s - 2s trim = 14s (even though wall span is 20s)
        self.assertAlmostEqual(sel.target_duration_seconds, 14.0)

    def test_pin_protects_segments_and_approved_extracting_never_ttl_expire(self):
        index = SegmentIndex()
        for i in range(4):
            index.add_segment(_seg(i, wall_closed=1004.0 + i * 4.0, mono_closed=104.0 + i * 4.0))

        # Pin covering seg 1 & seg 2 ([1005..1011])
        pin_pending = index.pin_event("ev-pending", 1005.0, 1011.0, now_mono=120.0, ttl_seconds=60.0)
        self.assertTrue(pin_pending.initial_coverage.has_full_preroll)
        self.assertTrue(index.is_segment_pinned(index.segments[1]))
        self.assertFalse(index.is_segment_pinned(index.segments[0]))

        # Removing a pinned segment must fail explicitly (never silently delete!)
        with self.assertRaises(SegmentPinnedError):
            index.remove_segment(index.segments[1].segment_id)

        # Pin another event on seg 2 & 3 and promote it to APPROVED
        index.pin_event("ev-approved", 1009.0, 1015.0, now_mono=120.0, ttl_seconds=60.0)
        index.mark_pin_approved("ev-approved")
        # Pin a third event on seg 0 and promote to EXTRACTING
        index.pin_event("ev-extract", 1000.0, 1003.0, now_mono=120.0, ttl_seconds=60.0)
        index.mark_pin_extracting("ev-extract")

        # Advance past TTL: ONLY PENDING_ANALYSIS expires; APPROVED & EXTRACTING stay protected!
        expired = index.sweep_expired_pins(now_mono=200.0)
        self.assertEqual([p.event_id for p in expired], ["ev-pending"])
        self.assertEqual(index.get_pin("ev-pending").state, PinState.EXPIRED_TTL)
        self.assertEqual(index.get_pin("ev-approved").state, PinState.APPROVED)
        self.assertEqual(index.get_pin("ev-extract").state, PinState.EXTRACTING)

        # seg 1 is now unpinned and eligible for retention cleanup; seg 0, 2, 3 remain pinned
        exp_segs = index.expired_unpinned_segments(now_mono=500.0, retention_seconds=300.0)
        self.assertEqual([s.segment_id for s in exp_segs], [index.segments[1].segment_id])

        # Unpinning releases protection
        index.unpin_event("ev-approved")
        index.unpin_event("ev-extract")
        removed = index.remove_segment(index.segments[0].segment_id)
        self.assertIsNotNone(removed)

    def test_pin_capacity_rejects_new_events_without_evicting_existing(self):
        index = SegmentIndex()
        index.add_segment(_seg(0, wall_closed=1004.0, mono_closed=104.0))

        index.pin_event("ev-1", 1000.0, 1004.0, now_mono=105.0, ttl_seconds=60.0, max_active_pins=2)
        index.mark_pin_approved("ev-1")
        index.pin_event("ev-2", 1000.0, 1004.0, now_mono=106.0, ttl_seconds=60.0, max_active_pins=2)

        with self.assertRaises(PinCapacityError):
            index.pin_event("ev-3", 1000.0, 1004.0, now_mono=107.0, ttl_seconds=60.0, max_active_pins=2)

        # Existing approved & pending pins are untouched
        self.assertEqual(index.get_pin("ev-1").state, PinState.APPROVED)
        self.assertEqual(index.get_pin("ev-2").state, PinState.PENDING_ANALYSIS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
