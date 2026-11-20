"""Entry point: monitor multiple Twitch channels concurrently with Stage 6 orchestration.

Usage (from the backend/ folder):
    # Monitor specific channels passed as arguments:
    python monitor_streams.py kaicenat xqc

    # Monitor channels specified via TARGET_CHANNELS in .env:
    python monitor_streams.py

    # Auto-discover top live GTA 6 channels and monitor them:
    python monitor_streams.py --auto

    # Override max concurrent streams:
    python monitor_streams.py kaicenat xqc shroud --max-streams 2

    # Quiet mode (silences per-message IRC printouts):
    python monitor_streams.py kaicenat xqc --quiet

Press Ctrl+C or send SIGTERM to stop cleanly (two-phase graceful drain & cleanup).
Requires a prior login (python authorize.py).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any, Callable

from app.config import ConfigError, Settings, load_settings
from app.discovery.stream_finder import DiscoveryError, StreamFinder
from app.discovery.youtube_finder import YouTubeLiveStream, YouTubeStreamFinder, resolve_channel_name
from app.filters.language_filter import LanguageFilter
from app.logging_setup import get_logger
from app.multistream.manager import MultiStreamManager, MultiStreamStatus
from app.multistream.router import normalize_channel
from app.multistream.session import StreamSession
from app.recording import (
    RecorderDependencyError,
    find_ffmpeg_binary,
    find_streamlink_binary,
)
from app.shorts.youtube_auth import YouTubeTokenManager

log = get_logger("monitor_streams")


def parse_channel_list(raw: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Parse comma/space separated channel strings or sequences into normalized list."""
    if not raw:
        return []
    if isinstance(raw, str):
        tokens = raw.replace(",", " ").split()
    else:
        tokens = list(raw)
    result: list[str] = []
    for token in tokens:
        clean = normalize_channel(token)
        if clean and clean not in result:
            result.append(clean)
    return result


def resolve_target_channels(
    cli_channels: list[str] | None = None,
    env_channels: str | list[str] | tuple[str, ...] | None = None,
    *,
    combine_env: bool = False,
) -> list[str]:
    """Resolve target channels from CLI arguments and/or environment variables.

    - If `combine_env` is True, returns CLI channels followed by env channels (deduplicated).
    - If `combine_env` is False, CLI channels take precedence over env channels if present.
    - If only one source is present, its channels are returned.
    """
    cli_list = parse_channel_list(cli_channels)
    env_list = parse_channel_list(env_channels)

    if cli_list and env_list and combine_env:
        combined = list(cli_list)
        for ch in env_list:
            if ch not in combined:
                combined.append(ch)
        return combined

    if cli_list:
        return cli_list
    return env_list


def format_status_line(status: MultiStreamStatus) -> str:
    """Format a compact, informative single-line status summary."""
    active_channels = status.active_channels
    active_str = ", ".join(f"#{ch}" for ch in active_channels) or "none"
    disk_mb = status.disk_budget.total_bytes / (1024 * 1024)
    max_mb = status.disk_budget.max_total_bytes / (1024 * 1024)
    pct = (
        (status.disk_budget.total_bytes / status.disk_budget.max_total_bytes * 100.0)
        if status.disk_budget.max_total_bytes > 0
        else 0.0
    )

    parts = [
        f"Streams ({len(active_channels)}/{status.max_concurrent_streams}): [{active_str}]",
        f"Disk: {disk_mb:.1f}/{max_mb:.0f}MB ({pct:.1f}%)",
    ]
    if status.gemini.enabled:
        parts.append(
            f"Gemini: submitted={status.gemini.submitted_count} "
            f"analyzed={status.gemini.analyzed_count} "
            f"pending={status.gemini.pending_count} "
            f"dropped={status.gemini.dropped_count}"
        )
    if status.liveness is not None:
        parts.append(
            f"Liveness: {status.liveness.online_count} live, "
            f"{status.liveness.offline_count} offline"
        )
    parts.append(
        f"Router: routed={status.routed_verdicts} discarded={status.discarded_verdicts}"
    )
    return " | ".join(parts)


async def _periodic_status_logger(
    manager: MultiStreamManager,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    """Periodically log a high-level summary of active sessions, disk budget, and Gemini queue."""
    if interval_seconds <= 0:
        return
    try:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
                break
            except asyncio.TimeoutError:
                pass

            if not manager.is_running:
                break

            status = manager.status()
            summary = format_status_line(status)
            log.info("[STATUS] %s", summary)
    except asyncio.CancelledError:
        pass


async def run_multi_stream_session(
    manager: MultiStreamManager,
    *,
    status_interval_seconds: float = 30.0,
    gemini_drain_timeout: float = 15.0,
    clip_drain_timeout: float = 30.0,
) -> None:
    """Run MultiStreamManager alongside a periodic status logger until shutdown."""
    manager.install_signal_handlers()
    stop_event = asyncio.Event()
    status_task: asyncio.Task[None] | None = None

    try:
        await manager.start()
        if status_interval_seconds > 0:
            status_task = asyncio.create_task(
                _periodic_status_logger(manager, status_interval_seconds, stop_event)
            )
        await manager.wait_for_shutdown()
    finally:
        stop_event.set()
        if status_task is not None:
            status_task.cancel()
            try:
                await status_task
            except (asyncio.CancelledError, Exception):
                pass
        manager.remove_signal_handlers()
        await manager.stop(
            gemini_drain_timeout=gemini_drain_timeout,
            clip_drain_timeout=clip_drain_timeout,
        )


def build_parser() -> argparse.ArgumentParser:
    """Construct command-line argument parser for monitor_streams.py."""
    parser = argparse.ArgumentParser(
        prog="monitor_streams.py",
        description="Monitor multiple Twitch streams concurrently with LiveClipper.",
    )
    parser.add_argument(
        "channels",
        nargs="*",
        metavar="CHANNEL",
        help="One or more Twitch channel logins to monitor (e.g. kaicenat xqc).",
    )
    parser.add_argument(
        "-c",
        "--channels",
        dest="flag_channels",
        metavar="CHANNELS",
        default="",
        help="Comma- or space-separated list of channel logins.",
    )
    parser.add_argument(
        "--auto",
        dest="auto_discover",
        action="store_true",
        default=False,
        help="Auto-discover top live GTA 6 channels via Twitch Helix API.",
    )
    parser.add_argument(
        "--combine-env",
        "--include-env",
        dest="combine_env",
        action="store_true",
        default=False,
        help="Combine channels from TARGET_CHANNELS in .env with CLI channels.",
    )
    multi_group = parser.add_mutually_exclusive_group()
    multi_group.add_argument(
        "--multi",
        dest="multi_stream_override",
        action="store_const",
        const=True,
        default=None,
        help="Enable multi-stream monitoring (overrides MULTI_STREAM_ENABLED env).",
    )
    multi_group.add_argument(
        "--single",
        dest="multi_stream_override",
        action="store_const",
        const=False,
        default=None,
        help="Force single-stream monitoring (overrides MULTI_STREAM_ENABLED env).",
    )
    parser.add_argument(
        "-m",
        "--max-streams",
        type=int,
        default=None,
        metavar="N",
        help="Override MAX_CONCURRENT_STREAMS (default from .env: 3).",
    )
    parser.add_argument(
        "-s",
        "--status-interval",
        type=float,
        default=30.0,
        metavar="SECS",
        help="Periodic manager status logging interval in seconds (default: 30.0; 0 to disable).",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        dest="quiet",
        action="store_true",
        default=False,
        help="Silence IRC chat message printouts (recommended for multi-stream).",
    )
    parser.add_argument(
        "-v",
        "--chat",
        "--verbose-chat",
        dest="verbose_chat",
        action="store_true",
        default=False,
        help="Print raw incoming IRC chat messages to console.",
    )
    parser.add_argument(
        "--auto-restart",
        dest="auto_restart",
        action="store_true",
        default=False,
        help="Automatically restart sessions if channels go offline and come back online.",
    )
    parser.add_argument(
        "--no-liveness",
        dest="no_liveness",
        action="store_true",
        default=False,
        help="Disable Twitch Helix liveness checking.",
    )
    parser.add_argument(
        "--no-budget",
        dest="no_budget",
        action="store_true",
        default=False,
        help="Disable global disk budget enforcement loop.",
    )
    # Platform flag (defaults to youtube)
    parser.add_argument(
        "--platform",
        default="youtube",
        help="Platform to monitor (default: 'youtube').",
    )
    parser.add_argument(
        "--yt-channels",
        dest="yt_channels",
        default="",
        help="Comma-separated list of YouTube channel IDs to monitor (overrides config).",
    )
    parser.add_argument(
        "--yt-category",
        dest="yt_category",
        default="gaming",
        help="YouTube category or search query to find live streams (default: 'gaming').",
    )
    parser.add_argument(
        "--languages",
        dest="languages",
        default=None,
        help="Comma-separated target languages (e.g. 'en,hi', 'en', 'hi').",
    )
    parser.add_argument(
        "--no-language-filter",
        dest="no_language_filter",
        action="store_true",
        default=False,
        help="Disable language filtering entirely.",
    )
    return parser


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    manager_factory: Callable[..., MultiStreamManager] | None = None,
    stream_finder: StreamFinder | None = None,
    token_checker: Callable[[Settings], str] | None = None,
) -> int:
    """CLI entry point for multi-stream monitoring."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if settings is None:
        try:
            settings = load_settings()
        except ConfigError as exc:
            log.error("Configuration problem: %s", exc)
            return 2

    target_platform = (args.platform or "youtube").lower()
    lang_filter = LanguageFilter(
        target_languages=args.languages or settings.target_languages,
        enabled=not args.no_language_filter and settings.language_filter_enabled,
    )

    if args.multi_stream_override is not None:
        is_multi = args.multi_stream_override
    else:
        is_multi = settings.multi_stream_enabled

    effective_max = (args.max_streams or settings.max_concurrent_streams) if is_multi else 1
    if is_multi:
        log.info("Multi-stream: ENABLED (max %d)", effective_max)
    else:
        log.info("Multi-stream: DISABLED (single stream only)")

    # Check token if a token checker is supplied (e.g. in test suites)
    if token_checker is not None:
        try:
            token_checker(settings)
        except Exception as exc:
            log.error("Authentication check failed: %s", exc)
            return 1

    # Check recording dependencies if recording is enabled.
    if settings.recording_enabled:
        try:
            find_streamlink_binary()
            find_ffmpeg_binary()
        except RecorderDependencyError as exc:
            log.error("Missing recording dependency: %s", exc)
            return 2

    channels: list[str] = []
    custom_sessions: list[StreamSession] = []

    cli_raw: list[str] = list(args.channels)
    if args.flag_channels:
        cli_raw.extend(parse_channel_list(args.flag_channels))

    env_target_channels = settings.target_youtube_channels if settings.target_youtube_channels else settings.target_channels
    resolved_channels = resolve_target_channels(
        cli_channels=cli_raw,
        env_channels=env_target_channels,
        combine_env=args.combine_env,
    )

    if not is_multi and resolved_channels:
        resolved_channels = resolved_channels[:1]

    channels.extend(resolved_channels)

    # Auto-discover streams if requested and no channels provided
    if not channels and args.auto_discover:
        try:
            finder = stream_finder or StreamFinder(settings)
            category, streams = finder.discover()
            if streams:
                accepted_streams = []
                for s in streams:
                    ok, detected_lang, reason = lang_filter.is_stream_accepted(
                        metadata_language=s.language, channel_name=s.channel
                    )
                    if ok:
                        accepted_streams.append(s.channel)
                    else:
                        log.info("Language filter skipped channel #%s (%s)", s.channel, reason)
                channels = accepted_streams[:effective_max]
                log.info(
                    "Auto-discovered %d live stream(s) for %r: %s",
                    len(channels),
                    category,
                    ", ".join(f"#{ch}" for ch in channels),
                )
            else:
                log.error("Auto-discovery found no live streams for category %r.", category)
                return 1
        except DiscoveryError as exc:
            log.error("Auto-discovery failed: %s", exc)
            return 1

    # Resolve YouTube channels if yt_channels specified or if no channels provided yet
    yt_candidates: list[str] = []
    if args.yt_channels:
        yt_candidates = [ch.strip() for ch in args.yt_channels.replace(",", " ").split() if ch.strip()]
    elif not channels and settings.target_youtube_channels:
        yt_candidates = list(settings.target_youtube_channels)

    if yt_candidates:
        token_mgr = None
        if settings.youtube_tokens_path and settings.youtube_tokens_path.is_file():
            token_mgr = YouTubeTokenManager(
                tokens_path=settings.youtube_tokens_path,
                client_id=settings.youtube_client_id,
                client_secret=settings.youtube_client_secret,
            )

        yt_finder = YouTubeStreamFinder(
            token_manager=token_mgr,
            api_key=settings.gemini_api_key,
            priority_channels=yt_candidates,
        )

        yt_streams: list[YouTubeLiveStream] = []
        if yt_candidates:
            try:
                yt_streams = yt_finder.check_priority_channels(yt_candidates)
                if yt_streams:
                    log.info("Found %d active YouTube live priority stream(s).", len(yt_streams))
            except Exception as exc:
                log.warning("YouTube priority channels check failed: %s", exc)

        # If no priority channels live and auto-discovery requested or youtube-only
        if not yt_streams and (args.auto_discover or target_platform == "youtube"):
            try:
                yt_streams = yt_finder.discover_category_streams(
                    query=args.yt_category if args.yt_category != "gaming" else "GTA 6 live",
                    category_id="20",
                )
                if yt_streams:
                    log.info("Found %d active YouTube gaming stream(s).", len(yt_streams))
            except Exception as exc:
                log.warning("YouTube category discovery failed: %s", exc)

        # Apply language filter to YouTube streams
        for ys in yt_streams:
            ok, detected_lang, reason = lang_filter.is_stream_accepted(
                metadata_language=ys.language, channel_name=ys.channel_name
            )
            if ok:
                ch_name = resolve_channel_name(ys.channel_name or ys.channel_id)
                sess = StreamSession(
                    channel=ch_name,
                    settings=settings,
                    platform="youtube",
                    video_id=ys.video_id,
                    live_chat_id=ys.live_chat_id,
                )
                custom_sessions.append(sess)
                log.info("Accepted YouTube stream [%s]: %s (viewers=%d)", ys.channel_name, ys.stream_title, ys.viewer_count)
            else:
                log.info("Language filter skipped YouTube stream [%s]: %s (%s)", ys.channel_name, ys.stream_title, reason)

        if not is_multi:
            if channels:
                custom_sessions = []
            elif custom_sessions:
                custom_sessions = custom_sessions[:1]

    total_target_count = len(channels) + len(custom_sessions)
    if total_target_count == 0:
        if args.auto_discover:
            log.error("Auto-discovery found no live streams to monitor.")
            return 1
        print("\nNo target streams found or specified.")
        print("Usage:")
        print("  python monitor_streams.py <channel1> <channel2> ...")
        print("  python monitor_streams.py --channels kaicenat,xqc")
        print("  python monitor_streams.py --platform youtube --yt-channels UC7cs8q-gJRlGwj4A8OmCmXg")
        print("  python monitor_streams.py --platform all --auto\n")
        return 2

    # Determine chat message visibility:
    # Explicit --quiet disables; explicit --chat enables.
    # When neither is given: True for 1 channel (single-stream equivalence), False for multi-channel.
    if args.quiet:
        print_messages = False
    elif args.verbose_chat:
        print_messages = True
    else:
        print_messages = (total_target_count == 1)

    max_streams = effective_max

    all_targets = [f"#{ch}" for ch in channels] + [f"yt:{s.channel}" for s in custom_sessions]
    log.info(
        "Initializing MultiStreamManager (targets=%d, max_concurrent=%d, disk_budget=%.0fMB, "
        "recording=%s, quiet=%s).",
        total_target_count,
        max_streams,
        settings.max_total_buffer_mb,
        settings.recording_enabled,
        not print_messages,
    )
    log.info("Target streams: %s", ", ".join(all_targets))

    factory = manager_factory or MultiStreamManager
    manager = factory(
        settings,
        channels=channels,
        max_concurrent_streams=max_streams,
        print_messages=print_messages,
        auto_restart_online=args.auto_restart,
        enable_liveness_check=not args.no_liveness and target_platform in ("twitch", "all"),
        enable_disk_budget=not args.no_budget,
        stream_finder=stream_finder,
        custom_sessions=custom_sessions,
    )

    try:
        asyncio.run(
            run_multi_stream_session(
                manager,
                status_interval_seconds=args.status_interval,
            )
        )
    except KeyboardInterrupt:
        print()  # tidy the ^C line
        log.info("Interrupted by user. Bye!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
