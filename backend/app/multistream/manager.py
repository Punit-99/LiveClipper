"""Stage 6.3: `MultiStreamManager` — multi-streamer orchestrator.

Coordinates multiple independent `StreamSession` instances concurrently with:
- Configurable concurrency ceiling (`max_concurrent_streams`).
- Shared Stage 4/6.1 `HighlightAnalysisWorker` with per-stream queue fairness.
- Shared Stage 6.2 `StreamRoutingAnalysisSink` routing verdicts and drops back
  to the owning session.
- Shared `asyncio.Semaphore` bounding concurrent FFmpeg clip extractions.
- `GlobalDiskBudget` monitoring and trimming unpinned segments across sessions
  without ever deleting pinned segments or another active session's data.
- `ChannelLivenessChecker` performing batched Helix liveness checks (stopping
  sessions when channels go offline; optionally restarting when they return).
- Strict two-phase graceful shutdown and POSIX `SIGINT`/`SIGTERM` handling:
  1. Stop liveness polling & all chat monitors (no new events ingested).
  2. Drain the shared Gemini worker while routes & clip workers stay open.
  3. Stop all sessions (drain clip extractions, terminate Streamlink/FFmpeg,
     and clean up each run's unpinned buffer).
  4. Stop the global disk budget monitor.
"""

from __future__ import annotations

import asyncio
import dataclasses
import signal
from dataclasses import dataclass
from typing import Any, Callable

from app.analysis import (
    AnalysisConfig,
    AnalysisSink,
    CompositeAnalysisSink,
    GeminiHighlightAnalyzer,
    HighlightAnalysisWorker,
    JsonlAnalysisSink,
    build_analysis_config,
    logging_analysis_sink,
)
from app.analysis.gemini_client import HighlightAnalyzer
from app.config import Settings
from app.discovery.stream_finder import LiveStream, StreamFinder
from app.logging_setup import get_logger
from app.multistream.disk_budget import BudgetSnapshot, GlobalDiskBudget
from app.multistream.liveness import (
    ChannelLivenessChecker,
    CheckLiveFn,
    LivenessStatusSnapshot,
)
from app.multistream.router import StreamRoutingAnalysisSink, normalize_channel
from app.multistream.session import (
    ChatMonitorFactory,
    SessionSnapshot,
    SessionState,
    StateChangeCallback,
    StreamSession,
)
from app.recording import (
    ClipExtractionResult,
    RecordingConfig,
)
from app.recording.clip_extractor import ExtractorProcessFactory
from app.recording.stream_recorder import PipelineFactory

log = get_logger(__name__)


@dataclass(frozen=True)
class GeminiWorkerSnapshot:
    """Inspectable status of the shared Gemini worker."""

    enabled: bool
    pending_count: int
    submitted_count: int
    analyzed_count: int
    prefilter_skipped_count: int
    failed_count: int
    dropped_count: int


@dataclass(frozen=True)
class MultiStreamStatus:
    """Complete inspectable status snapshot of `MultiStreamManager`."""

    running: bool
    max_concurrent_streams: int
    target_channels: list[str]
    active_channels: list[str]
    sessions: dict[str, SessionSnapshot]
    disk_budget: BudgetSnapshot
    gemini: GeminiWorkerSnapshot
    liveness: LivenessStatusSnapshot | None
    routed_verdicts: int
    discarded_verdicts: int


class MultiStreamManager:
    """Asyncio orchestrator managing multiple isolated `StreamSession` instances."""

    def __init__(
        self,
        settings: Settings,
        channels: list[str] | None = None,
        *,
        max_concurrent_streams: int | None = None,
        max_total_buffer_mb: float | None = None,
        max_concurrent_extractions: int | None = None,
        clip_queue_max_size: int = 16,
        gemini_max_queued_per_stream: int | None = None,
        liveness_poll_seconds: float | None = None,
        auto_restart_online: bool = False,
        enable_liveness_check: bool = True,
        enable_disk_budget: bool = True,
        disk_check_interval_seconds: float = 5.0,
        disk_emergency_multiplier: float = 1.2,
        print_messages: bool = False,
        # Optional dependency injection (used by tests & custom runners):
        analyzer: HighlightAnalyzer | None = None,
        stream_finder: StreamFinder | CheckLiveFn | None = None,
        recording_config: RecordingConfig | None = None,
        streamlink_bin: str | None = None,
        ffmpeg_bin: str | None = None,
        cleanup_interval_seconds: float = 5.0,
        trailing_wait_seconds: float = 15.0,
        chat_monitor_factory: ChatMonitorFactory | None = None,
        pipeline_factory: PipelineFactory | None = None,
        extractor_process_factory: ExtractorProcessFactory | None = None,
        on_extracted: Callable[[ClipExtractionResult], None] | None = None,
        on_state_change: StateChangeCallback | None = None,
        on_verdict: Callable[[AnalyzedHighlight], None] | None = None,
        custom_sessions: list[StreamSession] | None = None,
    ) -> None:
        max_streams = (
            max_concurrent_streams
            if max_concurrent_streams is not None
            else settings.max_concurrent_streams
        )
        if max_streams < 1:
            raise ValueError("max_concurrent_streams must be >= 1")

        max_extractions = (
            max_concurrent_extractions
            if max_concurrent_extractions is not None
            else settings.max_concurrent_extractions
        )
        if max_extractions < 1:
            raise ValueError("max_concurrent_extractions must be >= 1")

        total_buffer_mb = (
            max_total_buffer_mb
            if max_total_buffer_mb is not None
            else settings.max_total_buffer_mb
        )
        poll_secs = (
            liveness_poll_seconds
            if liveness_poll_seconds is not None
            else settings.liveness_poll_seconds
        )

        self._settings = settings
        self._max_concurrent_streams = max_streams
        self._max_concurrent_extractions = max_extractions
        self._clip_queue_max_size = clip_queue_max_size
        self._gemini_max_queued_per_stream = gemini_max_queued_per_stream
        self._auto_restart_online = auto_restart_online
        self._enable_liveness = enable_liveness_check
        self._enable_disk_budget = enable_disk_budget
        self._print_messages = print_messages

        # Injected/forwarded factories.
        self._injected_analyzer = analyzer
        self._stream_finder = stream_finder
        self._recording_config = recording_config
        self._streamlink_bin = streamlink_bin
        self._ffmpeg_bin = ffmpeg_bin
        self._cleanup_interval = cleanup_interval_seconds
        self._trailing_wait = trailing_wait_seconds
        self._chat_monitor_factory = chat_monitor_factory
        self._pipeline_factory = pipeline_factory
        self._extractor_process_factory = extractor_process_factory
        self._on_extracted = on_extracted
        self._on_state_change = on_state_change
        self._on_verdict = on_verdict

        # Normalized deduplicated target channels preserving order.
        self._target_channels: list[str] = []
        for raw in channels or []:
            ch = normalize_channel(raw)
            if ch and ch not in self._target_channels:
                self._target_channels.append(ch)

        # Shared multi-stream infrastructure.
        self._router = StreamRoutingAnalysisSink()
        self._extraction_semaphore = asyncio.Semaphore(max_extractions)
        self._worker: HighlightAnalysisWorker | None = None
        self._disk_budget = GlobalDiskBudget(
            max_total_buffer_mb=total_buffer_mb,
            emergency_multiplier=disk_emergency_multiplier,
            check_interval_seconds=disk_check_interval_seconds,
            on_emergency_budget=self._on_global_disk_emergency,
        )
        self._liveness: ChannelLivenessChecker | None = None
        self._liveness_poll_seconds = poll_secs

        # Session registry & lifecycle synchronization.
        self._sessions: dict[str, StreamSession] = {}
        if custom_sessions:
            for sess in custom_sessions:
                self._sessions[sess.channel] = sess
                if sess.channel not in self._target_channels:
                    self._target_channels.append(sess.channel)

        self._offline_channels: set[str] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._running = False
        self._stopping = False
        self._shutdown_event = asyncio.Event()
        self._installed_signals: list[signal.Signals] = []

    # -- inspectable properties ------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def max_concurrent_streams(self) -> int:
        return self._max_concurrent_streams

    @property
    def target_channels(self) -> list[str]:
        return list(self._target_channels)

    @property
    def router(self) -> StreamRoutingAnalysisSink:
        return self._router

    @property
    def worker(self) -> HighlightAnalysisWorker | None:
        return self._worker

    @property
    def disk_budget(self) -> GlobalDiskBudget:
        return self._disk_budget

    @property
    def liveness_checker(self) -> ChannelLivenessChecker | None:
        return self._liveness

    @property
    def extraction_semaphore(self) -> asyncio.Semaphore:
        return self._extraction_semaphore

    def get_session(self, channel: str) -> StreamSession | None:
        return self._sessions.get(normalize_channel(channel))

    def active_channels(self) -> list[str]:
        return sorted(
            ch
            for ch, sess in self._sessions.items()
            if sess.state == SessionState.RUNNING
        )

    def status(self) -> MultiStreamStatus:
        """Return an immutable point-in-time status snapshot of all managed components."""
        w = self._worker
        gemini_snap = GeminiWorkerSnapshot(
            enabled=w is not None,
            pending_count=w.pending_count if w is not None else 0,
            submitted_count=w.submitted_count if w is not None else 0,
            analyzed_count=w.analyzed_count if w is not None else 0,
            prefilter_skipped_count=w.prefilter_skipped_count if w is not None else 0,
            failed_count=w.failed_count if w is not None else 0,
            dropped_count=len(w.dropped_events) if w is not None else 0,
        )
        return MultiStreamStatus(
            running=self._running,
            max_concurrent_streams=self._max_concurrent_streams,
            target_channels=list(self._target_channels),
            active_channels=self.active_channels(),
            sessions={ch: sess.snapshot() for ch, sess in sorted(self._sessions.items())},
            disk_budget=self._disk_budget.snapshot(),
            gemini=gemini_snap,
            liveness=self._liveness.snapshot() if self._liveness is not None else None,
            routed_verdicts=self._router.routed_count,
            discarded_verdicts=self._router.discarded_count,
        )

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> dict[str, bool]:
        """Start shared infrastructure and initial target sessions up to the concurrency limit.

        Returns a dict mapping each attempted channel to whether it started.
        """
        async with self._lifecycle_lock:
            if self._running:
                return {ch: sess.is_running for ch, sess in self._sessions.items()}

            self._stopping = False
            self._shutdown_event.clear()

            # 1) Build and start shared Gemini analysis worker (if enabled).
            self._worker = self._build_shared_worker()
            if self._worker is not None:
                await self._worker.start()

            # 2) Start global disk budget monitor when recording is enabled.
            if self._settings.recording_enabled and self._enable_disk_budget:
                await self._disk_budget.start_budget_loop()

            self._running = True

            # 3) Start initial channels respecting max_concurrent_streams.
            to_start = self._target_channels[: self._max_concurrent_streams]
            if len(self._target_channels) > self._max_concurrent_streams:
                skipped = self._target_channels[self._max_concurrent_streams :]
                log.warning(
                    "Target channel count (%d) exceeds max_concurrent_streams (%d); "
                    "not starting: %s",
                    len(self._target_channels),
                    self._max_concurrent_streams,
                    ", ".join(skipped),
                )

            results: dict[str, bool] = {}
            if to_start:
                sessions = [self._get_or_create_session(ch) for ch in to_start]
                outcomes = await asyncio.gather(*(s.start() for s in sessions))
                for ch, ok in zip(to_start, outcomes):
                    results[ch] = ok

            # 4) Start batched Helix liveness checker.
            if self._enable_liveness:
                finder = (
                    self._stream_finder
                    if self._stream_finder is not None
                    else StreamFinder(self._settings)
                )
                self._liveness = ChannelLivenessChecker(
                    finder,
                    poll_interval_seconds=self._liveness_poll_seconds,
                    on_channel_offline=self._handle_channel_offline,
                    on_channel_online=self._handle_channel_online,
                )
                self._liveness.set_watched_channels(
                    self.active_channels(), initial_live=True
                )
                await self._liveness.start()

            log.info(
                "MultiStreamManager started (active=%d/%d, max_concurrent=%d, recording=%s).",
                len(self.active_channels()),
                len(self._target_channels),
                self._max_concurrent_streams,
                self._settings.recording_enabled,
            )
            return results

    async def stop(
        self,
        *,
        gemini_drain_timeout: float = 15.0,
        clip_drain_timeout: float = 30.0,
        chat_stop_timeout: float = 5.0,
    ) -> None:
        """Perform strict two-phase graceful shutdown across all managed sessions.

        Phase 1: Stop liveness polling and all chat monitors (routes & clip
                 workers remain open).
        Drain:   Drain the shared Gemini analysis worker so in-flight verdicts
                 route cleanly to open clip extraction workers.
        Phase 2: Stop all sessions (drain FFmpeg extractions, terminate
                 Streamlink/FFmpeg process groups, clean up unpinned buffers).
        Final:   Stop global disk budget loop.
        """
        self._stopping = True
        self._shutdown_event.set()

        async with self._lifecycle_lock:
            if not self._running and self._worker is None and not self._sessions:
                return

            sessions = list(self._sessions.values())

            # Phase 1a: Stop liveness polling so it cannot trigger restarts mid-shutdown.
            if self._liveness is not None:
                await self._liveness.stop()
                self._liveness = None

            # Phase 1b: Stop all chat monitors concurrently while routes stay open.
            if sessions:
                await asyncio.gather(
                    *(s.stop_chat(timeout=chat_stop_timeout) for s in sessions),
                    return_exceptions=True,
                )

            # Drain shared Gemini worker BEFORE stopping clip extraction workers!
            if self._worker is not None:
                await self._worker.stop(drain_timeout=gemini_drain_timeout)
                self._worker = None

            # Phase 2: Stop all sessions (drains extractions, stops recorders, purges buffers).
            if sessions:
                await asyncio.gather(
                    *(
                        s.stop(
                            clip_drain_timeout=clip_drain_timeout,
                            chat_stop_timeout=chat_stop_timeout,
                            cleanup_buffer=True,
                        )
                        for s in sessions
                    ),
                    return_exceptions=True,
                )
                for s in sessions:
                    self._disk_budget.unregister_session(s)

            # Final: Stop global disk budget monitor.
            await self._disk_budget.stop_budget_loop()

            self._running = False
            log.info("MultiStreamManager stopped cleanly.")

    # -- dynamic per-channel operations ----------------------------------------

    async def start_channel(self, channel: str) -> bool:
        """Start (or add & start) one channel session, enforcing `max_concurrent_streams`."""
        ch = normalize_channel(channel)
        if not ch:
            raise ValueError("channel must be a non-empty Twitch login")

        if self._stopping:
            return False

        existing = self._sessions.get(ch)
        if existing is not None and existing.is_running:
            return True

        active_count = sum(
            1
            for s in self._sessions.values()
            if s.state in (SessionState.RUNNING, SessionState.STOPPING)
        )
        if active_count >= self._max_concurrent_streams:
            log.warning(
                "Cannot start #%s: max_concurrent_streams (%d) already reached.",
                ch,
                self._max_concurrent_streams,
            )
            return False

        if ch not in self._target_channels:
            self._target_channels.append(ch)

        session = self._get_or_create_session(ch)
        ok = await session.start()
        if ok:
            self._offline_channels.discard(ch)
            if self._liveness is not None:
                self._liveness.add_channel(ch, initial_live=True)
        return ok

    async def stop_channel(
        self,
        channel: str,
        *,
        clip_drain_timeout: float = 30.0,
        remove_from_liveness: bool = True,
    ) -> bool:
        """Stop one channel session gracefully without affecting other sessions."""
        ch = normalize_channel(channel)
        session = self._sessions.get(ch)
        if session is None:
            return False

        if remove_from_liveness and self._liveness is not None:
            self._liveness.remove_channel(ch)

        await session.stop(clip_drain_timeout=clip_drain_timeout)
        self._disk_budget.unregister_session(session)
        return True

    async def restart_channel(
        self,
        channel: str,
        *,
        clip_drain_timeout: float = 30.0,
    ) -> bool:
        """Restart one channel session independently with a fresh `run_id`."""
        ch = normalize_channel(channel)
        session = self._sessions.get(ch)
        if session is None:
            return await self.start_channel(ch)

        self._disk_budget.register_session(session)
        ok = await session.restart(clip_drain_timeout=clip_drain_timeout)
        if ok:
            self._offline_channels.discard(ch)
            if self._liveness is not None:
                self._liveness.add_channel(ch, initial_live=True)
        return ok

    # -- signal handling & blocking runner -------------------------------------

    def request_shutdown(self) -> None:
        """Signal the manager to begin graceful shutdown (signal-handler safe)."""
        self._stopping = True
        self._shutdown_event.set()

    def install_signal_handlers(
        self, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """Install graceful `SIGINT`/`SIGTERM` handlers on the running event loop."""
        target_loop = loop or asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                target_loop.add_signal_handler(sig, self._on_signal, sig)
                if sig not in self._installed_signals:
                    self._installed_signals.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                # Fallback when running on Windows or non-main thread.
                pass

    def remove_signal_handlers(
        self, loop: asyncio.AbstractEventLoop | None = None
    ) -> None:
        """Remove any previously installed `SIGINT`/`SIGTERM` handlers."""
        if not self._installed_signals:
            return
        try:
            target_loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            self._installed_signals.clear()
            return
        for sig in list(self._installed_signals):
            try:
                target_loop.remove_signal_handler(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        self._installed_signals.clear()

    async def wait_for_shutdown(self) -> None:
        """Wait until `request_shutdown()` or a `SIGINT`/`SIGTERM` signal fires."""
        await self._shutdown_event.wait()

    async def run_until_shutdown(
        self,
        *,
        gemini_drain_timeout: float = 15.0,
        clip_drain_timeout: float = 30.0,
    ) -> None:
        """Start the manager, install signal handlers, wait for shutdown, and stop cleanly."""
        self.install_signal_handlers()
        try:
            await self.start()
            await self.wait_for_shutdown()
        finally:
            self.remove_signal_handlers()
            await self.stop(
                gemini_drain_timeout=gemini_drain_timeout,
                clip_drain_timeout=clip_drain_timeout,
            )

    # -- internals -------------------------------------------------------------

    def _on_signal(self, sig: signal.Signals) -> None:
        log.info("Received signal %s; initiating graceful multi-stream shutdown...", sig.name)
        self.request_shutdown()

    def _build_shared_worker(self) -> HighlightAnalysisWorker | None:
        """Construct the shared Stage 4/6.1 Gemini worker with per-stream fairness."""
        if self._injected_analyzer is not None:
            analyzer: HighlightAnalyzer = self._injected_analyzer
            base_cfg = build_analysis_config(self._settings)
        else:
            if not self._settings.gemini_enabled:
                log.info(
                    "Gemini analysis disabled (GEMINI_ENABLED=false); running detection-only."
                )
                return None
            if not self._settings.gemini_api_key:
                log.warning(
                    "GEMINI_API_KEY is not configured; running detection-only across streams."
                )
                return None
            base_cfg = build_analysis_config(self._settings)
            analyzer = GeminiHighlightAnalyzer(
                api_key=self._settings.gemini_api_key,
                config=base_cfg,
            )

        # Enforce per-stream queue fairness and guarantee queue_max_size >= cap * max_streams.
        per_stream_cap = (
            self._gemini_max_queued_per_stream
            if self._gemini_max_queued_per_stream is not None
            else max(2, base_cfg.queue_max_size // max(1, self._max_concurrent_streams))
        )
        queue_max = max(base_cfg.queue_max_size, per_stream_cap * self._max_concurrent_streams)
        analysis_cfg = dataclasses.replace(
            base_cfg,
            queue_max_size=queue_max,
            max_queued_per_stream=per_stream_cap,
        )

        sinks: list[AnalysisSink] = [
            logging_analysis_sink,
            JsonlAnalysisSink(self._settings.highlights_output_path),
            self._router,
        ]
        if self._on_verdict is not None:
            sinks.append(self._on_verdict)
        composite_sink = CompositeAnalysisSink(*sinks)

        log.info(
            "Shared Gemini worker configured (model=%s, queue_max=%d, "
            "max_queued_per_stream=%d, concurrency=%d).",
            analysis_cfg.model,
            analysis_cfg.queue_max_size,
            per_stream_cap,
            analysis_cfg.max_concurrency,
        )
        return HighlightAnalysisWorker(
            analyzer=analyzer,
            config=analysis_cfg,
            sink=composite_sink,
            on_event_dropped=self._router.handle_dropped_event,
        )

    def _get_or_create_session(self, channel: str) -> StreamSession:
        ch = normalize_channel(channel)
        session = self._sessions.get(channel) or self._sessions.get(ch)
        if session is None:
            for k, s in self._sessions.items():
                if k.lower() == channel.lower() or k.lower() == ch.lower():
                    session = s
                    break
        if session is None:
            session = StreamSession(
                channel=ch,
                settings=self._settings,
                submit_to_analysis=self._worker.submit if self._worker is not None else None,
                router=self._router,
                extraction_semaphore=self._extraction_semaphore,
                clip_queue_max_size=self._clip_queue_max_size,
                recording_config=self._recording_config,
                print_messages=self._print_messages,
                streamlink_bin=self._streamlink_bin,
                ffmpeg_bin=self._ffmpeg_bin,
                cleanup_interval_seconds=self._cleanup_interval,
                trailing_wait_seconds=self._trailing_wait,
                chat_monitor_factory=self._chat_monitor_factory,
                pipeline_factory=self._pipeline_factory,
                extractor_process_factory=self._extractor_process_factory,
                on_extracted=self._on_extracted,
                on_state_change=self._on_state_change,
            )
            self._sessions[ch] = session
        else:
            if session._submit_to_analysis is None and self._worker is not None:
                session._submit_to_analysis = self._worker.submit
            if session._router is None:
                session._router = self._router
            if session._extraction_semaphore is None:
                session._extraction_semaphore = self._extraction_semaphore
            if session._streamlink_bin is None:
                session._streamlink_bin = self._streamlink_bin
            if session._ffmpeg_bin is None:
                session._ffmpeg_bin = self._ffmpeg_bin
            if session._base_recording_config is None:
                session._base_recording_config = self._recording_config
            if not session._print_messages and self._print_messages:
                session._print_messages = self._print_messages
        self._disk_budget.register_session(session)
        return session

    def _on_global_disk_emergency(self, reason: str) -> None:
        """Pause/stop live recordings cleanly on global emergency ceiling without deleting pins."""
        for sess in list(self._sessions.values()):
            rec = sess.recorder
            if rec is not None and rec.is_running:
                rec.request_emergency_stop(reason)

    async def _handle_channel_offline(self, channel: str) -> None:
        """Option A: gracefully stop the individual offline session (no auto-replacement)."""
        if self._stopping:
            return
        self._offline_channels.add(channel)
        log.warning(
            "Channel #%s went offline; stopping individual session gracefully.",
            channel,
        )
        # Keep watching in liveness checker ONLY if auto_restart_online is enabled.
        await self.stop_channel(
            channel,
            remove_from_liveness=not self._auto_restart_online,
        )

    async def _handle_channel_online(self, channel: str, _stream: LiveStream) -> None:
        """Optionally restart a previously monitored channel when it returns online."""
        if self._stopping or not self._auto_restart_online:
            return
        if channel not in self._offline_channels:
            return
        log.info(
            "Previously monitored channel #%s returned online; restarting session.",
            channel,
        )
        await self.start_channel(channel)
