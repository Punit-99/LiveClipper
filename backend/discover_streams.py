"""Entry point: discover and display currently-live GTA 6 streams.

Run it from the backend/ folder:
    python discover_streams.py

It prints a numbered table of live streams. Phase 1 stops there  later we'll
feed a chosen channel into the chat monitor.
"""

from __future__ import annotations

import sys

from app.config import ConfigError, load_settings
from app.discovery.stream_finder import DiscoveryError, LiveStream, StreamFinder
from app.logging_setup import get_logger

log = get_logger("discover")


def _print_table(category: str, streams: list[LiveStream]) -> None:
    if not streams:
        print(f"\nNo live streams found right now for category: {category!r}.")
        print("Nobody may be streaming it at the moment  try again later, or")
        print("set a different GTA6_CATEGORY_NAME in your .env to test the pipeline.\n")
        return

    print(f"\nLive streams for category: {category!r}")
    print("=" * 78)
    # Header
    print(f"{'#':>2}  {'CHANNEL':<20} {'VIEWERS':>8}  {'LANG':<5} TITLE")
    print("-" * 78)
    for i, s in enumerate(streams, start=1):
        title = s.title if len(s.title) <= 32 else s.title[:29] + "..."
        print(f"{i:>2}  {s.display_name:<20} {s.viewer_count:>8,}  {s.language:<5} {title}")
    print("-" * 78)
    print("URLs:")
    for i, s in enumerate(streams, start=1):
        print(f"{i:>2}. {s.url}")
    print()


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("Configuration problem: %s", exc)
        return 2

    try:
        finder = StreamFinder(settings)
        category, streams = finder.discover()
    except DiscoveryError as exc:
        log.error("Discovery failed: %s", exc)
        return 1

    _print_table(category, streams)
    log.info("Done. Found %d live stream(s).", len(streams))
    return 0


if __name__ == "__main__":
    sys.exit(main())
