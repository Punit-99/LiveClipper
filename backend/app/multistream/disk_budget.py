"""Stage 6.3: Global disk budget manager for multi-stream live buffers.

Monitors total buffer storage across all active `StreamSession` instances.
When total disk usage exceeds `max_total_buffer_mb`:
1. Requests normal retention cleanup across active sessions.
2. Trims oldest unpinned segments across sessions (prioritizing sessions with
   the largest unpinned storage) until storage drops back within budget.
3. NEVER deletes pinned segments or evicts APPROVED/EXTRACTING clips.
4. Protects against cross-session data corruption: each session trims only its
   own unpinned files via its thread-safe `RollingBufferManager`.
5. If total storage exceeds the emergency ceiling (`max_total_buffer_mb * emergency_multiplier`),
   flags emergency state and invokes `on_emergency_budget`.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from app.logging_setup import get_logger
from app.multistream.session import StreamSession

log = get_logger(__name__)

EmergencyCallback = Callable[[str], None]


class BudgetState(str, Enum):
    OK = "ok"
    TRIMMING = "trimming"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    EMERGENCY = "emergency"


@dataclass(frozen=True)
class BudgetSnapshot:
    """Inspectable point-in-time snapshot of multi-stream disk storage."""

    total_bytes: int
    pinned_bytes: int
    unpinned_bytes: int
    max_total_bytes: int
    emergency_bytes: int
    state: BudgetState
    active_session_count: int
    bytes_over_budget: int


@dataclass(frozen=True)
class BudgetCheckResult:
    """Outcome of one `enforce_budget()` evaluation pass."""

    state: BudgetState
    initial_bytes: int
    freed_bytes: int
    final_bytes: int
    pinned_bytes: int
    emergency_triggered: bool


class GlobalDiskBudget:
    """Coordinates global disk cap enforcement across multiple `StreamSession` buffers."""

    def __init__(
        self,
        max_total_buffer_mb: float = 1500.0,
        *,
        emergency_multiplier: float = 1.2,
        check_interval_seconds: float = 5.0,
        on_emergency_budget: EmergencyCallback | None = None,
    ) -> None:
        if max_total_buffer_mb <= 0:
            raise ValueError("max_total_buffer_mb must be > 0")
        if emergency_multiplier < 1.0:
            raise ValueError("emergency_multiplier must be >= 1.0")
        if check_interval_seconds <= 0:
            raise ValueError("check_interval_seconds must be > 0")

        self._max_total_bytes = int(max_total_buffer_mb * 1024 * 1024)
        self._emergency_bytes = int(self._max_total_bytes * emergency_multiplier)
        self._check_interval = check_interval_seconds
        self._on_emergency = on_emergency_budget

        self._lock = threading.RLock()
        self._sessions: dict[str, StreamSession] = {}
        self._state = BudgetState.OK
        self._emergency_triggered = False

        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._running = False
        self._async_lock = asyncio.Lock()

    # -- session registration --------------------------------------------------

    def register_session(self, session: StreamSession) -> None:
        with self._lock:
            self._sessions[session.channel] = session

    def unregister_session(self, session: StreamSession) -> None:
        with self._lock:
            self._sessions.pop(session.channel, None)

    # -- properties & inspection -----------------------------------------------

    @property
    def max_total_bytes(self) -> int:
        return self._max_total_bytes

    @property
    def emergency_bytes(self) -> int:
        return self._emergency_bytes

    @property
    def is_running(self) -> bool:
        return self._running and self._task is not None and not self._task.done()

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            total = 0
            pinned = 0
            count = 0
            for sess in list(self._sessions.values()):
                mgr = sess.buffer_manager
                if mgr is not None:
                    count += 1
                    total += mgr.index.total_bytes
                    pinned += mgr.index.pinned_bytes

            unpinned = max(0, total - pinned)
            over = max(0, total - self._max_total_bytes)
            return BudgetSnapshot(
                total_bytes=total,
                pinned_bytes=pinned,
                unpinned_bytes=unpinned,
                max_total_bytes=self._max_total_bytes,
                emergency_bytes=self._emergency_bytes,
                state=self._state,
                active_session_count=count,
                bytes_over_budget=over,
            )

    # -- budget enforcement ----------------------------------------------------

    def enforce_budget(self) -> BudgetCheckResult:
        """Evaluate multi-stream buffer usage and trim unpinned segments if over budget.

        Returns a detailed `BudgetCheckResult`. Thread-safe.
        """
        with self._lock:
            sessions = [
                s
                for s in self._sessions.values()
                if s.buffer_manager is not None
            ]
            initial_total = sum(s.buffer_manager.index.total_bytes for s in sessions)  # type: ignore[union-attr]
            initial_pinned = sum(s.buffer_manager.index.pinned_bytes for s in sessions)  # type: ignore[union-attr]

            if initial_total <= self._max_total_bytes:
                self._state = BudgetState.OK
                return BudgetCheckResult(
                    state=BudgetState.OK,
                    initial_bytes=initial_total,
                    freed_bytes=0,
                    final_bytes=initial_total,
                    pinned_bytes=initial_pinned,
                    emergency_triggered=self._emergency_triggered,
                )

            # Stage 1: Request each session run cleanup_once() to expire TTL pins
            # and purge normally expired unpinned segments.
            self._state = BudgetState.TRIMMING
            total_freed = 0
            for s in sessions:
                assert s.buffer_manager is not None
                summary = s.buffer_manager.cleanup_once()
                total_freed += summary.freed_bytes

            current_total = sum(s.buffer_manager.index.total_bytes for s in sessions)  # type: ignore[union-attr]

            # Stage 2: If still over budget, trim oldest unpinned segments across sessions,
            # prioritizing the session holding the largest unpinned buffer.
            if current_total > self._max_total_bytes:
                needed = current_total - self._max_total_bytes
                # Sort sessions by unpinned bytes descending so the biggest buffer trims first.
                sorted_sessions = sorted(
                    sessions,
                    key=lambda s: max(
                        0,
                        s.buffer_manager.index.total_bytes  # type: ignore[union-attr]
                        - s.buffer_manager.index.pinned_bytes,  # type: ignore[union-attr]
                    ),
                    reverse=True,
                )
                for s in sorted_sessions:
                    if needed <= 0:
                        break
                    assert s.buffer_manager is not None
                    freed = s.buffer_manager.trim_oldest_unpinned_segments(needed)
                    total_freed += freed
                    needed -= freed

            final_total = sum(s.buffer_manager.index.total_bytes for s in sessions)  # type: ignore[union-attr]
            final_pinned = sum(s.buffer_manager.index.pinned_bytes for s in sessions)  # type: ignore[union-attr]

            # Stage 3: Emergency ceiling check.
            if final_total >= self._emergency_bytes:
                self._state = BudgetState.EMERGENCY
                if not self._emergency_triggered:
                    self._emergency_triggered = True
                    reason = (
                        f"Global disk budget emergency ceiling reached: "
                        f"{final_total / (1024 * 1024):.1f} MB >= "
                        f"{self._emergency_bytes / (1024 * 1024):.1f} MB "
                        f"(pinned={final_pinned / (1024 * 1024):.1f} MB across {len(sessions)} stream(s))."
                    )
                    log.error("%s", reason)
                    if self._on_emergency is not None:
                        try:
                            self._on_emergency(reason)
                        except Exception:  # noqa: BLE001
                            log.exception("on_emergency_budget callback raised.")
            elif final_total > self._max_total_bytes:
                self._state = BudgetState.CAPACITY_EXHAUSTED
                log.warning(
                    "Global disk budget capacity exhausted (%d / %d bytes, pinned=%d). "
                    "All unpinned segments trimmed.",
                    final_total,
                    self._max_total_bytes,
                    final_pinned,
                )
            else:
                self._state = BudgetState.OK
                if total_freed > 0:
                    log.info(
                        "Global disk budget restored (%d MB freed; total=%d MB, max=%d MB).",
                        total_freed // (1024 * 1024),
                        final_total // (1024 * 1024),
                        self._max_total_bytes // (1024 * 1024),
                    )

            return BudgetCheckResult(
                state=self._state,
                initial_bytes=initial_total,
                freed_bytes=total_freed,
                final_bytes=final_total,
                pinned_bytes=final_pinned,
                emergency_triggered=self._emergency_triggered,
            )

    # -- async background loop -------------------------------------------------

    async def start_budget_loop(self) -> None:
        async with self._async_lock:
            if self._running:
                return
            self._running = True
            self._stop_event = asyncio.Event()
            self._task = asyncio.create_task(self._loop(), name="global-disk-budget")
            log.info(
                "GlobalDiskBudget started (max=%.1f MB, emergency=%.1f MB, interval=%.1fs).",
                self._max_total_bytes / (1024 * 1024),
                self._emergency_bytes / (1024 * 1024),
                self._check_interval,
            )

    async def stop_budget_loop(self) -> None:
        async with self._async_lock:
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
            log.info("GlobalDiskBudget stopped.")

    async def _loop(self) -> None:
        while self._running:
            if await self._interruptible_sleep(self._check_interval):
                break
            try:
                self.enforce_budget()
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error in GlobalDiskBudget check.")

    async def _interruptible_sleep(self, seconds: float) -> bool:
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
