"""Speech-to-text transcription engine using faster-whisper.

Produces punctuated word-level timestamped transcripts adhering to the contract:
{
    "text": str,
    "language": str,
    "segments": [
        {
            "start": float,
            "end": float,
            "text": str,
            "words": [{"word": str, "start": float, "end": float}, ...]
        }
    ]
}
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from app.logging_setup import get_logger
from app.video.subtitles import merge_continuation_words

log = get_logger(__name__)


class TranscriptionError(Exception):
    """Raised when transcription cannot be performed."""


def has_audio_stream(media_path: str | Path) -> bool:
    """Check if the given video/audio file contains an audio stream."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0",
        str(media_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10.0)
        return bool(res.stdout.strip())
    except Exception:
        return True


def transcribe_media(
    media_path: str | Path,
    language: str | None = None,
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str = "int8",
) -> dict[str, Any]:
    """Transcribe audio from a video/audio file using faster-whisper.

    Returns the transcript dict with word-level timestamps.
    """
    path = Path(media_path)
    if not path.is_file():
        raise FileNotFoundError(f"Media file not found: {path}")

    if not has_audio_stream(path):
        raise TranscriptionError(f"Media has no audio stream: {path}")

    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError(
            "faster-whisper is not installed. To enable transcription, "
            "install faster-whisper: pip install faster-whisper"
        ) from exc

    log.info("Transcribing media with faster-whisper (model=%s, device=%s): %s", model_size, device, path.name)

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(path),
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
        word_timestamps=True,
        language=language,
    )

    out_segments: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for seg in segments:
        words = [
            {"word": w.word, "start": float(w.start), "end": float(w.end)}
            for w in (seg.words or [])
        ]
        out_segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": seg.text.strip(),
            "words": merge_continuation_words(words),
        })
        text_parts.append(seg.text.strip())

    return {
        "text": " ".join(text_parts),
        "language": info.language,
        "segments": out_segments,
    }
