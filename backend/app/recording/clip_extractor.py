"""Stage 5.4: Async FFmpeg clip extractor producing standalone `.mp4` + `.json` sidecars.

Extraction strategy (verified in Steps 5.0 and 5.1):
- Writes a temporary concat manifest listing the selected `.ts` segments.
- Runs FFmpeg asynchronously via `asyncio.create_subprocess_exec` so Twitch IRC
  and Gemini analysis are never blocked.
- Default re-encoding (`libx264 -preset veryfast -crf 20`, `aac 160k`, `+faststart`)
  ensures clean mid-GOP cuts and tight A/V sync; optional stream-copy mode is
  supported when `config.clip_reencode=False`.
- Writes to a temporary `.tmp.mp4` file and atomically renames to `.mp4` on
  success, then atomically writes the companion `ExtractedClipMetadata` `.json`
  sidecar. Temporary `.tmp.mp4` and `.concat.txt` files are always cleaned up.
- Enforces a per-extraction timeout (default 60s), killing FFmpeg cleanly on
  timeout to prevent orphan processes.
"""

from __future__ import annotations

import asyncio
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from app.analysis.models import AnalyzedHighlight, utc_iso_from_epoch
from app.logging_setup import get_logger
from app.recording.models import (
    ClipWindowPlan,
    ExtractedClipMetadata,
    RecordingConfig,
    SegmentSelection,
    VideoSegment,
)
from app.recording.stream_recorder import find_ffmpeg_binary

log = get_logger(__name__)

_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9_-]+")


class ClipExtractionError(RuntimeError):
    """Raised when FFmpeg fails to extract a clip."""


class ClipExtractionTimeoutError(ClipExtractionError):
    """Raised when FFmpeg extraction exceeds the configured timeout."""


class AsyncSubprocess(Protocol):
    """Minimal async subprocess protocol (satisfied by `asyncio.subprocess.Process`)."""

    @property
    def returncode(self) -> int | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


ExtractorProcessFactory = Callable[[list[str]], Awaitable[AsyncSubprocess]]


@dataclass(frozen=True)
class ClipExtractionResult:
    """Outcome of one clip extraction attempt."""

    status: str  # "extracted", "skipped_no_coverage", "skipped_insufficient_media"
    event_id: str
    clip_path: Path | None = None
    metadata_path: Path | None = None
    metadata: ExtractedClipMetadata | None = None
    reason: str | None = None
    elapsed_ms: float = 0.0


def _sanitize_token(value: str, fallback: str) -> str:
    cleaned = _SAFE_TOKEN_RE.sub("_", value.strip().lower()).strip("_")
    return cleaned or fallback


def build_clip_output_paths(
    output_dir: Path,
    channel: str,
    event_id: str,
    trigger_timestamp: float,
    category: str,
    platform: str | None = None,
) -> tuple[Path, Path]:
    """Build deterministic output paths: `<dir>/[platform/]<channel>/<YYYYMMDD_HHMMSS>_<id[:8]>_<cat>.mp4|.json`."""
    safe_channel = _sanitize_token(channel.lstrip("#"), "unknown")
    safe_category = _sanitize_token(category, "other")
    safe_id = _sanitize_token(event_id[:8], "event")
    dt = datetime.fromtimestamp(trigger_timestamp, tz=timezone.utc)
    ts_label = dt.strftime("%Y%m%d_%H%M%S")

    stem = f"{ts_label}_{safe_id}_{safe_category}"
    channel_dir = Path(output_dir) / safe_channel
    return channel_dir / f"{stem}.mp4", channel_dir / f"{stem}.json"


def _format_concat_manifest(segments: list[VideoSegment]) -> str:
    """Format an FFmpeg concat demuxer file safely escaping single quotes."""
    lines: list[str] = []
    for seg in segments:
        escaped = str(seg.path).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def build_ffmpeg_extract_command(
    ffmpeg_bin: str,
    concat_path: Path,
    trim_start_seconds: float,
    duration_seconds: float,
    tmp_output_path: Path,
    *,
    reencode: bool = True,
) -> list[str]:
    """Build the verified FFmpeg concat + trim extraction command."""
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-ss",
        f"{max(0.0, trim_start_seconds):.3f}",
        "-t",
        f"{max(0.0, duration_seconds):.3f}",
    ]
    if reencode:
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
            ]
        )
    else:
        cmd.extend(["-c", "copy"])

    cmd.extend(["-movflags", "+faststart", "-f", "mp4", str(tmp_output_path)])
    return cmd


async def _default_spawn_ffmpeg(cmd: list[str]) -> AsyncSubprocess:
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=(os.name == "posix"),
    )


def _kill_process_tree(proc: AsyncSubprocess) -> None:
    """Force-kill an FFmpeg subprocess and its entire POSIX process group."""
    if proc.returncode is not None:
        return
    pid = getattr(proc, "pid", None)
    if os.name == "posix" and isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class ClipExtractor:
    """Async FFmpeg clip extractor writing atomic `.mp4` + `.json` sidecars."""

    def __init__(
        self,
        config: RecordingConfig,
        *,
        output_dir: Path | None = None,
        ffmpeg_bin: str | None = None,
        min_media_seconds: float = 2.0,
        timeout_seconds: float = 60.0,
        process_factory: ExtractorProcessFactory | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        resolved_dir = output_dir if output_dir is not None else config.clips_output_dir
        if resolved_dir is None:
            raise ValueError("clips_output_dir must be set on RecordingConfig or passed explicitly")
        if min_media_seconds < 0:
            raise ValueError("min_media_seconds must be >= 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        self._config = config
        self._output_dir = Path(resolved_dir)
        self._ffmpeg_bin = ffmpeg_bin
        self._min_media_seconds = min_media_seconds
        self._timeout_seconds = timeout_seconds
        self._process_factory = process_factory or _default_spawn_ffmpeg
        self._wall_clock = wall_clock

    async def extract(
        self,
        analysis: AnalyzedHighlight,
        plan: ClipWindowPlan,
        selection: SegmentSelection,
        platform: str | None = None,
    ) -> ClipExtractionResult:
        """Extract a standalone `.mp4` clip and companion `.json` sidecar."""
        started = time.monotonic()

        if not selection.segments:
            log.warning(
                "Skipping clip extraction for event %s: zero recorded segments overlap [%.1f..%.1f].",
                analysis.event_id[:8],
                plan.requested_clip_start,
                plan.requested_clip_end,
            )
            return ClipExtractionResult(
                status="skipped_no_coverage",
                event_id=analysis.event_id,
                reason="no_overlapping_segments",
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        existing_segments = [s for s in selection.segments if s.path.is_file()]
        if not existing_segments:
            log.warning(
                "Skipping clip extraction for event %s: all %d selected segment files missing on disk.",
                analysis.event_id[:8],
                len(selection.segments),
            )
            return ClipExtractionResult(
                status="skipped_no_coverage",
                event_id=analysis.event_id,
                reason="segment_files_missing_on_disk",
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        if selection.target_duration_seconds < self._min_media_seconds:
            log.warning(
                "Skipping clip extraction for event %s: available media %.2fs < minimum %.2fs.",
                analysis.event_id[:8],
                selection.target_duration_seconds,
                self._min_media_seconds,
            )
            return ClipExtractionResult(
                status="skipped_insufficient_media",
                event_id=analysis.event_id,
                reason=(
                    f"insufficient_media ({selection.target_duration_seconds:.2f}s < "
                    f"{self._min_media_seconds:.2f}s)"
                ),
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )

        ffmpeg_bin = self._ffmpeg_bin or find_ffmpeg_binary()
        self._ffmpeg_bin = ffmpeg_bin

        category = analysis.verdict.category.value if analysis.verdict else "other"
        mp4_path, json_path = build_clip_output_paths(
            self._output_dir,
            analysis.streamer_name or analysis.stream_id,
            analysis.event_id,
            analysis.trigger_timestamp,
            category,
            platform=platform,
        )
        mp4_path.parent.mkdir(parents=True, exist_ok=True)

        concat_path = mp4_path.parent / f".{mp4_path.stem}.concat.txt"
        tmp_mp4_path = mp4_path.parent / f".{mp4_path.stem}.tmp.mp4"
        tmp_json_path = json_path.parent / f".{json_path.stem}.tmp.json"

        try:
            concat_path.write_text(
                _format_concat_manifest(existing_segments), encoding="utf-8"
            )
            cmd = build_ffmpeg_extract_command(
                ffmpeg_bin,
                concat_path,
                selection.approx_trim_start_seconds,
                selection.target_duration_seconds,
                tmp_mp4_path,
                reencode=self._config.clip_reencode,
            )

            proc = await self._process_factory(cmd)
            try:
                _, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                _kill_process_tree(proc)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
                raise ClipExtractionTimeoutError(
                    f"FFmpeg extraction timed out after {self._timeout_seconds:.1f}s "
                    f"for event {analysis.event_id[:8]}."
                ) from exc
            except asyncio.CancelledError:
                _kill_process_tree(proc)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                raise

            if proc.returncode != 0:
                err_text = stderr_bytes.decode("utf-8", errors="replace").strip()[-400:]
                raise ClipExtractionError(
                    f"FFmpeg extraction failed (rc={proc.returncode}) for event "
                    f"{analysis.event_id[:8]}: {err_text or 'no stderr'}"
                )

            if not tmp_mp4_path.is_file() or tmp_mp4_path.stat().st_size == 0:
                raise ClipExtractionError(
                    f"FFmpeg reported success but produced no output for event "
                    f"{analysis.event_id[:8]}."
                )

            # Atomic rename for .mp4 first.
            os.replace(tmp_mp4_path, mp4_path)

            extracted_at = self._wall_clock()
            metadata = ExtractedClipMetadata(
                event_id=analysis.event_id,
                stream_id=analysis.stream_id,
                streamer_name=analysis.streamer_name,
                clip_path=str(mp4_path),
                extracted_at=extracted_at,
                extracted_at_iso=utc_iso_from_epoch(extracted_at),
                clip_plan=plan,
                selection=selection,
                analysis=analysis.to_dict(),
                reencoded=self._config.clip_reencode,
                platform=platform or "twitch",
            )
            tmp_json_path.write_text(metadata.to_json(indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_json_path, json_path)

            elapsed_ms = (time.monotonic() - started) * 1000.0
            cov = selection.coverage
            log.warning(
                "CLIP EXTRACTED [%s] id=%s file=%s dur=%.1fs segs=%d partial=%s "
                "gaps=%d elapsed=%.0fms",
                analysis.streamer_name,
                analysis.event_id[:8],
                mp4_path.name,
                selection.target_duration_seconds,
                len(existing_segments),
                cov.is_partial,
                len(cov.discontinuities),
                elapsed_ms,
            )
            return ClipExtractionResult(
                status="extracted",
                event_id=analysis.event_id,
                clip_path=mp4_path,
                metadata_path=json_path,
                metadata=metadata,
                elapsed_ms=elapsed_ms,
            )
        finally:
            _safe_unlink(concat_path)
            _safe_unlink(tmp_mp4_path)
            _safe_unlink(tmp_json_path)
