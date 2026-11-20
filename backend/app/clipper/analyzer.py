"""Multi-signal intelligent moment detection for YouTube videos."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

from app.clipper.audio_energy import compute_audio_energy_profile, detect_audio_spikes
from app.clipper.models import MomentCandidate, TranscriptSegment, VideoMetadata
from app.config import get_settings

log = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(
    r"(?:(?:(\d{1,2}):)?(\d{1,2}):)?(\d{2})(?:\.(\d+))?"
)


def _parse_timestamp(ts_str: str) -> float:
    """Parse timestamp string (HH:MM:SS.mmm or MM:SS.mmm or SS.mmm) to seconds."""
    ts_str = ts_str.strip().replace(",", ".")
    parts = ts_str.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600.0 + float(m) * 60.0 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return float(m) * 60.0 + float(s)
    else:
        return float(parts[0])


def parse_subtitles_file(file_path: Path) -> list[TranscriptSegment]:
    """Parse VTT or SRT subtitle file into a list of TranscriptSegment."""
    if not file_path or not file_path.is_file():
        return []

    content = file_path.read_text(encoding="utf-8", errors="ignore")
    segments: list[TranscriptSegment] = []

    # Regex for VTT/SRT timing lines: 00:01:23.456 --> 00:01:28.789
    timing_pattern = re.compile(
        r"((?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3})"
    )

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = timing_pattern.search(line)
        if match:
            try:
                start_sec = _parse_timestamp(match.group(1))
                end_sec = _parse_timestamp(match.group(2))
                text_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() and not timing_pattern.search(lines[i]):
                    clean = re.sub(r"<[^>]+>", "", lines[i].strip())
                    if clean and not clean.isdigit():
                        text_lines.append(clean)
                    i += 1
                text = " ".join(text_lines).strip()
                if text:
                    segments.append(TranscriptSegment(start=start_sec, end=end_sec, text=text))
                continue
            except Exception:
                pass
        i += 1

    return segments


def extract_transcript(
    media_path: Path,
    subtitle_path: Path | None = None,
) -> list[TranscriptSegment]:
    """Extract transcript segments from subtitle file or faster-whisper fallback."""
    if subtitle_path and subtitle_path.is_file():
        parsed = parse_subtitles_file(subtitle_path)
        if parsed:
            log.info("Parsed %d subtitle segments from %s", len(parsed), subtitle_path.name)
            return parsed

    # Fallback to Whisper transcription if available
    try:
        from app.video.transcribe import transcribe_media
        log.info("Subtitles not found; running speech transcription on %s", media_path.name)
        result = transcribe_media(media_path)
        segments: list[TranscriptSegment] = []
        for seg in result.get("segments", []):
            segments.append(
                TranscriptSegment(
                    start=float(seg.get("start", 0.0)),
                    end=float(seg.get("end", 0.0)),
                    text=str(seg.get("text", "")).strip(),
                )
            )
        return segments
    except Exception as exc:
        log.warning("Whisper transcription skipped or failed: %s", exc)
        return []


def _analyze_transcript_with_gemini(
    chunk_text: str,
    chunk_start: float,
    api_key: str,
) -> list[dict[str, Any]]:
    """Analyze a transcript chunk with Gemini to identify viral-worthy moments."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    prompt = (
        "Analyze this transcript from a YouTube video. Identify the most interesting, funny, "
        "surprising, emotional, or viral-worthy moments.\n"
        "For each moment, provide:\n"
        "- timestamp range (start_time and end_time in seconds relative to the chunk start)\n"
        "- reason it's interesting\n"
        "- viral potential score (0-100)\n"
        "- suggested clip title\n\n"
        "Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "moments": [\n'
        "    {\n"
        '      "start_time": float,\n'
        '      "end_time": float,\n'
        '      "score": float,\n'
        '      "reason": string,\n'
        '      "suggested_title": string\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        f"Transcript chunk:\n{chunk_text}"
    )

    settings = get_settings()
    resp = client.models.generate_content(
        model=settings.gemini_model or "gemini-3.1-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )

    text = resp.text or "{}"
    data = json.loads(text)
    moments = data.get("moments", [])
    adjusted = []
    for m in moments:
        st = float(m.get("start_time", 0.0)) + chunk_start
        et = float(m.get("end_time", st + 30.0)) + chunk_start
        score = float(m.get("score", 70.0))
        reason = str(m.get("reason", "Interesting dialogue moment"))
        title = str(m.get("suggested_title", "Viral Highlight"))
        adjusted.append({
            "start_time": st,
            "end_time": et,
            "score": score,
            "reason": reason,
            "suggested_title": title,
        })
    return adjusted


def _heuristic_transcript_analysis(
    segments: list[TranscriptSegment],
) -> list[dict[str, Any]]:
    """Fallback heuristic analyzer when Gemini API is unavailable or offline."""
    if not segments:
        return []

    viral_triggers = [
        "what", "why", "omg", "oh my god", "insane", "crazy", "no way", "bro",
        "laugh", "screaming", "look at", "watch this", "unbelievable", "wtf",
        "win", "fail", "lost", "dead", "died", "hack", "secret", "reveal"
    ]

    candidates: list[dict[str, Any]] = []
    window_sec = 45.0
    step_sec = 20.0
    total_time = segments[-1].end if segments else 0.0

    current_t = 0.0
    while current_t < total_time:
        win_end = current_t + window_sec
        matching_segs = [s for s in segments if s.start >= current_t and s.end <= win_end]
        text = " ".join(s.text for s in matching_segs)
        lower = text.lower()

        # Score based on punctuation and keywords
        exclamation_count = text.count("!")
        question_count = text.count("?")
        keyword_hits = sum(1 for w in viral_triggers if w in lower)
        word_count = len(text.split())

        score = 50.0 + min(25.0, keyword_hits * 6.0) + min(15.0, exclamation_count * 3.0) + min(10.0, word_count * 0.1)

        if score >= 65.0 and matching_segs:
            peak_seg = matching_segs[len(matching_segs) // 2]
            title = peak_seg.text[:50].strip() or "Exciting Moment"
            candidates.append({
                "start_time": current_t,
                "end_time": min(total_time, win_end),
                "score": round(min(95.0, score), 1),
                "reason": f"High dialogue excitement ({keyword_hits} key triggers, {exclamation_count} exclamations)",
                "suggested_title": title,
                "transcript_excerpt": text[:200],
            })
        current_t += step_sec

    return candidates


def analyze_transcript_signal(
    segments: list[TranscriptSegment],
    *,
    api_key: str | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Analyze speech transcript using Gemini or heuristic fallback."""
    if not segments:
        return []

    settings = get_settings()
    key = api_key or settings.gemini_api_key

    if not key:
        log.info("Gemini API key not configured; using heuristic transcript analysis.")
        return _heuristic_transcript_analysis(segments)

    # Chunk into 5-minute blocks
    chunk_duration = 300.0
    overlap = 30.0
    total_duration = segments[-1].end
    results: list[dict[str, Any]] = []

    start = 0.0
    chunk_idx = 1
    total_chunks = max(1, int(total_duration // (chunk_duration - overlap)) + 1)

    while start < total_duration:
        end = start + chunk_duration
        chunk_segs = [s for s in segments if s.start >= start and s.end <= end]
        if chunk_segs:
            chunk_text = "\n".join(f"[{s.start:.1f}s - {s.end:.1f}s]: {s.text}" for s in chunk_segs)
            if progress_cb:
                progress_cb(f"Analyzing transcript with Gemini... (chunk {chunk_idx}/{total_chunks})")
            try:
                moments = _analyze_transcript_with_gemini(chunk_text, start, key)
                results.extend(moments)
            except Exception as exc:
                log.warning("Gemini chunk analysis failed for [%.1f-%.1f]: %s", start, end, exc)
                # Fallback for this chunk
                results.extend(_heuristic_transcript_analysis(chunk_segs))
        start += (chunk_duration - overlap)
        chunk_idx += 1

    return results


def analyze_visual_signal(video_path: Path) -> list[dict[str, Any]]:
    """Detect visual action / rapid scene cuts using scene_detection."""
    try:
        from app.video.scene_detection import detect_scenes
        scenes, fps = detect_scenes(video_path)
        if not scenes or fps <= 0:
            return []

        # Find timestamps where cuts are clustered
        cut_times = [s[0] / fps for s in scenes]
        candidates = []
        window = 15.0
        for i, ct in enumerate(cut_times):
            # count cuts in next 15s
            cuts_in_win = sum(1 for t in cut_times if ct <= t <= ct + window)
            if cuts_in_win >= 3:
                score = min(90.0, 50.0 + cuts_in_win * 8.0)
                candidates.append({
                    "start_time": ct,
                    "end_time": ct + window,
                    "score": round(score, 1),
                    "reason": f"Rapid visual scene changes ({cuts_in_win} cuts)",
                })
        return candidates
    except Exception as exc:
        log.warning("Visual scene analysis failed: %s", exc)
        return []


def analyze_engagement_signal(metadata: VideoMetadata) -> list[dict[str, Any]]:
    """Extract candidate moments from chapters, description timestamps, and heatmap."""
    candidates: list[dict[str, Any]] = []

    # 1. Heatmap (most replayed)
    for entry in metadata.heatmap:
        val = float(entry.get("value", 0.0))
        st = float(entry.get("start_time", 0.0))
        et = float(entry.get("end_time", st + 30.0))
        if val >= 0.5:
            score = 60.0 + (val * 35.0)
            candidates.append({
                "start_time": st,
                "end_time": et,
                "score": round(score, 1),
                "reason": f"YouTube Most Replayed peak (intensity {val:.2f})",
                "suggested_title": f"Most Replayed Moment ({st:.0f}s)",
            })

    # 2. Chapters
    for ch in metadata.chapters:
        st = float(ch.get("start_time", 0.0))
        et = float(ch.get("end_time", st + 45.0))
        title = ch.get("title", "")
        candidates.append({
            "start_time": st,
            "end_time": min(et, st + 55.0),
            "score": 65.0,
            "reason": f"Video Chapter: {title}",
            "suggested_title": title or "Chapter Highlight",
        })

    # 3. Description timestamps
    desc_ts = re.findall(r"(?:^|\s)(?:(\d{1,2}:)?(\d{1,2}):(\d{2}))\s+([^\n]+)", metadata.description)
    for h, m, s, note in desc_ts:
        hours = int(h[:-1]) if h else 0
        minutes = int(m)
        seconds = int(s)
        total_sec = hours * 3600 + minutes * 60 + seconds
        candidates.append({
            "start_time": float(total_sec),
            "end_time": float(total_sec + 55.0),
            "score": 75.0,
            "reason": f"Creator timestamped: {note[:40]}",
            "suggested_title": note[:50].strip() or "Creator Timestamp",
        })

    return candidates


def detect_and_rank_moments(
    media_path: Path,
    metadata: VideoMetadata,
    subtitle_path: Path | None = None,
    *,
    max_clips: int = 5,
    target_clip_duration: float = 55.0,
    skip_intro_sec: float = 0.0,
    skip_outro_sec: float = 0.0,
    start_time: float | None = None,
    end_time: float | None = None,
    progress_cb: Callable[[str], None] | None = None,
) -> list[MomentCandidate]:
    """Combine transcript, audio, visual, and engagement signals into ranked moments."""
    duration = metadata.duration
    min_time = max(0.0, skip_intro_sec)
    max_time = max(min_time, duration - skip_outro_sec) if duration > 0 else 999999.0

    if start_time is not None and float(start_time) > 0:
        min_time = max(min_time, float(start_time))
    if end_time is not None and float(end_time) > 0:
        max_time = min(max_time, float(end_time))

    # 1. Transcript signal (50% weight)
    if progress_cb:
        progress_cb("Extracting speech transcript...")
    transcript_segs = extract_transcript(media_path, subtitle_path)
    transcript_moments = analyze_transcript_signal(transcript_segs, progress_cb=progress_cb)

    # 2. Audio energy signal (20% weight)
    if progress_cb:
        progress_cb("Analyzing audio waveform and volume spikes...")
    audio_windows = compute_audio_energy_profile(media_path)
    audio_moments = detect_audio_spikes(audio_windows)

    # 3. Visual scene signal (15% weight)
    if progress_cb:
        progress_cb("Detecting visual cuts and scene changes...")
    visual_moments = analyze_visual_signal(media_path)

    # 4. Engagement signal (15% weight)
    if progress_cb:
        progress_cb("Analyzing chapters and engagement heatmap...")
    engagement_moments = analyze_engagement_signal(metadata)

    # Collect initial candidate peaks
    raw_candidates: list[dict[str, Any]] = []
    for m in transcript_moments:
        raw_candidates.append({**m, "source": "transcript", "peak": (m["start_time"] + m["end_time"]) / 2.0})
    for m in audio_moments:
        raw_candidates.append({**m, "source": "audio", "peak": (m["start_time"] + m["end_time"]) / 2.0})
    for m in visual_moments:
        raw_candidates.append({**m, "source": "visual", "peak": (m["start_time"] + m["end_time"]) / 2.0})
    for m in engagement_moments:
        raw_candidates.append({**m, "source": "engagement", "peak": (m["start_time"] + m["end_time"]) / 2.0})

    if not raw_candidates:
        # Default evenly spaced moments if no signals triggered
        log.info("No strong signals detected; generating evenly distributed highlight moments.")
        step = max(60.0, (max_time - min_time) / (max_clips + 1))
        for i in range(1, max_clips + 1):
            p = min_time + (i * step)
            raw_candidates.append({
                "start_time": p - 20.0,
                "end_time": p + 20.0,
                "peak": p,
                "score": 60.0,
                "source": "fallback",
                "reason": "Evenly spaced stream moment",
                "suggested_title": f"Highlight #{i}",
            })

    # Combined scoring and window generation
    scored_moments: list[MomentCandidate] = []
    for cand in raw_candidates:
        peak = cand["peak"]
        if peak < min_time or peak > max_time:
            continue

        half_dur = target_clip_duration / 2.0
        start_t = max(min_time, peak - half_dur)
        end_t = min(max_time, start_t + target_clip_duration)
        actual_dur = end_t - start_t

        # Compute signal scores in this window
        t_score = max([m["score"] for m in transcript_moments if abs((m["start_time"] + m["end_time"])/2.0 - peak) <= 25.0] or [0.0])
        a_score = max([m["score"] for m in audio_moments if abs((m["start_time"] + m["end_time"])/2.0 - peak) <= 25.0] or [0.0])
        v_score = max([m["score"] for m in visual_moments if abs((m["start_time"] + m["end_time"])/2.0 - peak) <= 25.0] or [0.0])
        e_score = max([m["score"] for m in engagement_moments if abs((m["start_time"] + m["end_time"])/2.0 - peak) <= 25.0] or [0.0])

        # 50% transcript, 20% audio, 15% visual, 15% engagement
        # Dynamic weighting if some signals are missing
        weights = {"t": 0.50, "a": 0.20, "v": 0.15, "e": 0.15}
        active_weights = 0.0
        weighted_sum = 0.0

        if t_score > 0:
            weighted_sum += weights["t"] * t_score
            active_weights += weights["t"]
        if a_score > 0:
            weighted_sum += weights["a"] * a_score
            active_weights += weights["a"]
        if v_score > 0:
            weighted_sum += weights["v"] * v_score
            active_weights += weights["v"]
        if e_score > 0:
            weighted_sum += weights["e"] * e_score
            active_weights += weights["e"]

        if active_weights > 0:
            final_score = weighted_sum / active_weights
        else:
            final_score = cand.get("score", 60.0)

        # Multi-signal bonus: if 2 or more signals align, boost score by up to 10%
        align_count = sum(1 for s in (t_score, a_score, v_score, e_score) if s > 0)
        if align_count >= 2:
            final_score = min(99.0, final_score + (align_count * 3.0))

        # Transcript excerpt
        matching_text = " ".join(
            s.text for s in transcript_segs if s.start >= start_t and s.end <= end_t
        )

        title = cand.get("suggested_title") or f"{metadata.title[:30]} Highlight"
        reason = cand.get("reason") or "Strong multi-signal alignment"

        scored_moments.append(
            MomentCandidate(
                moment_id=f"m_{int(start_t)}_{int(end_t)}",
                start_time=round(start_t, 2),
                end_time=round(end_t, 2),
                duration=round(actual_dur, 2),
                score=round(final_score, 1),
                reason=reason,
                suggested_title=title,
                transcript_excerpt=matching_text[:250],
                signal_scores={
                    "transcript": round(t_score, 1),
                    "audio": round(a_score, 1),
                    "visual": round(v_score, 1),
                    "engagement": round(e_score, 1),
                },
            )
        )

    # Sort descending by score
    scored_moments.sort(key=lambda m: m.score, reverse=True)

    # Merge / deduplicate overlapping moments (centers within 25 seconds)
    selected: list[MomentCandidate] = []
    for m in scored_moments:
        m_center = (m.start_time + m.end_time) / 2.0
        overlap = False
        for s in selected:
            s_center = (s.start_time + s.end_time) / 2.0
            if abs(m_center - s_center) < 30.0:
                overlap = True
                break
        if not overlap:
            selected.append(m)
        if len(selected) >= max_clips:
            break

    # Sort selected moments chronologically for clean extraction
    selected.sort(key=lambda m: m.start_time)
    return selected
