"""Offline unit tests for Stage 5.2 RollingBufferManager."""

from __future__ import annotations

import asyncio
import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from app.recording import (
    BufferCapacityError,
    BufferCapacityState,
    PinState,
    RecordingConfig,
    RollingBufferManager,
    VideoSegment,
)

MB = 1024 * 1024


def _create_seg_file(
    root: Path,
    idx: int,
    *,
    wall_closed: float,
    mono_closed: float,
    duration: float = 4.0,
    size_bytes: int = 1024,
    session_id: int = 1,
    create_on_disk: bool = True,
) -> VideoSegment:
    seg_id = f"s{session_id}_seg_{idx:05d}.ts"
    path = root / seg_id
    if create_on_disk:
        path.write_bytes(b"x" * min(size_bytes, 64))  # small real file; metadata tracks logical size
    return VideoSegment(
        segment_id=seg_id,
        path=path,
        session_id=session_id,
        wall_closed=wall_closed,
        mono_closed=mono_closed,
        media_duration=duration,
        ffmpeg_start_pts=idx * duration,
        ffmpeg_end_pts=(idx + 1) * duration,
        size_bytes=size_bytes,
    )


class TestRollingBufferManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="liveclipper_buf_test_")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_normal_rolling_retention_uses_monotonic_clock_and_is_idempotent(self):
        cfg = RecordingConfig(buffer_seconds=300.0, max_buffer_mb=500.0, buffer_dir=self.root)
        now = 500.0
        mgr = RollingBufferManager(cfg, mono_clock=lambda: now)

        # seg 0 (mono_closed=100) and seg 1 (mono_closed=199) are older than 500 - 300 = 200
        # seg 2 (mono_closed=205) and seg 3 (mono_closed=496) are within retention
        s0 = _create_seg_file(self.root, 0, wall_closed=1100.0, mono_closed=100.0, size_bytes=2000)
        s1 = _create_seg_file(self.root, 1, wall_closed=1199.0, mono_closed=199.0, size_bytes=2000)
        s2 = _create_seg_file(self.root, 2, wall_closed=1205.0, mono_closed=205.0, size_bytes=2000)
        s3 = _create_seg_file(self.root, 3, wall_closed=1496.0, mono_closed=496.0, size_bytes=2000)
        for s in (s0, s1, s2, s3):
            mgr.register_segment(s)

        summary = mgr.cleanup_once()
        self.assertEqual(summary.deleted_segments, [s0.segment_id, s1.segment_id])
        self.assertEqual(summary.freed_bytes, 4000)
        self.assertFalse(s0.path.exists())
        self.assertFalse(s1.path.exists())
        self.assertTrue(s2.path.exists())
        self.assertTrue(s3.path.exists())
        self.assertEqual(summary.capacity_state, BufferCapacityState.OK)

        # Second cleanup pass at same timestamp is a clean no-op (idempotent)
        again = mgr.cleanup_once()
        self.assertEqual(again.deleted_segments, [])
        self.assertEqual(again.freed_bytes, 0)
        self.assertEqual(len(mgr.index.segments), 2)

    def test_pinned_segments_protected_from_rolling_retention_and_purge(self):
        cfg = RecordingConfig(buffer_seconds=300.0, max_buffer_mb=500.0, buffer_dir=self.root)
        now = 100.0
        mgr = RollingBufferManager(cfg, mono_clock=lambda: now)

        s0 = _create_seg_file(self.root, 0, wall_closed=1004.0, mono_closed=100.0, size_bytes=1000)
        s1 = _create_seg_file(self.root, 1, wall_closed=1008.0, mono_closed=104.0, size_bytes=1000)
        s2 = _create_seg_file(self.root, 2, wall_closed=1012.0, mono_closed=108.0, size_bytes=1000)
        for s in (s0, s1, s2):
            mgr.register_segment(s)

        # Pin s1 ([1005..1007]) and approve it
        mgr.pin_event("ev-keep", 1005.0, 1007.0, now_mono=110.0)
        mgr.mark_pin_approved("ev-keep")

        # Jump well past 300s retention AND well past 180s TTL
        now = 1000.0
        summary = mgr.cleanup_once()
        self.assertEqual(summary.deleted_segments, [s0.segment_id, s2.segment_id])
        self.assertTrue(s1.path.exists())
        self.assertEqual([s.segment_id for s in mgr.index.segments], [s1.segment_id])

        # Even purge_unpinned_files must preserve the approved pin's file
        purge = mgr.purge_unpinned_files()
        self.assertEqual(purge.deleted_segments, [])
        self.assertTrue(s1.path.exists())

        # Once unpinned, cleanup deletes s1 cleanly
        mgr.unpin_event("ev-keep", cleanup=True)
        self.assertFalse(s1.path.exists())
        self.assertEqual(mgr.index.segments, [])

    def test_disk_cap_deletes_unpinned_first_even_within_retention_window(self):
        # 10 MB hard cap; each segment is 4 MB
        cfg = RecordingConfig(buffer_seconds=300.0, max_buffer_mb=10.0, buffer_dir=self.root)
        now = 120.0
        mgr = RollingBufferManager(cfg, mono_clock=lambda: now)

        s0 = _create_seg_file(self.root, 0, wall_closed=1004.0, mono_closed=104.0, size_bytes=4 * MB)
        s1 = _create_seg_file(self.root, 1, wall_closed=1008.0, mono_closed=108.0, size_bytes=4 * MB)
        mgr.register_segment(s0)
        mgr.register_segment(s1)

        # Registering s2 pushes total to 12 MB > 10 MB cap -> auto-trims oldest unpinned (s0)
        s2 = _create_seg_file(self.root, 2, wall_closed=1012.0, mono_closed=112.0, size_bytes=4 * MB)
        mgr.register_segment(s2)

        self.assertFalse(s0.path.exists())
        self.assertTrue(s1.path.exists())
        self.assertTrue(s2.path.exists())
        self.assertEqual(mgr.index.total_bytes, 8 * MB)
        self.assertEqual(mgr.capacity_state, BufferCapacityState.OK)

    def test_capacity_exhaustion_rejects_new_jobs_and_preserves_approved_and_extracting(self):
        # 10 MB hard cap, 12.5 MB emergency ceiling (1.25x)
        cfg = RecordingConfig(
            buffer_seconds=300.0,
            max_buffer_mb=10.0,
            emergency_buffer_multiplier=1.25,
            buffer_dir=self.root,
        )
        now = 120.0
        mgr = RollingBufferManager(cfg, mono_clock=lambda: now)

        s0 = _create_seg_file(self.root, 0, wall_closed=1004.0, mono_closed=104.0, size_bytes=6 * MB)
        s1 = _create_seg_file(self.root, 1, wall_closed=1008.0, mono_closed=108.0, size_bytes=5 * MB)
        mgr.register_segment(s0)
        mgr.pin_event("ev-approved", 1000.0, 1004.0, now_mono=105.0)
        mgr.mark_pin_approved("ev-approved")

        # Adding s1 (5 MB) pushes total to 11 MB (> 10 MB cap, < 12.5 MB emergency).
        # s1 is unpinned so cleanup trims s1 to stay under 10 MB; instead let's pin both before exceeding cap:
        mgr.register_segment(s1)
        self.assertFalse(s1.path.exists())  # s1 trimmed because unpinned

        # Now test pinned-only exhaustion: 2 segments of 5.5 MB each, both pinned before total > 10 MB
        s2 = _create_seg_file(self.root, 2, wall_closed=1012.0, mono_closed=112.0, size_bytes=4 * MB)
        mgr.register_segment(s2)  # total = 10 MB (exact cap)
        mgr.pin_event("ev-extract", 1008.0, 1012.0, now_mono=113.0)
        mgr.mark_pin_extracting("ev-extract")

        # Add a 1 MB segment pinned on the same window (total 11 MB > 10 MB cap, < 12.5 MB emergency)
        s3 = _create_seg_file(self.root, 3, wall_closed=1012.0, mono_closed=114.0, size_bytes=1 * MB)
        mgr.register_segment(s3)

        self.assertEqual(mgr.capacity_state, BufferCapacityState.CAPACITY_EXHAUSTED)
        self.assertFalse(mgr.accepting_new_jobs)
        self.assertFalse(mgr.emergency_stop_triggered)

        # New clip pin jobs MUST be rejected while approved & extracting pins stay untouched!
        with self.assertRaises(BufferCapacityError):
            mgr.pin_event("ev-new", 1000.0, 1004.0, now_mono=115.0)

        self.assertEqual(mgr.get_pin("ev-approved").state, PinState.APPROVED)
        self.assertEqual(mgr.get_pin("ev-extract").state, PinState.EXTRACTING)
        self.assertTrue(s0.path.exists())
        self.assertTrue(s2.path.exists())

    def test_emergency_disk_ceiling_signals_clean_shutdown_without_deleting_approved_pins(self):
        # 10 MB cap, 12.5 MB emergency ceiling
        cfg = RecordingConfig(
            buffer_seconds=300.0,
            max_buffer_mb=10.0,
            emergency_buffer_multiplier=1.25,
            buffer_dir=self.root,
        )
        now = 120.0
        emergency_reasons: list[str] = []
        mgr = RollingBufferManager(
            cfg,
            mono_clock=lambda: now,
            on_emergency_stop=emergency_reasons.append,
        )

        # Pin a window [1000..1020] as APPROVED, then fill it past 12.5 MB emergency ceiling
        s0 = _create_seg_file(self.root, 0, wall_closed=1004.0, mono_closed=104.0, size_bytes=6 * MB)
        mgr.register_segment(s0)
        mgr.pin_event("ev-approved", 1000.0, 1020.0, now_mono=105.0)
        mgr.mark_pin_approved("ev-approved")

        s1 = _create_seg_file(self.root, 1, wall_closed=1008.0, mono_closed=108.0, size_bytes=7 * MB)
        mgr.register_segment(s1)  # total = 13 MB >= 12.5 MB emergency ceiling, all pinned!

        self.assertTrue(mgr.emergency_stop_triggered)
        self.assertEqual(mgr.capacity_state, BufferCapacityState.EMERGENCY_STOP)
        self.assertFalse(mgr.accepting_new_jobs)
        self.assertEqual(len(emergency_reasons), 1)
        self.assertIn("Emergency disk ceiling reached", emergency_reasons[0])

        # CRITICAL: Approved highlight files were NEVER deleted!
        self.assertTrue(s0.path.exists())
        self.assertTrue(s1.path.exists())
        self.assertEqual(mgr.get_pin("ev-approved").state, PinState.APPROVED)

        # Subsequent cleanup does NOT re-fire callback and still refuses new segments/pins
        mgr.cleanup_once()
        self.assertEqual(len(emergency_reasons), 1)
        with self.assertRaises(BufferCapacityError):
            mgr.register_segment(
                _create_seg_file(self.root, 2, wall_closed=1012.0, mono_closed=112.0, size_bytes=1 * MB)
            )
        with self.assertRaises(BufferCapacityError):
            mgr.pin_event("ev-rejected", 1000.0, 1004.0, now_mono=115.0)

    def test_ttl_expires_pending_analysis_only_never_approved_or_extracting(self):
        cfg = RecordingConfig(
            buffer_seconds=60.0,
            pin_max_ttl_seconds=30.0,
            max_buffer_mb=500.0,
            buffer_dir=self.root,
        )
        now = 100.0
        mgr = RollingBufferManager(cfg, mono_clock=lambda: now)

        s0 = _create_seg_file(self.root, 0, wall_closed=1004.0, mono_closed=100.0, size_bytes=1000)
        s1 = _create_seg_file(self.root, 1, wall_closed=1008.0, mono_closed=100.0, size_bytes=1000)
        s2 = _create_seg_file(self.root, 2, wall_closed=1012.0, mono_closed=100.0, size_bytes=1000)
        for s in (s0, s1, s2):
            mgr.register_segment(s)

        mgr.pin_event("ev-pending", 1000.0, 1003.0, now_mono=105.0)
        mgr.pin_event("ev-approved", 1005.0, 1007.0, now_mono=105.0)
        mgr.mark_pin_approved("ev-approved")
        mgr.pin_event("ev-extract", 1009.0, 1011.0, now_mono=105.0)
        mgr.mark_pin_extracting("ev-extract")

        # Advance past both TTL (30s) and retention (60s)
        now = 250.0
        summary = mgr.cleanup_once()
        self.assertEqual(summary.expired_pins, ["ev-pending"])
        self.assertEqual(summary.deleted_segments, [s0.segment_id])
        self.assertFalse(s0.path.exists())
        self.assertTrue(s1.path.exists())
        self.assertTrue(s2.path.exists())
        self.assertEqual(mgr.get_pin("ev-pending").state, PinState.EXPIRED_TTL)
        self.assertEqual(mgr.get_pin("ev-approved").state, PinState.APPROVED)
        self.assertEqual(mgr.get_pin("ev-extract").state, PinState.EXTRACTING)

    def test_missing_segment_files_reconciled_cleanly(self):
        cfg = RecordingConfig(buffer_seconds=30.0, max_buffer_mb=500.0, buffer_dir=self.root)
        now = 100.0
        mgr = RollingBufferManager(cfg, mono_clock=lambda: now)

        # File missing on disk before cleanup runs
        s0 = _create_seg_file(
            self.root, 0, wall_closed=1004.0, mono_closed=50.0, size_bytes=4000, create_on_disk=False
        )
        mgr.register_segment(s0)

        summary = mgr.cleanup_once()
        self.assertEqual(summary.missing_files, [s0.segment_id])
        self.assertEqual(summary.deleted_segments, [])
        self.assertEqual(summary.failed_deletions, [])
        self.assertEqual(mgr.index.segments, [])

        # Repeating cleanup is a no-op
        again = mgr.cleanup_once()
        self.assertEqual(again.missing_files, [])

    def test_filesystem_error_handled_safely_and_retried_later(self):
        cfg = RecordingConfig(buffer_seconds=30.0, max_buffer_mb=500.0, buffer_dir=self.root)
        now = 100.0
        fail_once = {"should_fail": True}

        def flaky_unlink(path: Path) -> None:
            if fail_once["should_fail"]:
                raise PermissionError("busy file")
            path.unlink()

        mgr = RollingBufferManager(cfg, mono_clock=lambda: now, unlink_fn=flaky_unlink)
        s0 = _create_seg_file(self.root, 0, wall_closed=1004.0, mono_closed=50.0, size_bytes=4000)
        mgr.register_segment(s0)

        # First pass hits PermissionError: does not crash, retains index entry for honest accounting
        first = mgr.cleanup_once()
        self.assertEqual(len(first.failed_deletions), 1)
        self.assertEqual(first.failed_deletions[0][0], s0.segment_id)
        self.assertEqual(len(mgr.index.segments), 1)
        self.assertTrue(s0.path.exists())

        # Once transient FS error clears, next cleanup succeeds cleanly
        fail_once["should_fail"] = False
        second = mgr.cleanup_once()
        self.assertEqual(second.failed_deletions, [])
        self.assertEqual(second.deleted_segments, [s0.segment_id])
        self.assertFalse(s0.path.exists())
        self.assertEqual(mgr.index.segments, [])

    def test_concurrent_cleanup_and_mutations_preserve_index_integrity(self):
        cfg = RecordingConfig(buffer_seconds=20.0, max_buffer_mb=500.0, buffer_dir=self.root)
        mgr = RollingBufferManager(cfg, mono_clock=lambda: 200.0)

        # Seed 30 old segments; pin segments 10..15 as EXTRACTING
        segs = [
            _create_seg_file(
                self.root, i, wall_closed=1004.0 + i * 4.0, mono_closed=10.0 + i * 2.0, size_bytes=1000
            )
            for i in range(30)
        ]
        for s in segs:
            mgr.register_segment(s)
        mgr.pin_event("ev-hot", 1041.0, 1063.0, now_mono=190.0)
        mgr.mark_pin_extracting("ev-hot")

        # Hammer cleanup_once and query_range concurrently from 8 threads
        def _worker(_: int) -> None:
            for _ in range(10):
                mgr.cleanup_once()
                sel = mgr.query_range(1041.0, 1063.0)
                assert sel.coverage.segment_count >= 5

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_worker, range(8)))

        # Every pinned segment file must still exist; all unpinned old files must be gone
        remaining_ids = {s.segment_id for s in mgr.index.segments}
        for i in range(10, 16):
            self.assertIn(segs[i].segment_id, remaining_ids)
            self.assertTrue(segs[i].path.exists())
        for i in range(0, 10):
            self.assertNotIn(segs[i].segment_id, remaining_ids)
            self.assertFalse(segs[i].path.exists())


class TestRollingBufferManagerAsyncLoop(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_async_cleanup_loop_starts_runs_and_stops(self):
        with tempfile.TemporaryDirectory(prefix="liveclipper_buf_async_") as tmp:
            root = Path(tmp)
            cfg = RecordingConfig(buffer_seconds=10.0, max_buffer_mb=500.0, buffer_dir=root)
            mgr = RollingBufferManager(cfg, mono_clock=lambda: 200.0)
            old_seg = _create_seg_file(root, 0, wall_closed=1004.0, mono_closed=50.0, size_bytes=1000)
            mgr.register_segment(old_seg)

            await mgr.start_cleanup_loop(interval_seconds=0.02)
            try:
                await asyncio.sleep(0.08)
            finally:
                await mgr.stop_cleanup_loop()

            self.assertFalse(old_seg.path.exists())
            self.assertEqual(mgr.index.segments, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
