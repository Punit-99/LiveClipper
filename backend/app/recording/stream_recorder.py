"""Stage 5.3: Async Streamlink -> FFmpeg live rolling-segment recorder.

Pipeline (verified in Step 5.0):
    streamlink [--twitch-low-latency] --stdout https://www.twitch.tv/<channel> <quality>
      | (OS kernel pipe — zero Python byte-copying overhead)
    ffmpeg -hide_banner -loglevel warning -i pipe:0 -c copy
           -f stream_segment -segment_time <sec> -reset_timestamps 1
           -segment_list <s{sid}_segments.csv> -segment_list_type csv
           <s{sid}_seg_%05d.ts>

Design highlights:
- Runs fully asynchronously alongside Twitch IRC and Gemini without blocking.
- Watches FFmpeg's CSV segment list and registers each finalized `.ts` segment
  into `RollingBufferManager` with separate `wall_closed`, `mono_closed`,
  `media_duration`, `ffmpeg_start_pts`, `ffmpeg_end_pts`, and `stat().st_size`.
- Uses per-session filenames (`s{session_id}_seg_%05d.ts` and
  `s{session_id}_segments.csv`) and increments `session_id` on every restart so
  restarts never overwrite prior segments and discontinuities are tracked.
- Auto-restarts with exponential backoff on stream drop, process crash, or
  stalled pipe (no finalized segment within stall timeout).
- Honors `RollingBufferManager` emergency shutdown (`request_emergency_stop`):
  stops recording cleanly without restarting and never deletes approved pins.
- Graceful shutdown terminates Streamlink first so FFmpeg sees EOF and flushes
  its final segment cleanly, with terminate/kill fallbacks to prevent orphans.
"""

from __future__ import annotations

import asyncio
import csv
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from app.logging_setup import get_logger
from app.recording.buffer_manager import BufferCapacityError, RollingBufferManager
from app.recording.models import RecordingConfig, VideoSegment

log = get_logger(__name__)


class RecorderDependencyError(RuntimeError):
    """Raised when required external binaries (`streamlink` or `ffmpeg`) are missing."""


class SubprocessHandle(Protocol):
    """Minimal async subprocess interface (satisfied by `asyncio.subprocess.Process`)."""

    @property
    def returncode(self) -> int | None: ...

    @property
    def stderr(self) -> asyncio.StreamReader | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


@dataclass
class PipelineProcesses:
    """Pair of child process handles for one Streamlink -> FFmpeg session."""

    streamlink: SubprocessHandle
    ffmpeg: SubprocessHandle


PipelineFactory = Callable[[list[str], list[str]], Awaitable[PipelineProcesses]]


# -- binary discovery & verified command builders ------------------------------


def find_streamlink_binary() -> str:
    """Locate `streamlink`, preferring the active Python environment's sibling bin/."""
    # Check sys.executable's directory WITHOUT resolving symlinks first so venv/bin wins.
    venv_candidate = Path(sys.executable).parent / "streamlink"
    if venv_candidate.is_file() and os.access(venv_candidate, os.X_OK):
        return str(venv_candidate)

    resolved_candidate = Path(sys.executable).resolve().parent / "streamlink"
    if resolved_candidate.is_file() and os.access(resolved_candidate, os.X_OK):
        return str(resolved_candidate)

    on_path = shutil.which("streamlink")
    if on_path:
        return on_path

    for fallback in ("/opt/homebrew/bin/streamlink", "/usr/local/bin/streamlink"):
        if Path(fallback).is_file() and os.access(fallback, os.X_OK):
            return fallback

    raise RecorderDependencyError(
        "Streamlink binary not found. Install it into your backend virtual environment:\n"
        "    ./venv/bin/pip install streamlink"
    )


def find_ffmpeg_binary() -> str:
    """Locate `ffmpeg` in PATH or standard macOS Homebrew locations."""
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    for fallback in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(fallback).is_file() and os.access(fallback, os.X_OK):
            return fallback

    raise RecorderDependencyError(
        "FFmpeg binary not found. Install it before enabling live recording:\n"
        "    brew install ffmpeg"
    )


def _resolve_youtube_stream_url(clean_channel: str, video_id: str | None = None) -> str:
    """Resolve YouTube channel identifier, handle, or video ID to a Streamlink-compatible URL."""
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    if clean_channel.startswith(("http://", "https://")):
        return clean_channel
    if clean_channel.startswith("UC"):
        return f"https://www.youtube.com/channel/{clean_channel}/live"
    if clean_channel.startswith("@"):
        return f"https://www.youtube.com/{clean_channel}/live"

    # Try mapping human-readable channel name to known channel ID
    try:
        from app.discovery.youtube_finder import KNOWN_CHANNEL_NAMES
        for cid, name in KNOWN_CHANNEL_NAMES.items():
            if name.lower() == clean_channel.lower():
                return f"https://www.youtube.com/channel/{cid}/live"
    except ImportError:
        pass

    # Check if channel looks like a standalone 11-char video ID (e.g. dQw4w9WgXcQ)
    if len(clean_channel) == 11 and not any(c in clean_channel for c in " /@:."):
        return f"https://www.youtube.com/watch?v={clean_channel}"

    # Default to channel handle URL (e.g. https://www.youtube.com/@IShowSpeed/live)
    return f"https://www.youtube.com/@{clean_channel.lstrip('@')}/live"


def build_streamlink_command(
    streamlink_bin: str,
    channel: str,
    config: RecordingConfig,
    platform: str = "youtube",
    video_id: str | None = None,
) -> list[str]:
    """Build the verified Streamlink CLI command for YouTube live streams."""
    clean_channel = channel.lstrip("#").strip()
    if not clean_channel:
        raise ValueError("channel must be a non-empty identifier")

    cmd = [streamlink_bin]
    stream_url = _resolve_youtube_stream_url(clean_channel, video_id)
    cmd.extend(["--stdout", stream_url, config.quality])
    return cmd


def build_ffmpeg_segment_command(
    ffmpeg_bin: str,
    buffer_dir: Path,
    session_id: int,
    config: RecordingConfig,
) -> tuple[list[str], Path]:
    """Build the verified FFmpeg rolling-segment command and its per-session CSV path."""
    if session_id < 1:
        raise ValueError("session_id must be >= 1")

    csv_path = buffer_dir / f"s{session_id}_segments.csv"
    seg_pattern = buffer_dir / f"s{session_id}_seg_%05d.ts"
    # Format segment_seconds cleanly (e.g. "4" instead of "4.0" when integral).
    seg_time = (
        str(int(config.segment_seconds))
        if float(config.segment_seconds).is_integer()
        else str(config.segment_seconds)
    )
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        "-f",
        "stream_segment",
        "-segment_time",
        seg_time,
        "-reset_timestamps",
        "1",
        "-segment_list",
        str(csv_path),
        "-segment_list_type",
        "csv",
        str(seg_pattern),
    ]
    return cmd, csv_path


def _signal_process_tree(proc: SubprocessHandle, sig: int) -> None:
    """Send `sig` to `proc`'s POSIX process group when available, else fallback to handle."""
    if proc.returncode is not None:
        return
    pid = getattr(proc, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, sig)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
    try:
        if sig == signal.SIGKILL:
            proc.kill()
        else:
            proc.terminate()
    except ProcessLookupError:
        pass


async def _default_spawn_pipeline(
    sl_cmd: list[str], ff_cmd: list[str]
) -> PipelineProcesses:
    """Spawn Streamlink -> FFmpeg connected via an OS kernel pipe.

    Each child starts in its own POSIX session (`start_new_session=True`) so
    terminal Ctrl+C does not kill children before Python's graceful drain runs,
    and `_signal_process_tree` can cleanly terminate each child's entire
    process group with zero orphans.
    """
    read_fd, write_fd = os.pipe()
    sl_proc: asyncio.subprocess.Process | None = None
    ff_proc: asyncio.subprocess.Process | None = None
    new_session = os.name == "posix"
    try:
        sl_proc = await asyncio.create_subprocess_exec(
            *sl_cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=write_fd,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=new_session,
        )
        ff_proc = await asyncio.create_subprocess_exec(
            *ff_cmd,
            stdin=read_fd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=new_session,
        )
        return PipelineProcesses(streamlink=sl_proc, ffmpeg=ff_proc)
    except BaseException:
        for proc in (sl_proc, ff_proc):
            if proc is not None and proc.returncode is None:
                _signal_process_tree(proc, signal.SIGKILL)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
        raise
    finally:
        # Close parent's copies of pipe FDs immediately so EOF propagates cleanly.
        for fd in (read_fd, write_fd):
            try:
                os.close(fd)
            except OSError:
                pass


# -- recorder ------------------------------------------------------------------


class StreamRecorder:
    """Async live rolling-segment recorder feeding `RollingBufferManager`."""

    def __init__(
        self,
        channel: str,
        config: RecordingConfig,
        buffer_manager: RollingBufferManager,
        *,
        buffer_dir: Path | None = None,
        run_id: str | None = None,
        streamlink_bin: str | None = None,
        ffmpeg_bin: str | None = None,
        pipeline_factory: PipelineFactory | None = None,
        wall_clock: Callable[[], float] = time.time,
        mono_clock: Callable[[], float] = time.monotonic,
        poll_interval_seconds: float = 0.2,
        startup_timeout_seconds: float = 45.0,
        stall_timeout_seconds: float = 90.0,
        initial_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 30.0,
        shutdown_grace_seconds: float = 4.0,
        platform: str = "youtube",
        video_id: str | None = None,
    ) -> None:
        clean_channel = channel.lstrip("#").strip()
        if not clean_channel:
            raise ValueError("channel must be a non-empty identifier")

        self._channel = clean_channel
        self._platform = platform
        self._video_id = video_id
        self._config = config
        self._buffer_manager = buffer_manager

        base_dir = buffer_dir if buffer_dir is not None else config.buffer_dir
        if base_dir is None:
            raise ValueError("buffer_dir must be set on RecordingConfig or passed explicitly")
        self._channel_dir = (
            Path(base_dir) / clean_channel / run_id
            if run_id
            else Path(base_dir) / clean_channel
        )

        self._streamlink_bin = streamlink_bin
        self._ffmpeg_bin = ffmpeg_bin
        self._pipeline_factory = pipeline_factory or _default_spawn_pipeline
        self._last_streamlink_error: str | None = None

        self._wall_clock = wall_clock
        self._mono_clock = mono_clock
        self._poll_interval = max(0.01, poll_interval_seconds)
        self._startup_timeout = max(0.05, startup_timeout_seconds)
        self._stall_timeout = max(0.05, stall_timeout_seconds)
        self._initial_backoff = max(0.0, initial_backoff_seconds)
        self._max_backoff = max(self._initial_backoff, max_backoff_seconds)
        self._shutdown_grace = max(0.1, shutdown_grace_seconds)

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._active_procs: PipelineProcesses | None = None

        self._stop_requested = False
        self._emergency_stopped = False
        self._emergency_reason: str | None = None
        self._session_id = 0

        # Operational counters (useful for logging and tests).
        self.segments_registered: int = 0
        self.restart_count: int = 0
        self.stall_count: int = 0

    # -- properties ------------------------------------------------------------

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def channel_dir(self) -> Path:
        return self._channel_dir

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def emergency_stopped(self) -> bool:
        return self._emergency_stopped

    # -- lifecycle -------------------------------------------------------------

    def _resolve_binaries(self) -> tuple[str, str]:
        sl = self._streamlink_bin or find_streamlink_binary()
        ff = self._ffmpeg_bin or find_ffmpeg_binary()
        self._streamlink_bin = sl
        self._ffmpeg_bin = ff
        return sl, ff

    async def start(self) -> None:
        """Validate binaries and start the background recording supervision loop."""
        if self.is_running:
            return

        # Fail fast with actionable messages before launching background tasks.
        self._resolve_binaries()
        self._channel_dir.mkdir(parents=True, exist_ok=True)

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        self._stop_requested = False
        if self._buffer_manager.emergency_stop_triggered:
            self._emergency_stopped = True
            self._stop_requested = True
            self._stop_event.set()
            log.error(
                "Not starting StreamRecorder for #%s: buffer already in emergency shutdown.",
                self._channel,
            )
            return

        self._task = asyncio.create_task(
            self._supervise_loop(),
            name=f"stream-recorder-{self._channel}",
        )
        log.info(
            "StreamRecorder started for #%s (quality=%s, low_latency=%s, "
            "segment=%ss, buffer_dir=%s).",
            self._channel,
            self._config.quality,
            self._config.low_latency,
            self._config.segment_seconds,
            self._channel_dir,
        )

    async def stop(self) -> None:
        """Stop recording gracefully, flushing final segment and leaving zero orphans."""
        self._stop_requested = True
        if self._stop_event is not None:
            self._stop_event.set()

        task = self._task
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=self._shutdown_grace + 3.0)
            except asyncio.TimeoutError:
                log.warning(
                    "StreamRecorder #%s stop timed out; cancelling supervision task.",
                    self._channel,
                )
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            finally:
                self._task = None

        # Safety net: ensure no child processes linger.
        if self._active_procs is not None:
            await self._terminate_pipeline(self._active_procs)
            self._active_procs = None

        log.info(
            "StreamRecorder stopped for #%s (sessions=%d, segments=%d, restarts=%d, stalls=%d).",
            self._channel,
            self._session_id,
            self.segments_registered,
            self.restart_count,
            self.stall_count,
        )

    def request_emergency_stop(self, reason: str) -> None:
        """Thread-safe callback for `RollingBufferManager(on_emergency_stop=...)`.

        Signals the recorder to stop cleanly and never restart. Does NOT call
        back into `RollingBufferManager` synchronously (avoids lock re-entry).
        """
        self._emergency_stopped = True
        self._emergency_reason = reason
        self._stop_requested = True
        log.error(
            "StreamRecorder #%s received emergency stop signal: %s",
            self._channel,
            reason,
        )
        loop = self._loop
        stop_ev = self._stop_event
        if loop is not None and stop_ev is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(stop_ev.set)
            except RuntimeError:
                pass

    # -- supervision & single-session loop -------------------------------------

    async def _supervise_loop(self) -> None:
        backoff = self._initial_backoff
        while not self._stop_requested:
            if self._buffer_manager.emergency_stop_triggered or self._emergency_stopped:
                self._stop_requested = True
                break

            self._session_id += 1
            sid = self._session_id
            produced = 0
            try:
                produced = await self._run_session(sid)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  isolate unexpected recorder faults
                log.exception("StreamRecorder #%s session %d failed unexpectedly.", self._channel, sid)

            if self._stop_requested or self._emergency_stopped or self._buffer_manager.emergency_stop_triggered:
                break

            if produced > 0:
                backoff = self._initial_backoff

            self.restart_count += 1
            log.warning(
                "StreamRecorder #%s session %d ended (segments=%d); restarting in %.1fs...",
                self._channel,
                sid,
                produced,
                backoff,
            )
            await self._interruptible_sleep(backoff)
            backoff = min(max(backoff * 2.0, self._initial_backoff), self._max_backoff)

    async def _run_session(self, session_id: int) -> int:
        sl_bin, ff_bin = self._resolve_binaries()
        sl_cmd = build_streamlink_command(
            sl_bin, self._channel, self._config, platform=self._platform, video_id=self._video_id
        )
        ff_cmd, csv_path = build_ffmpeg_segment_command(
            ff_bin, self._channel_dir, session_id, self._config
        )

        log.info("Starting recording session %d for #%s...", session_id, self._channel)
        procs = await self._pipeline_factory(sl_cmd, ff_cmd)
        self._active_procs = procs

        # Drain child stderr asynchronously so pipe buffers can never deadlock.
        stderr_tasks = [
            asyncio.create_task(self._drain_stderr(procs.streamlink, f"streamlink[s{session_id}]")),
            asyncio.create_task(self._drain_stderr(procs.ffmpeg, f"ffmpeg[s{session_id}]")),
        ]

        seen_rows = 0
        session_segments = 0
        session_start_mono = self._mono_clock()
        last_progress_mono = session_start_mono

        try:
            while not self._stop_requested:
                if self._buffer_manager.emergency_stop_triggered or self._emergency_stopped:
                    self._stop_requested = True
                    break

                newly_registered, seen_rows = self._ingest_csv_updates(
                    csv_path, session_id, seen_rows
                )
                if newly_registered > 0:
                    session_segments += newly_registered
                    last_progress_mono = self._mono_clock()
                    if self._stop_requested:
                        break

                # Detect child exits. Check Streamlink first because if Streamlink exits,
                # FFmpeg will immediately see EOF on pipe:0 and exit with rc=183 (AVERROR_INVALIDDATA).
                if procs.streamlink.returncode is not None:
                    if session_segments == 0 and procs.streamlink.returncode != 0:
                        err_detail = getattr(self, "_last_streamlink_error", None) or f"rc={procs.streamlink.returncode}"
                        log.warning(
                            "Streamlink exited before stream began in session %d (%s).",
                            session_id,
                            err_detail,
                        )
                    else:
                        log.warning(
                            "Streamlink exited in session %d (rc=%s); waiting briefly for FFmpeg flush.",
                            session_id,
                            procs.streamlink.returncode,
                        )
                    # Give FFmpeg a brief moment to finish writing its final segment.
                    await self._wait_for_ffmpeg_flush(procs.ffmpeg, timeout=self._shutdown_grace)
                    newly_registered, seen_rows = self._ingest_csv_updates(
                        csv_path, session_id, seen_rows
                    )
                    session_segments += newly_registered
                    break

                if procs.ffmpeg.returncode is not None:
                    if procs.ffmpeg.returncode == 183 and session_segments == 0:
                        log.warning(
                            "FFmpeg exited in session %d (rc=183: empty/invalid stream input from pipe:0).",
                            session_id,
                        )
                    else:
                        log.warning(
                            "FFmpeg exited in session %d (rc=%s).",
                            session_id,
                            procs.ffmpeg.returncode,
                        )
                    break

                # Stall detection using monotonic clock.
                now_mono = self._mono_clock()
                limit = self._startup_timeout if session_segments == 0 else self._stall_timeout
                if (now_mono - last_progress_mono) >= limit:
                    self.stall_count += 1
                    log.warning(
                        "StreamRecorder #%s session %d stalled (no segment for %.1fs; limit=%.1fs).",
                        self._channel,
                        session_id,
                        now_mono - last_progress_mono,
                        limit,
                    )
                    break

                await self._interruptible_sleep(self._poll_interval)
        finally:
            try:
                try:
                    await self._terminate_pipeline(procs)
                except asyncio.CancelledError:
                    # Hard cancellation during teardown: force-kill both process trees
                    # synchronously and reap briefly without leaving detached tasks.
                    _signal_process_tree(procs.streamlink, signal.SIGKILL)
                    _signal_process_tree(procs.ffmpeg, signal.SIGKILL)
                    for proc in (procs.streamlink, procs.ffmpeg):
                        if proc.returncode is None:
                            try:
                                await asyncio.wait_for(proc.wait(), timeout=1.0)
                            except (asyncio.TimeoutError, asyncio.CancelledError):
                                pass
                    raise
            finally:
                self._active_procs = None
                for task in stderr_tasks:
                    task.cancel()
                if stderr_tasks:
                    try:
                        await asyncio.gather(*stderr_tasks, return_exceptions=True)
                    except asyncio.CancelledError:
                        pass

            # Final CSV sweep after FFmpeg flushes on clean shutdown (unless emergency-stopped).
            if not self._emergency_stopped and not self._buffer_manager.emergency_stop_triggered:
                extra, seen_rows = self._ingest_csv_updates(csv_path, session_id, seen_rows)
                session_segments += extra

        return session_segments

    def _ingest_csv_updates(
        self, csv_path: Path, session_id: int, seen_rows: int
    ) -> tuple[int, int]:
        """Parse newly appended CSV rows and register finalized segments."""
        if not csv_path.exists():
            return 0, seen_rows

        try:
            raw_lines = csv_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            log.warning("Could not read segment CSV %s: %s", csv_path.name, exc)
            return 0, seen_rows

        non_empty = [ln for ln in raw_lines if ln.strip()]
        if len(non_empty) <= seen_rows:
            return 0, seen_rows

        registered = 0
        for line in non_empty[seen_rows:]:
            seen_rows += 1
            parsed = self._parse_csv_row(line)
            if parsed is None:
                continue
            filename, start_pts, end_pts = parsed
            media_duration = end_pts - start_pts
            if media_duration <= 0:
                log.warning(
                    "Ignoring segment %r with non-positive CSV duration (%.4f..%.4f).",
                    filename,
                    start_pts,
                    end_pts,
                )
                continue

            seg_path = Path(filename)
            if not seg_path.is_absolute():
                seg_path = self._channel_dir / seg_path

            try:
                size_bytes = seg_path.stat().st_size
            except OSError as exc:
                log.warning("Skipping segment %s (stat failed: %s).", seg_path.name, exc)
                continue

            wall_closed = self._wall_clock()
            mono_closed = self._mono_clock()
            segment = VideoSegment(
                segment_id=seg_path.name,
                path=seg_path,
                session_id=session_id,
                wall_closed=wall_closed,
                mono_closed=mono_closed,
                media_duration=media_duration,
                ffmpeg_start_pts=start_pts,
                ffmpeg_end_pts=end_pts,
                size_bytes=size_bytes,
            )

            try:
                self._buffer_manager.register_segment(segment, now_mono=mono_closed)
            except BufferCapacityError as exc:
                log.error(
                    "Buffer rejected segment %s (%s); stopping recorder cleanly.",
                    segment.segment_id,
                    exc,
                )
                self._emergency_stopped = True
                self._stop_requested = True
                break

            registered += 1
            self.segments_registered += 1

        return registered, seen_rows

    @staticmethod
    def _parse_csv_row(line: str) -> tuple[str, float, float] | None:
        try:
            row = next(csv.reader([line]))
            if len(row) < 3:
                return None
            return row[0].strip(), float(row[1]), float(row[2])
        except (ValueError, StopIteration):
            return None

    async def _terminate_pipeline(self, procs: PipelineProcesses) -> None:
        """Terminate Streamlink's process tree first so FFmpeg flushes cleanly; kill fallback prevents orphans."""
        # 1) Stop Streamlink's process tree first so FFmpeg receives EOF on pipe:0.
        if procs.streamlink.returncode is None:
            _signal_process_tree(procs.streamlink, signal.SIGTERM)

        # Wait briefly for Streamlink to exit; force-kill process tree if stuck.
        await self._wait_or_kill(procs.streamlink, timeout=min(2.0, self._shutdown_grace))

        # 2) Give FFmpeg time to flush its final segment after EOF.
        exited = await self._wait_for_ffmpeg_flush(procs.ffmpeg, timeout=self._shutdown_grace)
        if not exited and procs.ffmpeg.returncode is None:
            _signal_process_tree(procs.ffmpeg, signal.SIGTERM)
            await self._wait_or_kill(procs.ffmpeg, timeout=min(2.0, self._shutdown_grace))

    @staticmethod
    async def _wait_for_ffmpeg_flush(proc: SubprocessHandle, *, timeout: float) -> bool:
        if proc.returncode is not None:
            return True
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    @staticmethod
    async def _wait_or_kill(proc: SubprocessHandle, *, timeout: float) -> None:
        if proc.returncode is not None:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            _signal_process_tree(proc, signal.SIGKILL)
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _drain_stderr(self, proc: SubprocessHandle, label: str) -> None:
        stream = proc.stderr
        if stream is None:
            return
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    if "streamlink" in label and ("error" in text.lower() or "warning" in text.lower()):
                        self._last_streamlink_error = text
                    log.debug("%s | %s", label, text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            pass

    async def _interruptible_sleep(self, seconds: float) -> None:
        if seconds <= 0 or self._stop_requested:
            return
        stop_ev = self._stop_event
        if stop_ev is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(stop_ev.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
