"""Stage 5.2: Pin-aware rolling buffer & disk-capacity manager.

Responsibilities:
- Enforce rolling monotonic retention (default 300s) for unpinned segments.
- Enforce the hard disk cap (default 500 MB) by deleting oldest unpinned
  segments first, NEVER automatically evicting `APPROVED` or `EXTRACTING` pins.
- Reject new clip/recording jobs cleanly (`BufferCapacityError`) when disk or
  pin capacity is exhausted, preserving active highlights and extractions.
- Signal a clean recording shutdown (`on_emergency_stop` callback + sticky
  `emergency_stop_triggered` flag + explicit `ERROR` log) if the emergency
  disk ceiling (`max_buffer_bytes * emergency_buffer_multiplier`) is reached.
- Handle missing files, filesystem `OSError`s, TTL expiration of abandoned
  `PENDING_ANALYSIS` pins, and concurrent cleanup calls safely & idempotently.

No Streamlink or FFmpeg subprocess logic lives here.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from app.logging_setup import get_logger
from app.recording.models import (
    BufferPin,
    ClipWindowPlan,
    DiscontinuityRecord,
    PinState,
    RecordingConfig,
    SegmentSelection,
    VideoSegment,
)
from app.recording.segment_index import PinCapacityError, SegmentIndex

log = get_logger(__name__)


class BufferCapacityError(RuntimeError):
    """Raised when buffer disk or pin capacity is exhausted for new jobs."""


class BufferCapacityState(str, Enum):
    """Current operational capacity state of the rolling buffer."""

    OK = "ok"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    EMERGENCY_STOP = "emergency_stop"


@dataclass(frozen=True)
class CleanupSummary:
    """Audit summary returned by every `cleanup_once()` pass."""

    expired_pins: list[str] = field(default_factory=list)
    deleted_segments: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    failed_deletions: list[tuple[str, str]] = field(default_factory=list)
    freed_bytes: int = 0
    remaining_bytes: int = 0
    pinned_bytes: int = 0
    capacity_state: BufferCapacityState = BufferCapacityState.OK
    emergency_stop_triggered: bool = False


class RollingBufferManager:
    """Thread-safe, pin-aware disk retention manager over `SegmentIndex`."""

    def __init__(
        self,
        config: RecordingConfig,
        *,
        index: SegmentIndex | None = None,
        mono_clock: Callable[[], float] = time.monotonic,
        unlink_fn: Callable[[Path], None] | None = None,
        on_emergency_stop: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._index = (
            index
            if index is not None
            else SegmentIndex(gap_tolerance_seconds=config.gap_tolerance_seconds)
        )
        self._mono_clock = mono_clock
        self._unlink = unlink_fn if unlink_fn is not None else os.unlink
        self._on_emergency_stop = on_emergency_stop

        self._lock = threading.RLock()
        self._capacity_state = BufferCapacityState.OK
        self._emergency_stop_triggered = False
        self._emergency_reason: str | None = None

        self._cleanup_task: asyncio.Task[None] | None = None
        self._cleanup_running = False

    # -- properties ------------------------------------------------------------

    @property
    def config(self) -> RecordingConfig:
        return self._config

    @property
    def index(self) -> SegmentIndex:
        return self._index

    @property
    def capacity_state(self) -> BufferCapacityState:
        with self._lock:
            return self._capacity_state

    @property
    def emergency_stop_triggered(self) -> bool:
        with self._lock:
            return self._emergency_stop_triggered

    @property
    def emergency_reason(self) -> str | None:
        with self._lock:
            return self._emergency_reason

    @property
    def accepting_new_jobs(self) -> bool:
        """True only when neither emergency stop nor disk/pin exhaustion is active."""
        with self._lock:
            if self._emergency_stop_triggered:
                return False
            if self._capacity_state != BufferCapacityState.OK:
                return False
            if len(self._index.active_pins) >= self._config.max_active_pins:
                return False
            if self._index.total_bytes > self._config.max_buffer_bytes:
                return False
            return True

    # -- periodic async cleanup lifecycle --------------------------------------

    async def start_cleanup_loop(self, *, interval_seconds: float = 5.0) -> None:
        """Start periodic background cleanup on the active asyncio event loop."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be > 0")
        if self._cleanup_running:
            return
        self._cleanup_running = True
        self._cleanup_task = asyncio.create_task(
            self._periodic_cleanup(interval_seconds),
            name="rolling-buffer-cleanup",
        )

    async def stop_cleanup_loop(self) -> None:
        """Stop the periodic background cleanup loop cleanly."""
        self._cleanup_running = False
        task = self._cleanup_task
        self._cleanup_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _periodic_cleanup(self, interval_seconds: float) -> None:
        while self._cleanup_running:
            try:
                await asyncio.sleep(interval_seconds)
                self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  cleanup errors must never crash loop
                log.exception("Unexpected error during periodic buffer cleanup.")

    # -- segment registration & query ------------------------------------------

    def register_segment(
        self, segment: VideoSegment, *, now_mono: float | None = None
    ) -> DiscontinuityRecord | None:
        """Register a newly finalized segment and enforce retention/disk bounds.

        Raises `BufferCapacityError` if emergency shutdown has already been
        signaled for this buffer.
        """
        with self._lock:
            if self._emergency_stop_triggered:
                raise BufferCapacityError(
                    "Recording buffer is in emergency shutdown; rejecting new segment "
                    f"{segment.segment_id!r}."
                )

            disc = self._index.add_segment(segment)
            if disc is not None:
                log.warning(
                    "Recording discontinuity detected (%s): %s -> %s (mono_gap=%.2fs)",
                    disc.reason,
                    disc.prev_segment_id,
                    disc.next_segment_id,
                    disc.mono_gap_seconds,
                )

            # Immediately enforce cap when total bytes exceed max_buffer_bytes.
            if self._index.total_bytes > self._config.max_buffer_bytes:
                self.cleanup_once(now_mono=now_mono)

            return disc

    def query_range(self, start_wall: float, end_wall: float) -> SegmentSelection:
        """Thread-safe range query over the underlying `SegmentIndex`."""
        with self._lock:
            return self._index.query_range(start_wall, end_wall)

    # -- pin lifecycle (protected by capacity & disk rules) --------------------

    def pin_event(
        self,
        event_id: str,
        start_wall: float,
        end_wall: float,
        *,
        plan: ClipWindowPlan | None = None,
        now_mono: float | None = None,
        ttl_seconds: float | None = None,
    ) -> BufferPin:
        """Pin segments for `event_id` if capacity allows; never evicts active pins.

        Raises `BufferCapacityError` when emergency stop is active, disk
        capacity is exhausted by pinned segments, or active pins are at limit.
        """
        ts_mono = self._mono_clock() if now_mono is None else now_mono
        pin_ttl = (
            self._config.pin_max_ttl_seconds if ttl_seconds is None else ttl_seconds
        )

        with self._lock:
            # First sweep abandoned PENDING_ANALYSIS pins (never touching APPROVED/
            # EXTRACTING and never deleting segments the incoming pin needs!).
            for expired in self._index.sweep_expired_pins(ts_mono):
                log.warning(
                    "Expired abandoned PENDING_ANALYSIS pin event_id=%s after %.0fs TTL.",
                    expired.event_id[:8],
                    expired.ttl_seconds,
                )

            existing = self._index.get_pin(event_id)
            if existing is not None and existing.is_active:
                return existing

            if self._emergency_stop_triggered:
                raise BufferCapacityError(
                    f"Emergency disk ceiling active ({self._emergency_reason}); "
                    f"rejecting new clip job event_id={event_id!r}."
                )

            # If currently over disk cap, try trimming oldest unpinned segments
            # that do NOT overlap the incoming pin window before rejecting.
            if self._index.total_bytes > self._config.max_buffer_bytes:
                deleted: list[str] = []
                missing: list[str] = []
                failed: list[tuple[str, str]] = []
                failed_ids: set[str] = set()
                for seg in self._index.unpinned_segments():
                    if self._index.total_bytes <= self._config.max_buffer_bytes:
                        break
                    if seg.approx_end_wall > start_wall and seg.approx_start_wall < end_wall:
                        continue
                    self._delete_unpinned_segment_locked(
                        seg, deleted, missing, failed, failed_ids
                    )

            if self._index.total_bytes > self._config.max_buffer_bytes:
                self._capacity_state = BufferCapacityState.CAPACITY_EXHAUSTED
                raise BufferCapacityError(
                    f"Rolling buffer disk cap exhausted "
                    f"({self._index.total_bytes}/{self._config.max_buffer_bytes} bytes); "
                    f"rejecting event_id={event_id!r} to preserve existing highlights."
                )

            try:
                pin = self._index.pin_event(
                    event_id,
                    start_wall,
                    end_wall,
                    now_mono=ts_mono,
                    ttl_seconds=pin_ttl,
                    plan=plan,
                    max_active_pins=self._config.max_active_pins,
                )
            except PinCapacityError as exc:
                self._capacity_state = BufferCapacityState.CAPACITY_EXHAUSTED
                raise BufferCapacityError(str(exc)) from exc

            cov = pin.initial_coverage
            if cov is not None and (not cov.has_full_preroll or cov.is_partial):
                log.warning(
                    "Pinned event %s with partial coverage "
                    "(segments=%d, missing_leading=%.1fs, missing_trailing=%.1fs, gaps=%d).",
                    event_id[:8],
                    cov.segment_count,
                    cov.missing_leading_seconds,
                    cov.missing_trailing_seconds,
                    len(cov.discontinuities),
                )

            self._refresh_capacity_state_locked()
            return pin

    def mark_pin_approved(self, event_id: str) -> BufferPin | None:
        """Promote a pending pin to `APPROVED` (protected from TTL & disk eviction)."""
        with self._lock:
            return self._index.mark_pin_approved(event_id)

    def mark_pin_extracting(self, event_id: str) -> BufferPin | None:
        """Promote a pin to `EXTRACTING` while FFmpeg is reading its files."""
        with self._lock:
            return self._index.mark_pin_extracting(event_id)

    def unpin_event(
        self, event_id: str, *, cleanup: bool = False, now_mono: float | None = None
    ) -> BufferPin | None:
        """Release a pin so its segments return to normal rolling cleanup."""
        with self._lock:
            pin = self._index.unpin_event(event_id)
            if cleanup:
                self.cleanup_once(now_mono=now_mono)
            else:
                self._refresh_capacity_state_locked()
            return pin

    def get_pin(self, event_id: str) -> BufferPin | None:
        with self._lock:
            return self._index.get_pin(event_id)

    # -- core cleanup & disk-cap enforcement -----------------------------------

    def cleanup_once(self, *, now_mono: float | None = None) -> CleanupSummary:
        """Execute one idempotent retention + disk-cap cleanup pass.

        Policy enforced strictly in order:
        1. Expire abandoned `PENDING_ANALYSIS` pins past TTL (`APPROVED` and
           `EXTRACTING` pins are NEVER TTL-expired).
        2. Delete unpinned segments older than `buffer_seconds` (monotonic).
        3. If total bytes still exceed `max_buffer_bytes`, delete oldest
           unpinned segments first. NEVER delete pinned segments or evict
           `APPROVED`/`EXTRACTING` pins.
        4. Update capacity state; if `total_bytes >= emergency_buffer_bytes`,
           signal a clean recording shutdown and log an explicit error.
        """
        ts_mono = self._mono_clock() if now_mono is None else now_mono

        with self._lock:
            expired_pins_objs = self._index.sweep_expired_pins(ts_mono)
            expired_pin_ids = [p.event_id for p in expired_pins_objs]
            for p in expired_pins_objs:
                log.warning(
                    "Expired abandoned PENDING_ANALYSIS pin event_id=%s after %.0fs TTL.",
                    p.event_id[:8],
                    p.ttl_seconds,
                )

            deleted: list[str] = []
            missing: list[str] = []
            failed: list[tuple[str, str]] = []
            failed_ids: set[str] = set()
            freed_bytes = 0

            # Pass 1: Normal monotonic rolling retention (unpinned only).
            for seg in self._index.expired_unpinned_segments(
                ts_mono, self._config.buffer_seconds
            ):
                freed = self._delete_unpinned_segment_locked(
                    seg, deleted, missing, failed, failed_ids
                )
                freed_bytes += freed

            # Pass 2: Hard disk cap (delete oldest unpinned segments first).
            if self._index.total_bytes > self._config.max_buffer_bytes:
                for seg in self._index.unpinned_segments():
                    if self._index.total_bytes <= self._config.max_buffer_bytes:
                        break
                    if seg.segment_id in failed_ids:
                        continue
                    freed = self._delete_unpinned_segment_locked(
                        seg, deleted, missing, failed, failed_ids
                    )
                    freed_bytes += freed
                if deleted:
                    log.warning(
                        "Rolling buffer trimmed %d unpinned segment(s) to respect "
                        "%.1f MB disk cap (remaining=%.1f MB, pinned=%.1f MB).",
                        len(deleted),
                        self._config.max_buffer_mb,
                        self._index.total_bytes / (1024 * 1024),
                        self._index.pinned_bytes / (1024 * 1024),
                    )

            # Pass 3: Capacity & emergency disk-ceiling evaluation.
            self._refresh_capacity_state_locked()

            return CleanupSummary(
                expired_pins=expired_pin_ids,
                deleted_segments=deleted,
                missing_files=missing,
                failed_deletions=failed,
                freed_bytes=freed_bytes,
                remaining_bytes=self._index.total_bytes,
                pinned_bytes=self._index.pinned_bytes,
                capacity_state=self._capacity_state,
                emergency_stop_triggered=self._emergency_stop_triggered,
            )

    def purge_unpinned_files(self) -> CleanupSummary:
        """Delete all unpinned segments (e.g., on shutdown) while preserving active pins."""
        with self._lock:
            deleted: list[str] = []
            missing: list[str] = []
            failed: list[tuple[str, str]] = []
            failed_ids: set[str] = set()
            freed_bytes = 0

            for seg in self._index.unpinned_segments():
                freed_bytes += self._delete_unpinned_segment_locked(
                    seg, deleted, missing, failed, failed_ids
                )

            self._refresh_capacity_state_locked()
            return CleanupSummary(
                deleted_segments=deleted,
                missing_files=missing,
                failed_deletions=failed,
                freed_bytes=freed_bytes,
                remaining_bytes=self._index.total_bytes,
                pinned_bytes=self._index.pinned_bytes,
                capacity_state=self._capacity_state,
                emergency_stop_triggered=self._emergency_stop_triggered,
            )

    def trim_oldest_unpinned_segments(self, target_bytes_to_free: int) -> int:
        """Delete oldest unpinned segments until at least `target_bytes_to_free` is reclaimed.

        Never deletes pinned segments or evicts APPROVED/EXTRACTING pins.
        Returns total bytes freed.
        """
        if target_bytes_to_free <= 0:
            return 0
        with self._lock:
            deleted: list[str] = []
            missing: list[str] = []
            failed: list[tuple[str, str]] = []
            failed_ids: set[str] = set()
            freed_bytes = 0

            for seg in self._index.unpinned_segments():
                if freed_bytes >= target_bytes_to_free:
                    break
                if seg.segment_id in failed_ids:
                    continue
                freed_bytes += self._delete_unpinned_segment_locked(
                    seg, deleted, missing, failed, failed_ids
                )

            self._refresh_capacity_state_locked()
            return freed_bytes

    # -- internal helpers (caller holds self._lock) ----------------------------

    def _delete_unpinned_segment_locked(
        self,
        seg: VideoSegment,
        deleted: list[str],
        missing: list[str],
        failed: list[tuple[str, str]],
        failed_ids: set[str],
    ) -> int:
        # Defense in depth: re-check pin status before touching the filesystem.
        if self._index.is_segment_pinned(seg):
            return 0

        try:
            self._unlink(seg.path)
            self._index.remove_segment(seg.segment_id)
            deleted.append(seg.segment_id)
            return seg.size_bytes
        except FileNotFoundError:
            # File already missing on disk: reconcile index cleanly so cleanup is idempotent.
            self._index.remove_segment(seg.segment_id)
            missing.append(seg.segment_id)
            log.warning(
                "Segment file already missing on disk during cleanup: %s",
                seg.path.name,
            )
            return seg.size_bytes
        except OSError as exc:
            # Keep segment in index so byte accounting remains accurate and next pass retries.
            failed.append((seg.segment_id, f"{type(exc).__name__}: {exc}"))
            failed_ids.add(seg.segment_id)
            log.error(
                "Failed to delete rolling segment %s (will retry next cleanup): %s",
                seg.path.name,
                exc,
            )
            return 0

    def _refresh_capacity_state_locked(self) -> None:
        total = self._index.total_bytes
        pinned = self._index.pinned_bytes
        max_bytes = self._config.max_buffer_bytes
        emergency_bytes = self._config.emergency_buffer_bytes

        if self._emergency_stop_triggered:
            self._capacity_state = BufferCapacityState.EMERGENCY_STOP
            return

        if total >= emergency_bytes:
            reason = (
                f"Emergency disk ceiling reached: {total / (1024 * 1024):.1f} MB >= "
                f"{emergency_bytes / (1024 * 1024):.1f} MB "
                f"(pinned={pinned / (1024 * 1024):.1f} MB, "
                f"protected_pins={len(self._index.protected_pins)}). "
                "Stopping recording cleanly without sacrificing approved highlights."
            )
            self._emergency_stop_triggered = True
            self._emergency_reason = reason
            self._capacity_state = BufferCapacityState.EMERGENCY_STOP
            log.error("BUFFER EMERGENCY SHUTDOWN: %s", reason)
            if self._on_emergency_stop is not None:
                try:
                    self._on_emergency_stop(reason)
                except Exception:  # noqa: BLE001  callback faults must not corrupt buffer state
                    log.exception("Emergency stop callback raised.")
            return

        if (
            total > max_bytes
            or len(self._index.active_pins) >= self._config.max_active_pins
        ):
            if self._capacity_state != BufferCapacityState.CAPACITY_EXHAUSTED:
                log.warning(
                    "Rolling buffer capacity exhausted (total=%.1f MB / %.1f MB, "
                    "active_pins=%d/%d); rejecting new jobs while preserving active highlights.",
                    total / (1024 * 1024),
                    self._config.max_buffer_mb,
                    len(self._index.active_pins),
                    self._config.max_active_pins,
                )
            self._capacity_state = BufferCapacityState.CAPACITY_EXHAUSTED
            return

        self._capacity_state = BufferCapacityState.OK
