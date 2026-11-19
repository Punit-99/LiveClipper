"""Stage 6.2: `StreamSession` — isolated single-streamer runtime encapsulation.

Encapsulates ONE streamer's full lifecycle so multiple streams can run
concurrently without sharing mutable per-stream state:
- `ChatMonitor` (independent WebSocket connection, quiet output by default)
- `StreamHighlightDetector` (per-channel spike & event detector)
- Optional Stage 5 recording stack (ONLY when `settings.recording_enabled=True`
  and Gemini analysis submission is enabled, preserving Stage 1–5 defaults):
  * `RollingBufferManager` & `SegmentIndex`
  * `StreamRecorder` writing to `<buffer_dir>/<channel>/<run_id>/`
  * `ClipWindowPlanner`
  * `ClipExtractor` writing to `<clips_output_dir>/<channel>/`
  * `ClipExtractionWorker` (bounded queue + optional shared FFmpeg semaphore)
  * `Stage5PinAndSubmitSink` bridging detector -> shared Gemini worker + buffer

Design guarantees:
- Independently startable, stoppable, and restartable (fresh `run_id` and clean
  components on every start/restart).
- Inspectable lifecycle states: `STOPPED`, `RUNNING`, `STOPPING`, `ERRORED`.
- Strict failure isolation: startup or runtime failures mark THIS session
  `ERRORED`, clean up THIS session's resources, and NEVER propagate to other
  sessions.
- Supports two-phase shutdown (`stop_chat()` then `stop()`) so a coordinator
  can stop chat ingestion across all streams, drain the shared Gemini worker so
  in-flight verdicts still route to open clip workers, and then stop each
  session cleanly.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from app.analysis.models import AnalyzedHighlight, CompositeHighlightSink
from app.chat.twitch_chat import ChatMonitor
from app.config import Settings
from app.detection import (
    HighlightSink,
    StreamHighlightDetector,
    build_highlight_config,
    logging_sink,
)
from app.detection.highlight_event import HighlightEvent
from app.logging_setup import get_logger
from app.multistream.router import StreamRoutingAnalysisSink, normalize_channel
from app.recording import (
    ClipExtractionResult,
    ClipExtractionWorker,
    ClipExtractor,
    ClipWindowPlanner,
    RecordingConfig,
    RollingBufferManager,
    SegmentIndex,
    Stage5PinAndSubmitSink,
    StreamRecorder,
    build_recording_config,
)
from app.recording.clip_extractor import ExtractorProcessFactory
from app.recording.stream_recorder import PipelineFactory

log = get_logger(__name__)

SubmitToAnalysisFn = Callable[[HighlightEvent], bool]
ChatMonitorFactory = Callable[[Settings, str, StreamHighlightDetector, bool], Any]
StateChangeCallback = Callable[["StreamSession", "SessionState", "SessionState"], None]


class SessionState(str, Enum):
    """Inspectable lifecycle state of a `StreamSession`."""

    STOPPED = "stopped"
    RUNNING = "running"
    STOPPING = "stopping"
    ERRORED = "errored"


@dataclass(frozen=True)
class SessionSnapshot:
    """Point-in-time read-only status snapshot of a `StreamSession`."""

    channel: str
    state: SessionState
    run_id: str | None
    recording_enabled: bool
    recording_session_id: int
    start_count: int
    segments_registered: int
    active_pins: int
    buffer_bytes: int
    clips_extracted: int
    clips_failed: int
    clips_dropped: int
    error_message: str | None


def _new_run_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )


def _default_chat_monitor_factory(
    settings: Settings,
    channel: str,
    pipeline: StreamHighlightDetector,
    print_messages: bool,
) -> ChatMonitor:
    return ChatMonitor(
        settings,
        channel,
        pipeline=pipeline,
        print_messages=print_messages,
    )


class StreamSession:
    """Isolated lifecycle container for one monitored (and optionally recorded) streamer."""

    def __init__(
        self,
        channel: str,
        settings: Settings,
        *,
        submit_to_analysis: SubmitToAnalysisFn | None = None,
        router: StreamRoutingAnalysisSink | None = None,
        extraction_semaphore: asyncio.Semaphore | None = None,
        clip_queue_max_size: int = 16,
        recording_config: RecordingConfig | None = None,
        print_messages: bool = False,
        streamlink_bin: str | None = None,
        ffmpeg_bin: str | None = None,
        cleanup_interval_seconds: float = 5.0,
        trailing_wait_seconds: float = 15.0,
        chat_monitor_factory: ChatMonitorFactory | None = None,
        pipeline_factory: PipelineFactory | None = None,
        extractor_process_factory: ExtractorProcessFactory | None = None,
        on_extracted: Callable[[ClipExtractionResult], None] | None = None,
        on_state_change: StateChangeCallback | None = None,
        platform: str = "twitch",
        video_id: str | None = None,
        live_chat_id: str | None = None,
    ) -> None:
        clean_channel = channel.lstrip("#").strip() if platform == "youtube" else normalize_channel(channel)
        if not clean_channel:
            raise ValueError("channel must be a non-empty Twitch login or YouTube identifier")
        if clip_queue_max_size < 1:
            raise ValueError("clip_queue_max_size must be >= 1")

        self._channel = clean_channel
        self._platform = platform
        self._video_id = video_id
        self._live_chat_id = live_chat_id
        self._settings = settings
        self._submit_to_analysis = submit_to_analysis
        self._router = router
        self._extraction_semaphore = extraction_semaphore
        self._clip_queue_max_size = clip_queue_max_size
        self._base_recording_config = recording_config
        self._print_messages = print_messages
        self._streamlink_bin = streamlink_bin
        self._ffmpeg_bin = ffmpeg_bin
        self._cleanup_interval = cleanup_interval_seconds
        self._trailing_wait = trailing_wait_seconds
        self._chat_monitor_factory = chat_monitor_factory or _default_chat_monitor_factory
        self._pipeline_factory = pipeline_factory
        self._extractor_process_factory = extractor_process_factory
        self._on_extracted = on_extracted
        self._on_state_change = on_state_change

        # Lifecycle state & synchronization.
        self._state: SessionState = SessionState.STOPPED
        self._lifecycle_lock = asyncio.Lock()
        self._stop_requested = False
        self._accepting_results = False
        self._run_id: str | None = None
        self._start_count: int = 0
        self._last_error: BaseException | None = None
        self._error_message: str | None = None

        # Active per-run components (recreated fresh on every start/restart).
        self._detector: StreamHighlightDetector | None = None
        self._chat_monitor: Any | None = None
        self._chat_task: asyncio.Task[None] | None = None
        self._buffer_manager: RollingBufferManager | None = None
        self._recorder: StreamRecorder | None = None
        self._planner: ClipWindowPlanner | None = None
        self._extractor: ClipExtractor | None = None
        self._clip_worker: ClipExtractionWorker | None = None
        self._pin_sink: Stage5PinAndSubmitSink | None = None
        self._buffer_run_dir: Path | None = None
        self._recording_active: bool = False

    # -- inspectable properties ------------------------------------------------

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state == SessionState.RUNNING

    @property
    def run_id(self) -> str | None:
        return self._run_id

    @property
    def recording_enabled(self) -> bool:
        """Whether live recording is active for the current/configured run."""
        return self._recording_active

    @property
    def recording_session_id(self) -> int:
        return self._recorder.session_id if self._recorder is not None else 0

    @property
    def start_count(self) -> int:
        return self._start_count

    @property
    def last_error(self) -> BaseException | None:
        return self._last_error

    @property
    def error_message(self) -> str | None:
        return self._error_message

    @property
    def buffer_run_dir(self) -> Path | None:
        return self._buffer_run_dir

    @property
    def platform(self) -> str:
        return self._platform

    @property
    def clips_channel_dir(self) -> Path:
        base = (
            self._base_recording_config.clips_output_dir
            if self._base_recording_config is not None
            and self._base_recording_config.clips_output_dir is not None
            else self._settings.clips_output_dir
        )
        if self._platform == "youtube":
            return Path(base) / "youtube" / self._channel
        return Path(base) / self._channel

    @property
    def detector(self) -> StreamHighlightDetector | None:
        return self._detector

    @property
    def chat_monitor(self) -> Any | None:
        return self._chat_monitor

    @property
    def buffer_manager(self) -> RollingBufferManager | None:
        return self._buffer_manager

    @property
    def segment_index(self) -> SegmentIndex | None:
        return self._buffer_manager.index if self._buffer_manager is not None else None

    @property
    def recorder(self) -> StreamRecorder | None:
        return self._recorder

    @property
    def clip_worker(self) -> ClipExtractionWorker | None:
        return self._clip_worker

    def snapshot(self) -> SessionSnapshot:
        """Return an immutable point-in-time status snapshot for monitoring."""
        mgr = self._buffer_manager
        cw = self._clip_worker
        rec = self._recorder
        return SessionSnapshot(
            channel=self._channel,
            state=self._state,
            run_id=self._run_id,
            recording_enabled=self._recording_active,
            recording_session_id=rec.session_id if rec is not None else 0,
            start_count=self._start_count,
            segments_registered=rec.segments_registered if rec is not None else 0,
            active_pins=len(mgr.index.active_pins) if mgr is not None else 0,
            buffer_bytes=mgr.index.total_bytes if mgr is not None else 0,
            clips_extracted=cw.extracted_count if cw is not None else 0,
            clips_failed=cw.failed_count if cw is not None else 0,
            clips_dropped=cw.dropped_count if cw is not None else 0,
            error_message=self._error_message,
        )

    # -- router target hooks ---------------------------------------------------

    def handle_analysis_result(self, result: AnalyzedHighlight) -> bool:
        """Receive a routed `AnalyzedHighlight` from `StreamRoutingAnalysisSink`.

        Returns True if handled by this session, False if ignored because the
        session is already closed. Never raises.
        """
        if not self._accepting_results:
            log.info(
                "[%s] Ignoring late Gemini verdict for stopped session (event=%s status=%s).",
                self._channel,
                result.event_id[:8],
                result.status.value,
            )
            return False

        cw = self._clip_worker
        if cw is not None:
            cw.submit(result)
        return True

    def handle_dropped_event(self, event: HighlightEvent, reason: str) -> None:
        """Release any rolling-buffer pin when Gemini drops or cancels `event`."""
        mgr = self._buffer_manager
        if mgr is not None:
            mgr.unpin_event(event.event_id)
            log.info(
                "[%s] Unpinned rolling buffer for dropped Gemini event %s (%s).",
                self._channel,
                event.event_id[:8],
                reason,
            )

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> bool:
        """Start this streamer's session (idempotent when already running).

        Returns True if the session is running, False if startup failed
        (state transitions to `ERRORED` without propagating the failure to
        sibling sessions).
        """
        async with self._lifecycle_lock:
            if self._state == SessionState.RUNNING:
                return True

            self._stop_requested = False
            self._last_error = None
            self._error_message = None

            run_id = _new_run_id()
            self._run_id = run_id

            try:
                self._build_components(run_id)

                # Register route BEFORE starting chat so no verdict arrives
                # before the session is routable.
                self._accepting_results = True
                if self._router is not None:
                    self._router.register(self._channel, self)

                if self._buffer_manager is not None:
                    await self._buffer_manager.start_cleanup_loop(
                        interval_seconds=self._cleanup_interval
                    )
                if self._clip_worker is not None:
                    await self._clip_worker.start()
                if self._recorder is not None:
                    await self._recorder.start()

                self._start_count += 1
                self._set_state(SessionState.RUNNING)

                assert self._chat_monitor is not None
                self._chat_task = asyncio.create_task(
                    self._run_chat_supervised(self._chat_monitor, run_id),
                    name=f"stream-chat-{self._channel}",
                )

                log.info(
                    "[%s] StreamSession started (run_id=%s, recording=%s, start_count=%d).",
                    self._channel,
                    run_id,
                    self._recording_active,
                    self._start_count,
                )
                return True
            except Exception as exc:  # noqa: BLE001  isolate session startup failures
                self._last_error = exc
                self._error_message = str(exc) or exc.__class__.__name__
                log.error(
                    "[%s] StreamSession failed to start (isolated): %s",
                    self._channel,
                    self._error_message,
                )
                await self._teardown_components(
                    clip_drain_timeout=0.0,
                    cleanup_buffer=True,
                    stop_chat=False,
                )
                self._set_state(SessionState.ERRORED)
                return False
            except BaseException:
                await self._teardown_components(
                    clip_drain_timeout=0.0,
                    cleanup_buffer=True,
                    stop_chat=False,
                )
                self._set_state(SessionState.STOPPED)
                raise

    async def stop_chat(self, *, timeout: float = 5.0) -> None:
        """Stop ONLY chat ingestion while keeping routing & clip extraction open.

        Used during multi-stream graceful shutdown so the shared Gemini worker
        can finish draining and route verdicts to this session's open
        `ClipExtractionWorker` before `stop()` shuts down recording & clipping.
        """
        self._stop_requested = True
        monitor = self._chat_monitor
        if monitor is not None:
            aclose = getattr(monitor, "aclose", None)
            if callable(aclose):
                try:
                    await aclose()
                except Exception:  # noqa: BLE001
                    log.exception("[%s] Error closing ChatMonitor.", self._channel)
            else:
                stop_fn = getattr(monitor, "stop", None)
                if callable(stop_fn):
                    stop_fn()

        chat_task = self._chat_task
        current = asyncio.current_task()
        if chat_task is not None and chat_task is not current and not chat_task.done():
            try:
                done, _ = await asyncio.wait({chat_task}, timeout=timeout)
                if not done:
                    log.warning(
                        "[%s] Chat task did not exit within %.1fs; cancelling.",
                        self._channel,
                        timeout,
                    )
                    chat_task.cancel()
                    await asyncio.gather(chat_task, return_exceptions=True)
            except asyncio.CancelledError:
                chat_task.cancel()
                await asyncio.gather(chat_task, return_exceptions=True)
                raise

    async def stop(
        self,
        *,
        clip_drain_timeout: float = 30.0,
        chat_stop_timeout: float = 5.0,
        cleanup_buffer: bool = True,
    ) -> None:
        """Gracefully stop this session and clean up all of its resources.

        Safe to call repeatedly or from any state (`RUNNING`, `STOPPED`, `ERRORED`).
        """
        self._stop_requested = True
        async with self._lifecycle_lock:
            if self._state == SessionState.STOPPED and self._chat_task is None:
                return

            if self._state != SessionState.STOPPED:
                self._set_state(SessionState.STOPPING)

            try:
                await self._teardown_components(
                    clip_drain_timeout=clip_drain_timeout,
                    chat_stop_timeout=chat_stop_timeout,
                    cleanup_buffer=cleanup_buffer,
                    stop_chat=True,
                )
            finally:
                self._set_state(SessionState.STOPPED)

            log.info("[%s] StreamSession stopped.", self._channel)

    async def restart(
        self,
        *,
        clip_drain_timeout: float = 30.0,
        cleanup_buffer: bool = True,
    ) -> bool:
        """Stop this session cleanly and start a fresh run with a new `run_id`."""
        await self.stop(
            clip_drain_timeout=clip_drain_timeout,
            cleanup_buffer=cleanup_buffer,
        )
        return await self.start()

    # -- internals -------------------------------------------------------------

    def _build_components(self, run_id: str) -> None:
        """Construct fresh per-run detector, chat monitor, and optional recording stack."""
        want_recording = self._settings.recording_enabled
        if want_recording and self._submit_to_analysis is None:
            log.warning(
                "[%s] RECORDING_ENABLED=true requires Gemini analysis; running "
                "detection-only for this session.",
                self._channel,
            )
            want_recording = False

        self._recording_active = want_recording

        if want_recording:
            rec_config = (
                self._base_recording_config
                if self._base_recording_config is not None
                else build_recording_config(self._settings)
            )
            recorder_box: list[StreamRecorder] = []

            def _on_emergency_stop(reason: str) -> None:
                if recorder_box:
                    recorder_box[0].request_emergency_stop(reason)

            buffer_manager = RollingBufferManager(
                rec_config,
                on_emergency_stop=_on_emergency_stop,
            )
            recorder = StreamRecorder(
                channel=self._channel,
                config=rec_config,
                buffer_manager=buffer_manager,
                run_id=run_id,
                streamlink_bin=self._streamlink_bin,
                ffmpeg_bin=self._ffmpeg_bin,
                pipeline_factory=self._pipeline_factory,
                platform=self._platform,
                video_id=self._video_id,
            )
            recorder_box.append(recorder)

            planner = ClipWindowPlanner(
                rec_config,
                event_post_seconds=self._settings.event_post_seconds,
                max_event_seconds=self._settings.max_event_seconds,
            )
            extractor = ClipExtractor(
                rec_config,
                ffmpeg_bin=self._ffmpeg_bin,
                process_factory=self._extractor_process_factory,
            )
            clip_worker = ClipExtractionWorker(
                extractor,
                buffer_manager,
                queue_max_size=self._clip_queue_max_size,
                extraction_semaphore=self._extraction_semaphore,
                trailing_wait_seconds=self._trailing_wait,
                on_extracted=self._on_extracted,
                platform=self._platform,
            )

            assert self._submit_to_analysis is not None
            pin_sink = Stage5PinAndSubmitSink(
                submit_to_analysis=self._submit_to_analysis,
                planner=planner,
                buffer_manager=buffer_manager,
            )
            detector_sink: HighlightSink = CompositeHighlightSink(logging_sink, pin_sink)

            self._buffer_manager = buffer_manager
            self._recorder = recorder
            self._planner = planner
            self._extractor = extractor
            self._clip_worker = clip_worker
            self._pin_sink = pin_sink
            self._buffer_run_dir = recorder.channel_dir
        else:
            self._buffer_manager = None
            self._recorder = None
            self._planner = None
            self._extractor = None
            self._clip_worker = None
            self._pin_sink = None
            self._buffer_run_dir = None

            if self._submit_to_analysis is not None:
                detector_sink = CompositeHighlightSink(
                    logging_sink, self._submit_to_analysis
                )
            else:
                detector_sink = logging_sink

        highlight_cfg = build_highlight_config(self._settings)
        self._detector = StreamHighlightDetector(
            stream_id=self._channel,
            streamer_name=self._channel,
            config=highlight_cfg,
            sink=detector_sink,
        )
        if self._platform == "youtube" and self._live_chat_id and self._chat_monitor_factory == _default_chat_monitor_factory:
            from app.chat.youtube_chat import YouTubeChatMonitor
            from app.shorts.youtube_auth import YouTubeTokenManager

            token_mgr = None
            if self._settings.youtube_tokens_path and self._settings.youtube_tokens_path.is_file():
                token_mgr = YouTubeTokenManager(
                    tokens_path=self._settings.youtube_tokens_path,
                    client_id=self._settings.youtube_client_id,
                    client_secret=self._settings.youtube_client_secret,
                )
            self._chat_monitor = YouTubeChatMonitor(
                live_chat_id=self._live_chat_id,
                detector=self._detector,
                token_manager=token_mgr,
                poll_interval=self._settings.youtube_chat_poll_interval,
            )
        else:
            self._chat_monitor = self._chat_monitor_factory(
                self._settings,
                self._channel,
                self._detector,
                self._print_messages,
            )

    async def _run_chat_supervised(self, monitor: Any, expected_run_id: str) -> None:
        """Run `monitor.run()` with strict per-session failure isolation."""
        runtime_error: BaseException | None = None
        try:
            await monitor.run()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001  isolate per-session crash
            runtime_error = exc
            log.exception(
                "[%s] StreamSession chat crashed (isolated from other streams): %s",
                self._channel,
                exc,
            )

        # If an explicit stop()/stop_chat() already initiated teardown, do not
        # contend on _lifecycle_lock — let the caller finish tearing down.
        if self._stop_requested:
            if runtime_error is not None:
                self._last_error = runtime_error
                self._error_message = str(runtime_error) or runtime_error.__class__.__name__
            return

        # Unexpected runtime exit or crash: clean up THIS session independently.
        async with self._lifecycle_lock:
            if self._run_id != expected_run_id:
                return
            if self._state in (SessionState.STOPPED, SessionState.ERRORED):
                return

            self._set_state(SessionState.STOPPING)
            if runtime_error is not None:
                self._last_error = runtime_error
                self._error_message = (
                    str(runtime_error) or runtime_error.__class__.__name__
                )

            try:
                await self._teardown_components(
                    clip_drain_timeout=30.0 if runtime_error is None else 5.0,
                    cleanup_buffer=True,
                    stop_chat=False,
                )
            finally:
                final_state = (
                    SessionState.ERRORED
                    if runtime_error is not None
                    else SessionState.STOPPED
                )
                self._set_state(final_state)

    async def _teardown_components(
        self,
        *,
        clip_drain_timeout: float,
        chat_stop_timeout: float = 5.0,
        cleanup_buffer: bool,
        stop_chat: bool,
    ) -> None:
        """Shut down this session's components in safe dependency order."""
        # 1) Stop chat ingestion first so no new highlights are emitted.
        if stop_chat:
            await self.stop_chat(timeout=chat_stop_timeout)

        # 2) Close routing intake so any late verdicts are cleanly discarded.
        self._accepting_results = False
        if self._router is not None:
            self._router.unregister(self._channel, expected=self)

        # 3) Drain active clip extractions while segments remain pinned.
        clip_worker = self._clip_worker
        if clip_worker is not None:
            try:
                await clip_worker.stop(drain_timeout=clip_drain_timeout)
            except Exception:  # noqa: BLE001
                log.exception("[%s] Error stopping ClipExtractionWorker.", self._channel)

        # 4) Stop live recorder (terminates Streamlink + FFmpeg process groups).
        recorder = self._recorder
        if recorder is not None:
            try:
                await recorder.stop()
            except Exception:  # noqa: BLE001
                log.exception("[%s] Error stopping StreamRecorder.", self._channel)

        # 5) Stop rolling-buffer cleanup loop & purge ONLY this run's unpinned files.
        buffer_manager = self._buffer_manager
        run_dir = self._buffer_run_dir
        if buffer_manager is not None:
            try:
                await buffer_manager.stop_cleanup_loop()
            except Exception:  # noqa: BLE001
                log.exception("[%s] Error stopping RollingBufferManager.", self._channel)
            if cleanup_buffer:
                self._cleanup_run_buffer_dir(buffer_manager, run_dir)

        self._chat_task = None

    @staticmethod
    def _cleanup_run_buffer_dir(
        buffer_manager: RollingBufferManager, run_dir: Path | None
    ) -> None:
        """Purge unpinned segments, remove finished CSVs, and prune empty run dirs."""
        buffer_manager.purge_unpinned_files()
        if run_dir is None or not run_dir.exists():
            return

        for csv_file in run_dir.glob("s*_segments.csv"):
            try:
                csv_file.unlink(missing_ok=True)
            except OSError:
                pass

        # Remove run_dir (and parent channel dir) ONLY if empty so preserved
        # pinned files or concurrent runs are never touched.
        for candidate in (run_dir, run_dir.parent):
            try:
                candidate.rmdir()
            except OSError:
                break

    def _set_state(self, new_state: SessionState) -> None:
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        if self._on_state_change is not None:
            try:
                self._on_state_change(self, old_state, new_state)
            except Exception:  # noqa: BLE001  observer faults must not break session
                log.exception(
                    "[%s] on_state_change callback raised (%s -> %s).",
                    self._channel,
                    old_state.value,
                    new_state.value,
                )
