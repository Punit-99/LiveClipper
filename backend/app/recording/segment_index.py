"""Pure in-memory index of rolling video segments, discontinuities, and pins.

Design guarantees:
- Does NOT assume segments are contiguous: detects session restarts, monotonic
  gaps, and FFmpeg PTS discontinuities explicitly between consecutive segments.
- Stores raw per-segment timing observations (`wall_closed`, `mono_closed`,
  `media_duration`, `ffmpeg_start_pts`, `ffmpeg_end_pts`) separately; wall-clock
  window queries are explicitly approximate.
- Enforces pin safety at the data-structure boundary:
  * `pin_event()` checks pre-roll/post-roll coverage immediately and reports
    missing/partial coverage honestly.
  * `remove_segment()` refuses to remove any segment intersecting an active pin.
  * `sweep_expired_pins()` expires ONLY `PENDING_ANALYSIS` pins past TTL, never
    `APPROVED` or `EXTRACTING` pins.
  * Enforces `max_active_pins` by rejecting new pins with `PinCapacityError`
    rather than silently sacrificing an approved highlight.
"""

from __future__ import annotations

from app.recording.models import (
    BufferPin,
    ClipWindowPlan,
    DiscontinuityRecord,
    PinCoverageReport,
    PinState,
    SegmentSelection,
    VideoSegment,
)

# Boundary tolerance (seconds) when checking whether pre-roll / post-roll is covered.
_COVERAGE_EPSILON = 0.25


def _intervals_overlap(
    seg_start: float, seg_end: float, req_start: float, req_end: float
) -> bool:
    """Return True when segment `[seg_start, seg_end]` overlaps `[req_start, req_end]`.

    For positive-length windows we require strictly positive overlap so adjacent
    contiguous segments touching at a single boundary point (e.g. `[0, 4]` vs
    `[4, 8]` for a `[0, 4]` request) are not falsely included or pinned.
    """
    if req_end > req_start:
        return seg_end > req_start and seg_start < req_end
    return seg_start <= req_start <= seg_end


class PinCapacityError(RuntimeError):
    """Raised when active pins are at capacity and new pins must be rejected."""


class SegmentPinnedError(RuntimeError):
    """Raised when code attempts to remove a segment protected by an active pin."""


class SegmentIndex:
    """In-memory timeline of rolling buffer segments, gaps, and active pins."""

    def __init__(
        self,
        *,
        gap_tolerance_seconds: float = 1.5,
        pts_tolerance_seconds: float = 1.5,
    ) -> None:
        if gap_tolerance_seconds < 0:
            raise ValueError("gap_tolerance_seconds must be >= 0")
        if pts_tolerance_seconds < 0:
            raise ValueError("pts_tolerance_seconds must be >= 0")

        self._gap_tolerance = gap_tolerance_seconds
        self._pts_tolerance = pts_tolerance_seconds
        self._segments: list[VideoSegment] = []
        self._segment_ids: set[str] = set()
        self._discontinuities: list[DiscontinuityRecord] = []
        self._pins: dict[str, BufferPin] = {}

    # -- segment management ----------------------------------------------------

    def add_segment(self, segment: VideoSegment) -> DiscontinuityRecord | None:
        """Append a finalized segment and return any detected discontinuity."""
        if segment.segment_id in self._segment_ids:
            raise ValueError(f"Duplicate segment_id: {segment.segment_id!r}")

        disc: DiscontinuityRecord | None = None
        if self._segments:
            prev = self._segments[-1]
            disc = self._detect_discontinuity(prev, segment)
            if disc is not None:
                self._discontinuities.append(disc)

        self._segments.append(segment)
        self._segment_ids.add(segment.segment_id)
        return disc

    def remove_segment(self, segment_id: str) -> VideoSegment | None:
        """Remove an unpinned segment from the index.

        Raises `SegmentPinnedError` if the segment intersects any active pin.
        """
        for idx, seg in enumerate(self._segments):
            if seg.segment_id == segment_id:
                if self.is_segment_pinned(seg):
                    raise SegmentPinnedError(
                        f"Refusing to remove pinned segment {segment_id!r}."
                    )
                removed = self._segments.pop(idx)
                self._segment_ids.discard(segment_id)
                self._prune_stale_discontinuities()
                return removed
        return None

    @property
    def segments(self) -> list[VideoSegment]:
        return list(self._segments)

    @property
    def discontinuities(self) -> list[DiscontinuityRecord]:
        return list(self._discontinuities)

    @property
    def total_bytes(self) -> int:
        return sum(s.size_bytes for s in self._segments)

    @property
    def pinned_bytes(self) -> int:
        return sum(s.size_bytes for s in self._segments if self.is_segment_pinned(s))

    @property
    def unpinned_bytes(self) -> int:
        return sum(s.size_bytes for s in self._segments if not self.is_segment_pinned(s))

    # -- pin lifecycle ---------------------------------------------------------

    def pin_event(
        self,
        event_id: str,
        start_wall: float,
        end_wall: float,
        *,
        now_mono: float,
        ttl_seconds: float,
        plan: ClipWindowPlan | None = None,
        max_active_pins: int | None = None,
    ) -> BufferPin:
        """Reserve segments intersecting `[start_wall, end_wall]` for `event_id`.

        Checks current segment coverage immediately and attaches an honest
        `PinCoverageReport` to the returned pin.
        """
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if end_wall < start_wall:
            raise ValueError("end_wall must be >= start_wall")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")

        existing = self._pins.get(event_id)
        if existing is not None and existing.is_active:
            return existing

        if max_active_pins is not None and len(self.active_pins) >= max_active_pins:
            raise PinCapacityError(
                f"Active pin capacity exhausted ({len(self.active_pins)}/{max_active_pins}); "
                f"rejecting event_id={event_id!r} to protect existing highlights."
            )

        selection = self.query_range(start_wall, end_wall)
        pin = BufferPin(
            event_id=event_id,
            requested_start_wall=start_wall,
            requested_end_wall=end_wall,
            created_mono=now_mono,
            ttl_seconds=ttl_seconds,
            state=PinState.PENDING_ANALYSIS,
            plan=plan,
            initial_coverage=selection.coverage,
        )
        self._pins[event_id] = pin
        return pin

    def get_pin(self, event_id: str) -> BufferPin | None:
        return self._pins.get(event_id)

    def mark_pin_approved(self, event_id: str) -> BufferPin | None:
        """Promote a pending pin to `APPROVED` (protected from TTL/disk eviction)."""
        pin = self._pins.get(event_id)
        if pin is None or not pin.is_active:
            return None
        if pin.state == PinState.PENDING_ANALYSIS:
            pin.state = PinState.APPROVED
        return pin

    def mark_pin_extracting(self, event_id: str) -> BufferPin | None:
        """Promote a pin to `EXTRACTING` while FFmpeg is reading its segments."""
        pin = self._pins.get(event_id)
        if pin is None or not pin.is_active:
            return None
        pin.state = PinState.EXTRACTING
        return pin

    def unpin_event(self, event_id: str) -> BufferPin | None:
        """Release a pin so its segments become eligible for normal expiration."""
        pin = self._pins.get(event_id)
        if pin is None:
            return None
        pin.state = PinState.RELEASED
        return pin

    def sweep_expired_pins(self, now_mono: float) -> list[BufferPin]:
        """Expire `PENDING_ANALYSIS` pins past TTL. Never touches APPROVED/EXTRACTING."""
        expired: list[BufferPin] = []
        for pin in self._pins.values():
            if pin.is_ttl_expired(now_mono):
                pin.state = PinState.EXPIRED_TTL
                expired.append(pin)
        return expired

    @property
    def active_pins(self) -> list[BufferPin]:
        return [p for p in self._pins.values() if p.is_active]

    @property
    def protected_pins(self) -> list[BufferPin]:
        return [p for p in self._pins.values() if p.is_protected_from_eviction]

    def is_segment_pinned(self, segment: VideoSegment) -> bool:
        """Return True if `segment` intersects any currently active pin."""
        seg_start = segment.approx_start_wall
        seg_end = segment.approx_end_wall
        for pin in self._pins.values():
            if not pin.is_active:
                continue
            if _intervals_overlap(
                seg_start, seg_end, pin.requested_start_wall, pin.requested_end_wall
            ):
                return True
        return False

    def unpinned_segments(self) -> list[VideoSegment]:
        """Return all unpinned segments in oldest-first order."""
        return [s for s in self._segments if not self.is_segment_pinned(s)]

    def expired_unpinned_segments(
        self, now_mono: float, retention_seconds: float
    ) -> list[VideoSegment]:
        """Return unpinned segments whose monotonic close time is older than retention."""
        cutoff = now_mono - retention_seconds
        return [
            s
            for s in self._segments
            if s.mono_closed < cutoff and not self.is_segment_pinned(s)
        ]

    # -- window query & coverage -----------------------------------------------

    def query_range(self, start_wall: float, end_wall: float) -> SegmentSelection:
        """Select segments intersecting `[start_wall, end_wall]` with coverage report."""
        if end_wall < start_wall:
            raise ValueError("end_wall must be >= start_wall")

        requested_duration = max(0.0, end_wall - start_wall)
        selected = [
            s
            for s in self._segments
            if _intervals_overlap(s.approx_start_wall, s.approx_end_wall, start_wall, end_wall)
        ]

        if not selected:
            coverage = PinCoverageReport(
                requested_start_wall=start_wall,
                requested_end_wall=end_wall,
                has_full_preroll=False,
                has_full_postroll=False,
                is_partial=True,
                segment_count=0,
                actual_approx_start_wall=None,
                actual_approx_end_wall=None,
                missing_leading_seconds=requested_duration,
                missing_trailing_seconds=0.0,
                total_media_duration=0.0,
                discontinuities=[],
            )
            return SegmentSelection(
                segments=[],
                approx_trim_start_seconds=0.0,
                target_duration_seconds=0.0,
                coverage=coverage,
            )

        first = selected[0]
        last = selected[-1]
        actual_start = first.approx_start_wall
        actual_end = last.approx_end_wall

        missing_leading = max(0.0, actual_start - start_wall)
        missing_trailing = max(0.0, end_wall - actual_end)
        has_full_preroll = missing_leading <= _COVERAGE_EPSILON
        has_full_postroll = missing_trailing <= _COVERAGE_EPSILON

        selected_ids = {s.segment_id for s in selected}
        internal_discs = [
            d
            for d in self._discontinuities
            if d.prev_segment_id in selected_ids and d.next_segment_id in selected_ids
        ]

        total_media = sum(s.media_duration for s in selected)
        approx_trim_start = max(0.0, start_wall - actual_start)
        # Clamp trim_start strictly inside the first segment's media duration.
        approx_trim_start = min(approx_trim_start, max(0.0, first.media_duration - 0.01))

        # Target duration cannot exceed available concatenated media after trim_start.
        effective_start = max(start_wall, actual_start)
        effective_end = min(end_wall, actual_end)
        desired_span = max(0.0, effective_end - effective_start)
        available_media_after_trim = max(0.0, total_media - approx_trim_start)
        target_duration = min(desired_span, available_media_after_trim)

        is_partial = (
            (not has_full_preroll)
            or (not has_full_postroll)
            or bool(internal_discs)
        )

        coverage = PinCoverageReport(
            requested_start_wall=start_wall,
            requested_end_wall=end_wall,
            has_full_preroll=has_full_preroll,
            has_full_postroll=has_full_postroll,
            is_partial=is_partial,
            segment_count=len(selected),
            actual_approx_start_wall=actual_start,
            actual_approx_end_wall=actual_end,
            missing_leading_seconds=missing_leading,
            missing_trailing_seconds=missing_trailing,
            total_media_duration=total_media,
            discontinuities=internal_discs,
        )
        return SegmentSelection(
            segments=selected,
            approx_trim_start_seconds=approx_trim_start,
            target_duration_seconds=target_duration,
            coverage=coverage,
        )

    # -- internal helpers ------------------------------------------------------

    def _detect_discontinuity(
        self, prev: VideoSegment, nxt: VideoSegment
    ) -> DiscontinuityRecord | None:
        mono_gap = nxt.approx_start_mono - prev.approx_end_mono
        wall_gap = nxt.approx_start_wall - prev.approx_end_wall

        if nxt.session_id != prev.session_id:
            return DiscontinuityRecord(
                prev_segment_id=prev.segment_id,
                next_segment_id=nxt.segment_id,
                prev_session_id=prev.session_id,
                next_session_id=nxt.session_id,
                mono_gap_seconds=mono_gap,
                wall_gap_seconds=wall_gap,
                reason="session_restart",
            )

        # Positive monotonic gap larger than tolerance indicates an ad pause,
        # network stall, or dropped segments. (Negative deltas during initial
        # HLS edge prefetch burst are expected and NOT treated as missing gaps.)
        if mono_gap > self._gap_tolerance:
            return DiscontinuityRecord(
                prev_segment_id=prev.segment_id,
                next_segment_id=nxt.segment_id,
                prev_session_id=prev.session_id,
                next_session_id=nxt.session_id,
                mono_gap_seconds=mono_gap,
                wall_gap_seconds=wall_gap,
                reason="monotonic_gap",
            )

        pts_gap = nxt.ffmpeg_start_pts - prev.ffmpeg_end_pts
        if pts_gap < -0.5 or pts_gap > self._pts_tolerance:
            return DiscontinuityRecord(
                prev_segment_id=prev.segment_id,
                next_segment_id=nxt.segment_id,
                prev_session_id=prev.session_id,
                next_session_id=nxt.session_id,
                mono_gap_seconds=mono_gap,
                wall_gap_seconds=wall_gap,
                reason="pts_discontinuity",
            )

        return None

    def _prune_stale_discontinuities(self) -> None:
        self._discontinuities = [
            d
            for d in self._discontinuities
            if d.prev_segment_id in self._segment_ids
            and d.next_segment_id in self._segment_ids
        ]
