"""CLI entry point to review and upload processed vertical shorts to YouTube Shorts.

Usage:
    python upload_shorts.py --list
    python upload_shorts.py
    python upload_shorts.py --channel xqc
    python upload_shorts.py --auto
    python upload_shorts.py --clip ce5e4f32
    python upload_shorts.py --min-score 80
"""

from __future__ import annotations

import argparse
import sys

from app.config import ConfigError, load_settings
from app.logging_setup import get_logger
from app.shorts.pipeline import ShortsPipeline

log = get_logger("upload_shorts")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review and upload processed vertical shorts to YouTube Shorts."
    )
    parser.add_argument(
        "--channel",
        help="Filter shorts by streamer channel (e.g. kaicenat, xqc).",
    )
    parser.add_argument(
        "--clip",
        help="Upload a single specific clip by clip ID.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Display all pending processed shorts without uploading.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Upload qualifying shorts without interactive prompts.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        help="Override minimum highlight score threshold for upload.",
    )
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        help="Override upload privacy status (default: from configuration).",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    pipeline = ShortsPipeline(settings=settings)

    min_score = args.min_score if args.min_score is not None else settings.youtube_min_score
    privacy = args.privacy or settings.youtube_privacy_status

    print("\n" + "=" * 65)
    print("  LiveClipper: YouTube Shorts Upload Manager")
    print(f"  Shorts Dir    : {settings.shorts_output_dir}")
    print(f"  Min Score     : {min_score}/100")
    print(f"  Default Priv  : {privacy}")
    if pipeline.uploader and pipeline.uploader.quota_tracker:
        q_status = pipeline.uploader.quota_tracker.get_status()
        print(f"  Daily Quota   : {q_status['uploads_today']}/{q_status['daily_limit']} uploads ({q_status['units_used']}/{q_status['max_units']} units)")
    print("=" * 65 + "\n")

    candidates = pipeline.list_upload_candidates(channel=args.channel, min_score=min_score)
    if args.clip:
        candidates = [c for c in candidates if c.get("clip_id") == args.clip]

    if args.list:
        print(f"Found {len(candidates)} short(s) eligible for upload (min_score >= {min_score}):\n")
        if not candidates:
            print("  (No pending shorts found matching criteria)")
        for idx, c in enumerate(candidates, 1):
            dur = c.get("final_duration") or "unknown"
            viral_t = c.get("selected_viral_title")
            title_display = f"     Title : {c.get('title')}\n"
            if viral_t and viral_t != c.get("analytical_title") and c.get("analytical_title"):
                title_display = f"     Viral : {viral_t}\n     Orig  : {c.get('analytical_title')}\n"
            elif c.get("viral_titles") and isinstance(c.get("viral_titles"), list):
                top_v = c["viral_titles"][0]
                title_display = f"     Viral : {top_v}\n     Orig  : {c.get('title')}\n"
            print(
                f"  {idx}. [{c.get('clip_id')}] ({c.get('channel')}) "
                f"Score: {c.get('highlight_score', 0)}/100 | Dur: {dur}s\n"
                f"{title_display}"
                f"     File  : {c.get('short_video_path')}\n"
            )
        return 0

    if not candidates:
        print("No eligible shorts pending upload matching criteria.")
        return 0

    if not pipeline.uploader:
        print("\n✗ Error: YouTube uploader not configured.")
        print("  Set YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET in backend/.env,")
        print("  or run 'python backend/authorize_youtube.py' first.\n")
        return 2

    # Auto mode is active if --auto flag or YOUTUBE_AUTO_UPLOAD=true in settings
    auto_mode = args.auto or settings.youtube_auto_upload
    interactive_mode = not auto_mode

    summary = pipeline.upload_shorts(
        channel=args.channel,
        min_score=min_score,
        interactive=interactive_mode,
        auto_upload=auto_mode,
        privacy_status=privacy,
        clip_id=args.clip,
    )

    print("\n" + "-" * 65)
    print("  Upload Summary:")
    print(f"    Candidates : {summary['candidates']}")
    print(f"    Uploaded   : {summary['uploaded']}")
    print(f"    Skipped    : {summary['skipped']}")
    print(f"    Rejected   : {summary['rejected']}")
    print(f"    Failed     : {summary['failed']}")
    if summary.get("quota_status"):
        qs = summary["quota_status"]
        print(f"    Remaining  : {qs['daily_limit'] - qs['uploads_today']} uploads remaining today")
    print("-" * 65)

    if summary["items"]:
        print("\nUploaded Shorts:")
        for it in summary["items"]:
            if it.get("youtube_url"):
                print(f"  ✓ [{it.get('clip_id')}] {it.get('title')} -> {it['youtube_url']}")
    print()

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
