"""Offline FFmpeg extraction-strategy verification tests (Step 5.1 Condition #1).

Validates the chosen re-encoding FFmpeg extraction strategy against synthetic
H.264/AAC MPEG-TS rolling segments before production extractor code is written:
- Mid-GOP clip extraction
- Multi-segment extraction
- Missing-segment extraction (discontinuity gap)
- Start/end boundary extraction
- Audio/video synchronization across all scenarios

Runs completely offline in a temporary directory. Skipped cleanly if `ffmpeg`
or `ffprobe` is not installed on the machine.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.recording import SegmentIndex, VideoSegment


def _find_binary(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if Path(candidate).exists():
            return candidate
    return None


FFMPEG = _find_binary("ffmpeg")
FFPROBE = _find_binary("ffprobe")


def _probe(path: Path) -> dict:
    assert FFPROBE is not None
    raw = subprocess.check_output(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        text=True,
    )
    info = json.loads(raw)
    fmt = info.get("format", {})
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    v_dur = float(v.get("duration") or fmt["duration"])
    a_dur = float(a.get("duration") or fmt["duration"])
    v_start = float(v.get("start_time") or 0.0)
    a_start = float(a.get("start_time") or 0.0)
    return {
        "fmt_dur": float(fmt["duration"]),
        "v_dur": v_dur,
        "a_dur": a_dur,
        "v_start": v_start,
        "a_start": a_start,
        "av_start_diff_ms": abs(v_start - a_start) * 1000.0,
        "av_dur_diff_ms": abs(v_dur - a_dur) * 1000.0,
    }


def _extract_reencode(
    segments: list[VideoSegment], trim_start: float, duration: float, out_path: Path
) -> dict:
    assert FFMPEG is not None
    concat_file = out_path.with_suffix(".concat.txt")
    concat_file.write_text(
        "\n".join(f"file '{s.path}'" for s in segments) + "\n", encoding="utf-8"
    )
    subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-ss", f"{trim_start:.3f}", "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart",
            str(out_path),
        ],
        check=True,
    )
    return _probe(out_path)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg and ffprobe are required for extraction strategy tests")
class TestFfmpegExtractionStrategy(unittest.TestCase):
    _tmpdir: tempfile.TemporaryDirectory[str]
    _seg_dir: Path
    _index: SegmentIndex
    _segments: list[VideoSegment]

    @classmethod
    def setUpClass(cls) -> None:
        assert FFMPEG is not None
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="liveclipper_ext_test_")
        root = Path(cls._tmpdir.name)
        cls._seg_dir = root / "segments"
        cls._seg_dir.mkdir()
        csv_path = cls._seg_dir / "segments.csv"

        # Generate 24s synthetic Twitch-like 720p30 H.264 (2s GOP = 60 frames) + 48kHz AAC
        # piped into FFmpeg stream_segment muxer producing six 4-second .ts segments.
        gen_cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=24",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "mpegts", "pipe:1",
        ]
        seg_cmd = [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-c", "copy",
            "-f", "stream_segment",
            "-segment_time", "4",
            "-reset_timestamps", "1",
            "-segment_list", str(csv_path),
            "-segment_list_type", "csv",
            str(cls._seg_dir / "seg_%05d.ts"),
        ]
        p_gen = subprocess.Popen(gen_cmd, stdout=subprocess.PIPE)
        p_seg = subprocess.Popen(seg_cmd, stdin=p_gen.stdout)
        assert p_gen.stdout is not None
        p_gen.stdout.close()
        rc_seg = p_seg.wait()
        rc_gen = p_gen.wait()
        assert rc_gen == 0 and rc_seg == 0, f"Synthetic generation failed: gen={rc_gen} seg={rc_seg}"

        cls._index = SegmentIndex(gap_tolerance_seconds=1.5)
        cls._segments = []
        base_wall = 1000.0
        base_mono = 100.0
        for name, s_pts_s, e_pts_s in csv.reader(csv_path.read_text().splitlines()):
            s_pts, e_pts = float(s_pts_s), float(e_pts_s)
            dur = e_pts - s_pts
            seg_path = cls._seg_dir / name
            seg = VideoSegment(
                segment_id=name,
                path=seg_path,
                session_id=1,
                wall_closed=base_wall + e_pts,
                mono_closed=base_mono + e_pts,
                media_duration=dur,
                ffmpeg_start_pts=s_pts,
                ffmpeg_end_pts=e_pts,
                size_bytes=seg_path.stat().st_size,
            )
            cls._index.add_segment(seg)
            cls._segments.append(seg)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmpdir.cleanup()

    def _assert_av_sync(self, probe: dict, expected_dur: float, *, dur_tol: float = 0.15) -> None:
        self.assertAlmostEqual(probe["fmt_dur"], expected_dur, delta=dur_tol)
        self.assertLessEqual(probe["av_start_diff_ms"], 50.0)
        self.assertLessEqual(probe["av_dur_diff_ms"], 50.0)

    def test_mid_gop_clip_extraction_and_av_sync(self):
        # GOP is 2.0s; start at 5.37s (1.37s into seg_00001, squarely mid-GOP) for 5.0s.
        sel = self._index.query_range(1005.37, 1010.37)
        self.assertFalse(sel.coverage.is_partial)
        out = Path(self._tmpdir.name) / "mid_gop.mp4"
        probe = _extract_reencode(
            sel.segments, sel.approx_trim_start_seconds, sel.target_duration_seconds, out
        )
        self._assert_av_sync(probe, expected_dur=5.0)

    def test_multi_segment_extraction_and_av_sync(self):
        # Span 4 consecutive segments: [1006.5 .. 1018.5] = 12.0s
        sel = self._index.query_range(1006.5, 1018.5)
        self.assertGreaterEqual(len(sel.segments), 4)
        self.assertFalse(sel.coverage.is_partial)
        out = Path(self._tmpdir.name) / "multi_seg.mp4"
        probe = _extract_reencode(
            sel.segments, sel.approx_trim_start_seconds, sel.target_duration_seconds, out
        )
        self._assert_av_sync(probe, expected_dur=12.0)

    def test_missing_segment_gap_extraction_and_av_sync(self):
        # Build a separate SegmentIndex missing seg_00002 (a 4s hole in the middle!)
        gapped_index = SegmentIndex(gap_tolerance_seconds=1.5)
        for idx in (0, 1, 3, 4):
            gapped_index.add_segment(self._segments[idx])

        sel = gapped_index.query_range(1002.0, 1018.0)
        self.assertTrue(sel.coverage.is_partial)
        self.assertEqual(len(sel.coverage.discontinuities), 1)
        out = Path(self._tmpdir.name) / "missing_seg.mp4"
        # Available concatenated media across seg 0, 1, 3, 4 minus 2s trim is ~14s (capped to 14s or span)
        probe = _extract_reencode(
            sel.segments, sel.approx_trim_start_seconds, min(sel.target_duration_seconds, 10.0), out
        )
        self._assert_av_sync(probe, expected_dur=10.0)

    def test_start_and_end_boundary_extraction(self):
        # 1) Start boundary: exact start of first segment (t = 0.0 -> 4.5s)
        first_start = self._segments[0].approx_start_wall
        sel_start = self._index.query_range(first_start, first_start + 4.5)
        self.assertAlmostEqual(sel_start.approx_trim_start_seconds, 0.0, places=2)
        out_start = Path(self._tmpdir.name) / "start_boundary.mp4"
        probe_start = _extract_reencode(
            sel_start.segments,
            sel_start.approx_trim_start_seconds,
            sel_start.target_duration_seconds,
            out_start,
        )
        self._assert_av_sync(probe_start, expected_dur=4.5)

        # 2) End boundary: last 5.0s right up to the end of the final segment
        last_end = self._segments[-1].approx_end_wall
        sel_end = self._index.query_range(last_end - 5.0, last_end)
        self.assertTrue(sel_end.coverage.has_full_postroll)
        out_end = Path(self._tmpdir.name) / "end_boundary.mp4"
        probe_end = _extract_reencode(
            sel_end.segments,
            sel_end.approx_trim_start_seconds,
            sel_end.target_duration_seconds,
            out_end,
        )
        self._assert_av_sync(probe_end, expected_dur=5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
