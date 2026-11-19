"""CLI entry point to process horizontal LiveClipper clips into 9:16 vertical shorts.

Usage:
    python process_shorts.py
    python process_shorts.py --channel kaicenat
    python process_shorts.py --clip ce5e4f32
    python process_shorts.py --dry-run
    python process_shorts.py --engine ffmpeg_fallback
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import ConfigError, load_settings
from app.logging_setup import get_logger
from app.shorts.openshorts_bridge import OpenShortsBridge
from app.shorts.pipeline import ShortsPipeline

log = get_logger("process_shorts")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process LiveClipper clips into vertical YouTube Shorts."
    )
    parser.add_argument(
        "--channel",
        help="Process only clips for a specific channel (e.g. kaicenat, xqc).",
    )
    parser.add_argument(
        "--clip",
        help="Process a single specific clip by clip ID or filename.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what clips would be processed without generating any files.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-process clips even if they were previously processed.",
    )
    parser.add_argument(
        "--engine",
        choices=["auto", "openshorts", "ffmpeg_fallback"],
        help="Override reframing engine (default: from configuration).",
    )
    parser.add_argument(
        "--subtitle-style",
        help="Override subtitle styling preset (e.g. bold, default, hormozi, pill, clean, none).",
    )
    parser.add_argument(
        "--reframe-mode",
        choices=["auto", "split", "track", "wide"],
        help="Override vertical reframing mode (auto, split, track, wide).",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    # Override engine if requested
    engine = args.engine or settings.shorts_engine
    sub_style = args.subtitle_style or settings.openshorts_subtitle_style
    reframe_mode = args.reframe_mode or settings.openshorts_reframe_mode
    bridge = OpenShortsBridge(
        output_dir=settings.shorts_output_dir,
        engine=engine,
        max_duration=settings.shorts_max_duration,
        docker_image=settings.openshorts_docker_image,
        docker_timeout=settings.openshorts_docker_timeout,
        subtitle_style=sub_style,
        reframe_mode=reframe_mode,
        output_bitrate=settings.shorts_output_bitrate,
        output_crf=settings.shorts_output_crf,
    )

    pipeline = ShortsPipeline(
        settings=settings,
        bridge=bridge,
    )

    print("\n" + "=" * 65)
    print("  LiveClipper: Shorts Processing Engine")
    print(f"  Clips Dir  : {settings.clips_output_dir}")
    print(f"  Shorts Dir : {settings.shorts_output_dir}")
    print(f"  Engine     : {engine}")
    if engine in ("auto", "openshorts"):
        print(f"  Docker Img : {settings.openshorts_docker_image}")
        print(f"  Subtitles  : {sub_style}")
        print(f"  Reframe    : {reframe_mode}")
    if args.channel:
        print(f"  Channel    : {args.channel}")
    if args.clip:
        print(f"  Clip Filter: {args.clip}")
    if args.dry_run:
        print("  Mode       : DRY RUN (no files modified)")
    print("=" * 65 + "\n")

    summary = pipeline.process_pending_shorts(
        channel=args.channel,
        clip_id_or_file=args.clip,
        dry_run=args.dry_run,
        force=args.force,
    )

    print("\n" + "-" * 65)
    print("  Processing Summary:")
    print(f"    Total clips found : {summary['found']}")
    print(f"    Candidates        : {summary['candidates']}")
    print(f"    Processed         : {summary['processed']}")
    print(f"    Skipped (cached)  : {summary['skipped']}")
    print(f"    Failed            : {summary['failed']}")
    print("-" * 65)

    if summary["items"]:
        print("\nProcessed Clips:")
        for it in summary["items"]:
            status_icon = "✓" if it.get("state") == "PROCESSED" else "✗"
            print(
                f"  {status_icon} [{it.get('clip_id')}] ({it.get('channel')}) "
                f"Score: {it.get('highlight_score', 0)}/100 | "
                f"Title: {it.get('title', '')}"
            )
            if it.get("error"):
                print(f"      Error: {it['error']}")
    print()

    return 1 if summary["failed"] > 0 and summary["processed"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
