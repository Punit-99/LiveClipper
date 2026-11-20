"""Audio energy and loudness analysis using FFmpeg PCM extraction and pure Python math."""

from __future__ import annotations

import array
import logging
import math
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000  # 16 kHz mono s16le
WINDOW_SEC = 0.5     # 0.5s resolution
SAMPLES_PER_WINDOW = int(SAMPLE_RATE * WINDOW_SEC)  # 8000 samples
BYTES_PER_WINDOW = SAMPLES_PER_WINDOW * 2           # 16-bit = 2 bytes/sample


def compute_audio_energy_profile(
    media_path: str | Path,
    *,
    start_sec: float | None = None,
    duration_sec: float | None = None,
) -> list[dict[str, Any]]:
    """Extract audio via FFmpeg and compute RMS / dBFS energy per time window.

    Returns a list of dicts: `[{"time": float, "rms": float, "dbfs": float}, ...]`.
    """
    path = Path(media_path)
    if not path.is_file():
        log.warning("Audio energy: file not found: %s", media_path)
        return []

    cmd = ["ffmpeg", "-v", "error"]
    if start_sec is not None and start_sec > 0:
        cmd.extend(["-ss", f"{start_sec:.2f}"])
    if duration_sec is not None and duration_sec > 0:
        cmd.extend(["-t", f"{duration_sec:.2f}"])
    cmd.extend([
        "-i", str(path),
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-f", "s16le",
        "-",
    ])

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        log.warning("Audio energy: FFmpeg binary not found on system PATH.")
        return []

    windows: list[dict[str, Any]] = []
    window_idx = 0
    base_time = start_sec or 0.0

    try:
        while True:
            raw_bytes = proc.stdout.read(BYTES_PER_WINDOW)
            if not raw_bytes:
                break
            num_samples = len(raw_bytes) // 2
            if num_samples == 0:
                break

            samples = array.array("h")
            samples.frombytes(raw_bytes[: num_samples * 2])

            sum_sq = sum(s * s for s in samples)
            rms = math.sqrt(sum_sq / num_samples) if num_samples > 0 else 0.0
            if rms > 0.0:
                dbfs = 20.0 * math.log10(rms / 32768.0)
            else:
                dbfs = -96.0

            timestamp = base_time + (window_idx * WINDOW_SEC)
            windows.append({
                "time": round(timestamp, 2),
                "rms": round(rms, 2),
                "dbfs": round(dbfs, 2),
            })
            window_idx += 1

        proc.stdout.close()
        proc.wait(timeout=10)
    except Exception as exc:
        log.warning("Audio energy extraction encountered error: %s", exc)
        try:
            proc.kill()
        except Exception:
            pass

    return windows


def detect_audio_spikes(
    windows: list[dict[str, Any]],
    *,
    min_spike_db: float = 12.0,
    min_duration_sec: float = 1.0,
    target_duration_sec: float = 55.0,
) -> list[dict[str, Any]]:
    """Find sudden volume spikes and silence-to-loud transitions from energy windows.

    Returns candidate highlight intervals with start_time, end_time, score, and peak_dbfs.
    """
    if not windows:
        return []

    dbfs_values = [w["dbfs"] for w in windows]
    sorted_db = sorted(dbfs_values)
    # Baseline: 30th percentile representing typical background sound
    p30_idx = int(len(sorted_db) * 0.30)
    baseline_dbfs = sorted_db[p30_idx]

    # Max acceptable silence threshold
    silence_threshold = -42.0

    spike_regions: list[tuple[float, float, float]] = []  # (start, end, peak_dbfs)
    in_spike = False
    current_start = 0.0
    current_peak = -96.0

    for i, w in enumerate(windows):
        db = w["dbfs"]
        t = w["time"]

        is_spike = (db - baseline_dbfs >= min_spike_db) or (
            i > 2 and windows[i - 2]["dbfs"] < silence_threshold and db > -18.0
        )

        if is_spike:
            if not in_spike:
                in_spike = True
                current_start = t
                current_peak = db
            else:
                current_peak = max(current_peak, db)
        else:
            if in_spike:
                current_end = t
                if (current_end - current_start) >= min_duration_sec:
                    spike_regions.append((current_start, current_end, current_peak))
                in_spike = False

    if in_spike:
        current_end = windows[-1]["time"]
        if (current_end - current_start) >= min_duration_sec:
            spike_regions.append((current_start, current_end, current_peak))

    # Merge nearby spikes (within 8 seconds of each other)
    merged_regions: list[tuple[float, float, float]] = []
    for r in spike_regions:
        if not merged_regions:
            merged_regions.append(r)
        else:
            prev_s, prev_e, prev_peak = merged_regions[-1]
            if r[0] - prev_e <= 8.0:
                merged_regions[-1] = (prev_s, r[1], max(prev_peak, r[2]))
            else:
                merged_regions.append(r)

    candidates: list[dict[str, Any]] = []
    for s, e, peak in merged_regions:
        # Score calculation: 50 to 95 based on peak loudness and spike delta
        delta = peak - baseline_dbfs
        normalized_score = min(95.0, max(50.0, 50.0 + (delta * 1.8) + ((peak + 20.0) * 1.2)))
        candidates.append({
            "start_time": s,
            "end_time": e,
            "peak_dbfs": round(peak, 1),
            "baseline_dbfs": round(baseline_dbfs, 1),
            "score": round(normalized_score, 1),
        })

    return candidates


def analyze_clip_audio_energy(media_path: str | Path) -> dict[str, Any]:
    """Compute high-level audio metrics for an existing clip or buffer segment.

    Used by live highlight detection to give Gemini context on stream audio.
    """
    windows = compute_audio_energy_profile(media_path)
    if not windows:
        return {
            "peak_dbfs": -96.0,
            "mean_dbfs": -96.0,
            "baseline_dbfs": -96.0,
            "spike_detected": False,
            "loud_ratio": 0.0,
            "available": False,
        }

    db_vals = [w["dbfs"] for w in windows]
    peak_dbfs = max(db_vals)
    mean_dbfs = sum(db_vals) / len(db_vals)
    sorted_db = sorted(db_vals)
    baseline_dbfs = sorted_db[int(len(sorted_db) * 0.30)]

    # Loud ratio: fraction of time audio is above -18 dBFS
    loud_count = sum(1 for v in db_vals if v >= -18.0)
    loud_ratio = loud_count / len(db_vals)

    spike_detected = (peak_dbfs - baseline_dbfs >= 12.0) and (peak_dbfs >= -20.0)

    return {
        "peak_dbfs": round(peak_dbfs, 1),
        "mean_dbfs": round(mean_dbfs, 1),
        "baseline_dbfs": round(baseline_dbfs, 1),
        "delta_db": round(peak_dbfs - baseline_dbfs, 1),
        "spike_detected": spike_detected,
        "loud_ratio": round(loud_ratio, 2),
        "available": True,
    }
