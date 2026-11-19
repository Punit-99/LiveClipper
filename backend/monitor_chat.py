"""Entry point: monitor ONE Twitch channel's chat (with optional Gemini & Stage 5 recording).

Usage (from the backend/ folder):
    python monitor_chat.py <channel>
    e.g.  python monitor_chat.py xqc

Pick a channel from `discover_streams.py`. Press Ctrl+C to stop.
Requires a prior login (python authorize.py).
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.analysis import (
    AnalysisSink,
    CompositeAnalysisSink,
    CompositeHighlightSink,
    GeminiHighlightAnalyzer,
    HighlightAnalysisWorker,
    JsonlAnalysisSink,
    build_analysis_config,
    logging_analysis_sink,
)
from app.auth.user_token import ReauthRequired, get_valid_access_token
from app.chat.twitch_chat import ChatMonitor
from app.config import ConfigError, Settings, load_settings
from app.detection import (
    HighlightSink,
    StreamHighlightDetector,
    build_highlight_config,
    logging_sink,
)
from app.logging_setup import get_logger
from app.recording import (
    ClipExtractionWorker,
    ClipExtractor,
    ClipWindowPlanner,
    RecorderDependencyError,
    RollingBufferManager,
    Stage5PinAndSubmitSink,
    StreamRecorder,
    build_recording_config,
    find_ffmpeg_binary,
    find_streamlink_binary,
)

log = get_logger("monitor_chat")


@dataclass
class _RecordingRuntime:
    """Bundled Stage 5 live recording, buffer, and clip extraction components."""

    buffer_manager: RollingBufferManager
    recorder: StreamRecorder
    planner: ClipWindowPlanner
    clip_worker: ClipExtractionWorker
    run_dir: Path


def _build_recording_runtime(
    settings: Settings,
    channel: str,
    *,
    gemini_active: bool,
    streamlink_bin: str | None = None,
    ffmpeg_bin: str | None = None,
) -> _RecordingRuntime | None:
    """Construct optional Stage 5 live video capture, buffer & clip extraction."""
    if not settings.recording_enabled:
        return None

    if not gemini_active:
        log.warning(
            "RECORDING_ENABLED=true requires Gemini analysis (GEMINI_ENABLED=true and "
            "GEMINI_API_KEY configured); skipping live recording and running detection-only."
        )
        return None

    rec_config = build_recording_config(settings)
    sl_bin = streamlink_bin or find_streamlink_binary()
    ff_bin = ffmpeg_bin or find_ffmpeg_binary()

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
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
        channel=channel,
        config=rec_config,
        buffer_manager=buffer_manager,
        run_id=run_id,
        streamlink_bin=sl_bin,
        ffmpeg_bin=ff_bin,
    )
    recorder_box.append(recorder)

    planner = ClipWindowPlanner(
        rec_config,
        event_post_seconds=settings.event_post_seconds,
        max_event_seconds=settings.max_event_seconds,
    )
    extractor = ClipExtractor(rec_config, ffmpeg_bin=ff_bin)
    clip_worker = ClipExtractionWorker(extractor, buffer_manager)

    log.info(
        "Stage 5 live recording ready (quality=%s, low_latency=%s, seg=%ss, "
        "buffer=%ss/%.0fMB, reencode=%s, run_dir=%s, clips_dir=%s).",
        rec_config.quality,
        rec_config.low_latency,
        rec_config.segment_seconds,
        rec_config.buffer_seconds,
        rec_config.max_buffer_mb,
        rec_config.clip_reencode,
        recorder.channel_dir,
        rec_config.clips_output_dir,
    )
    return _RecordingRuntime(
        buffer_manager=buffer_manager,
        recorder=recorder,
        planner=planner,
        clip_worker=clip_worker,
        run_dir=recorder.channel_dir,
    )


def _build_worker(
    settings: Settings,
    *,
    clip_worker: ClipExtractionWorker | None = None,
) -> HighlightAnalysisWorker | None:
    """Construct the optional async Gemini analysis worker (Stage 4)."""
    if not settings.gemini_enabled:
        log.info("Gemini analysis disabled (GEMINI_ENABLED=false); running detection-only.")
        return None
    if not settings.gemini_api_key:
        log.warning(
            "GEMINI_API_KEY is not configured in backend/.env; running detection-only. "
            "Add GEMINI_API_KEY to enable Stage 4 Gemini highlight intelligence."
        )
        return None

    analysis_config = build_analysis_config(settings)
    analyzer = GeminiHighlightAnalyzer(
        api_key=settings.gemini_api_key,
        config=analysis_config,
    )
    sinks: list[AnalysisSink] = [
        logging_analysis_sink,
        JsonlAnalysisSink(settings.highlights_output_path),
    ]
    if clip_worker is not None:
        sinks.append(clip_worker)
    analysis_sink = CompositeAnalysisSink(*sinks)

    log.info(
        "Gemini highlight intelligence ready (model=%s, timeout=%.0fs, min_score=%d, "
        "min_conf=%.2f, queue_max=%d, concurrency=%d, output=%s).",
        analysis_config.model,
        analysis_config.timeout_seconds,
        analysis_config.min_clip_score,
        analysis_config.min_confidence,
        analysis_config.queue_max_size,
        analysis_config.max_concurrency,
        settings.highlights_output_path.name,
    )
    return HighlightAnalysisWorker(
        analyzer=analyzer,
        config=analysis_config,
        sink=analysis_sink,
    )


def _build_pipeline(
    settings: Settings,
    channel: str,
    worker: HighlightAnalysisWorker | None = None,
    recording: _RecordingRuntime | None = None,
) -> StreamHighlightDetector:
    """Construct the per-stream highlight detection pipeline."""
    config = build_highlight_config(settings)
    sink: HighlightSink
    if worker is not None and recording is not None:
        pin_and_submit = Stage5PinAndSubmitSink(
            submit_to_analysis=worker.submit,
            planner=recording.planner,
            buffer_manager=recording.buffer_manager,
        )
        sink = CompositeHighlightSink(logging_sink, pin_and_submit)
    elif worker is not None:
        sink = CompositeHighlightSink(logging_sink, worker)
    else:
        sink = logging_sink

    # For a single live channel we use the channel login as the stream id.
    return StreamHighlightDetector(
        stream_id=channel,
        streamer_name=channel,
        config=config,
        sink=sink,
    )


def _cleanup_run_buffer_dir(recording: _RecordingRuntime) -> None:
    """Purge unpinned segments, remove finished CSVs, and prune empty run dirs."""
    recording.buffer_manager.purge_unpinned_files()

    run_dir = recording.run_dir
    if not run_dir.exists():
        return

    # Remove per-session FFmpeg CSV manifests inside this run's isolated dir.
    for csv_file in run_dir.glob("s*_segments.csv"):
        try:
            csv_file.unlink(missing_ok=True)
        except OSError:
            pass

    # Remove run_dir (and its parent channel dir) ONLY if empty so any preserved
    # pinned files are never touched.
    for candidate in (run_dir, run_dir.parent):
        try:
            candidate.rmdir()
        except OSError:
            break


async def _run_session(
    settings: Settings,
    channel: str,
    pipeline: StreamHighlightDetector,
    worker: HighlightAnalysisWorker | None,
    recording: _RecordingRuntime | None = None,
) -> None:
    """Run ChatMonitor alongside optional Stage 4 Gemini & Stage 5 recording workers."""
    monitor = ChatMonitor(settings, channel, pipeline=pipeline)

    if recording is not None:
        await recording.buffer_manager.start_cleanup_loop(interval_seconds=5.0)
        await recording.clip_worker.start()
        await recording.recorder.start()
    if worker is not None:
        await worker.start()

    try:
        await monitor.run()
    finally:
        # Strict safe shutdown order:
        # 1) Stop chat ingestion.
        monitor.stop()
        # 2) Drain in-flight Gemini verdicts so approved highlights queue for clipping.
        if worker is not None:
            await worker.stop(drain_timeout=15.0)
        # 3) Drain active FFmpeg clip extractions while their segments stay pinned.
        if recording is not None:
            await recording.clip_worker.stop(drain_timeout=30.0)
        # 4) Stop recorder, stop cleanup loop, and purge this run's unpinned buffer.
        if recording is not None:
            await recording.recorder.stop()
            await recording.buffer_manager.stop_cleanup_loop()
            _cleanup_run_buffer_dir(recording)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1].startswith("-"):
        print("Usage: python monitor_chat.py <channel>")
        print("Example: python monitor_chat.py xqc")
        return 2
    channel = sys.argv[1]

    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("Configuration problem: %s", exc)
        return 2

    # Fail fast with a friendly message if the user hasn't logged in yet.
    try:
        get_valid_access_token(settings)
    except ReauthRequired:
        log.error("You are not logged in yet. Run: ./venv/bin/python authorize.py")
        return 1

    channel = channel.lstrip("#").strip().lower()
    gemini_active = bool(settings.gemini_enabled and settings.gemini_api_key)

    try:
        recording = _build_recording_runtime(
            settings,
            channel,
            gemini_active=gemini_active,
        )
        worker = _build_worker(
            settings,
            clip_worker=recording.clip_worker if recording is not None else None,
        )
        pipeline = _build_pipeline(settings, channel, worker=worker, recording=recording)
    except RecorderDependencyError as exc:
        log.error("Missing recording dependency: %s", exc)
        return 2
    except ValueError as exc:
        log.error("Invalid detection/analysis/recording configuration: %s", exc)
        return 2

    log.info(
        "Highlight detection active (window=%ss, multiplier=%sx, min_msgs=%s, "
        "cooldown=%ss, warmup=%ss, pre=%ss, post=%ss, emote_weight=%s).",
        settings.spike_window_seconds,
        settings.spike_multiplier,
        settings.spike_min_messages,
        settings.spike_cooldown_seconds,
        settings.spike_warmup_seconds,
        settings.event_pre_seconds,
        settings.event_post_seconds,
        settings.emote_weight,
    )
    try:
        asyncio.run(_run_session(settings, channel, pipeline, worker, recording))
    except KeyboardInterrupt:
        print()  # tidy the ^C line
        log.info("Interrupted by user. Bye!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
