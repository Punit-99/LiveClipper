"""Central state and supervisor for LiveClipper background operations."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.analysis.models import AnalyzedHighlight
from app.config import Settings, load_settings
from app.discovery.stream_finder import StreamFinder
from app.discovery.youtube_finder import (
    YouTubeLiveStream,
    YouTubeStreamFinder,
    check_youtube_channel_live_status,
    resolve_channel_name,
)
from app.filters.language_filter import LanguageFilter
from app.multistream.manager import MultiStreamManager
from app.multistream.session import SessionState, StreamSession
from app.recording.clip_extractor import ClipExtractionResult
from app.shorts.pipeline import ShortsPipeline
from app.shorts.youtube_auth import YouTubeTokenManager
from app.shorts.youtube_uploader import DailyQuotaTracker, YouTubeUploader
from app.api.events import ws_manager

log = logging.getLogger("app.api.state")


def _get_dir_size_bytes(directory: Path | str) -> int:
    """Recursively calculate the total size of files inside a directory."""
    path = Path(directory)
    if not path.is_dir():
        return 0
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


class MonitoringSupervisor:
    """Supervises the MultiStreamManager lifecycle and broadcasts real-time updates."""

    def __init__(self) -> None:
        self.settings: Settings = load_settings()
        self.manager: MultiStreamManager | None = None
        self._task: asyncio.Task[None] | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._start_time: float | None = None
        self._lock = asyncio.Lock()
        self.active_platform: str = "none"
        self.active_channels: list[str] = []
        self.discovered_fallback_streams: list[dict[str, Any]] = []
        self.waiting_for_targets: bool = False
        self.monitored_channels_meta: dict[str, dict[str, Any]] = {}
        self.live_stats: dict[str, Any] = {
            "segments": 0,
            "chat_messages": 0,
            "highlights_detected": 0,
            "highlights_approved": 0,
            "highlights_rejected": 0,
            "clips_extracted": 0,
            "duration_seconds": 0.0,
            "buffer_mb": 0.0,
        }

    @property
    def is_running(self) -> bool:
        return self.manager is not None and self.manager.is_running

    def get_live_stats(self) -> dict[str, Any]:
        """Return current live counters and buffer consumption."""
        dur = 0.0
        if self._start_time and self.is_running:
            dur = time.time() - self._start_time

        buffer_dir = self.settings.recording_buffer_dir
        buf_bytes = _get_dir_size_bytes(buffer_dir)
        buf_mb = round(buf_bytes / (1024 * 1024), 1)

        # Count total registered segments from active sessions
        total_segments = self.live_stats.get("segments", 0)
        if self.manager and self.is_running:
            mgr_status = self.manager.status()
            sess_segs = sum(s.segments_registered for s in mgr_status.sessions.values())
            if sess_segs > total_segments:
                total_segments = sess_segs

        return {
            **self.live_stats,
            "segments": total_segments,
            "duration_seconds": round(dur, 1),
            "buffer_mb": buf_mb,
        }

    def _on_extracted(self, result: ClipExtractionResult) -> None:
        """Callback when FFmpeg finishes extracting a new clip."""
        channel_name = "unknown"
        clip_id = result.event_id[:8] if result.event_id else "unknown"

        if result.status == "extracted" and result.clip_path:
            sidecar_data: dict[str, Any] = {}
            if result.metadata_path and Path(result.metadata_path).is_file():
                try:
                    with open(result.metadata_path, "r", encoding="utf-8") as fh:
                        sidecar_data = json.load(fh)
                except Exception:
                    pass

            analysis = sidecar_data.get("analysis") or {}
            channel_name = resolve_channel_name(
                sidecar_data.get("streamer_name") or sidecar_data.get("stream_id") or "unknown"
            )
            self.live_stats["clips_extracted"] = self.live_stats.get("clips_extracted", 0) + 1

            event_payload = {
                "event_id": result.event_id,
                "clip_id": clip_id,
                "clip_path": str(result.clip_path),
                "filename": Path(result.clip_path).name,
                "channel": channel_name,
                "platform": sidecar_data.get("platform") or "twitch",
                "title": analysis.get("title") or "Stream Highlight",
                "highlight_score": int(analysis.get("highlight_score", 0)),
                "category": analysis.get("category") or "highlight",
                "duration": float(result.metadata.total_media_duration if result.metadata else 0.0),
                "extracted_at": time.time(),
                "message": f"New clip extracted for {channel_name}: {analysis.get('title') or 'Stream Highlight'}",
            }
            log.info("Broadcasting clip extracted event for %s", clip_id)
            ws_manager.broadcast_sync("clip_extracted", event_payload)
            ws_manager.broadcast_sync("stats_update", self.get_live_stats())
        elif result.status == "failed":
            log.warning("Clip extraction failed for event %s: %s", result.event_id, result.error_message)
            ws_manager.broadcast_sync("clip_extraction_failed", {
                "clip_id": clip_id,
                "error": result.error_message or "Clip extraction failed",
                "message": f"Clip extraction failed: {result.error_message or 'Unknown error'}",
            })

    def _on_verdict(self, analyzed: AnalyzedHighlight) -> None:
        """Callback when Gemini analysis produces a highlight verdict."""
        stream_id = getattr(analyzed.candidate, "stream_id", "unknown")
        ch_name = resolve_channel_name(stream_id)
        score = int(getattr(analyzed, "highlight_score", 0))
        min_score = int(getattr(self.settings, "gemini_min_clip_score", 65))
        approved = score >= min_score
        title = getattr(analyzed, "title", "Highlight")
        reasoning = getattr(analyzed, "reasoning", "")

        if approved:
            self.live_stats["highlights_approved"] = self.live_stats.get("highlights_approved", 0) + 1
            log.info("Gemini approved highlight on %s (Score: %d): %s", ch_name, score, title)
            ws_manager.broadcast_sync("gemini_verdict", {
                "channel": ch_name,
                "approved": True,
                "score": score,
                "title": title,
                "reasoning": reasoning,
                "message": f"Gemini approved highlight on {ch_name} (Score: {score}): {title}",
            })
            # Also notify that extraction is starting
            ws_manager.broadcast_sync("clip_extracting", {
                "channel": ch_name,
                "title": title,
                "score": score,
                "message": f"Extracting highlight clip for {ch_name}...",
            })
        else:
            self.live_stats["highlights_rejected"] = self.live_stats.get("highlights_rejected", 0) + 1
            log.info("Gemini rejected highlight candidate on %s (Score: %d)", ch_name, score)
            ws_manager.broadcast_sync("gemini_verdict", {
                "channel": ch_name,
                "approved": False,
                "score": score,
                "title": title,
                "reasoning": reasoning,
                "message": f"Gemini rejected candidate on {ch_name} (Score: {score})",
            })
        ws_manager.broadcast_sync("stats_update", self.get_live_stats())

    def _on_state_change(self, session: StreamSession, old_state: SessionState, new_state: SessionState) -> None:
        """Callback when a stream session changes state."""
        ch_name = resolve_channel_name(session.channel)
        ws_manager.broadcast_sync(
            "session_state",
            {
                "channel": ch_name,
                "platform": session.platform,
                "old_state": old_state.value,
                "new_state": new_state.value,
            },
        )
        if new_state == SessionState.RUNNING:
            ws_manager.broadcast_sync("recording_started", {
                "channel": ch_name,
                "platform": session.platform,
                "message": f"Started recording stream for {ch_name}",
            })
        elif new_state in (SessionState.ERRORED, SessionState.STOPPED) and old_state == SessionState.RUNNING:
            ws_manager.broadcast_sync("stream_offline", {
                "channel": ch_name,
                "platform": session.platform,
                "message": f"Stream {ch_name} went offline or stopped recording.",
            })

    async def start(
        self,
        platform: str | None = None,
        channels: list[str] | None = None,
        languages: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start monitoring live streams with explicit or configured channels."""
        async with self._lock:
            if self.is_running:
                return {"status": "already_running", "channels": self.active_channels}

            # Reload settings from disk
            self.settings = load_settings()
            self.discovered_fallback_streams = []
            self.waiting_for_targets = False
            self.monitored_channels_meta = {}

            target_platform = (platform or self.settings.monitor_platform or "all").lower()
            effective_channels = [c.strip() for c in (channels or []) if c and c.strip()]

            # Determine candidates
            yt_candidates: list[str] = []
            twitch_candidates: list[str] = []

            if effective_channels:
                for c in effective_channels:
                    if c.startswith("UC") or target_platform == "youtube":
                        yt_candidates.append(c)
                    else:
                        twitch_candidates.append(c.lstrip("#").lower())
            else:
                if target_platform in ("youtube", "all"):
                    yt_candidates = list(self.settings.target_youtube_channels)
                if target_platform in ("twitch", "all"):
                    twitch_candidates = [c.lstrip("#").lower() for c in self.settings.target_channels]

            # 1. Check YouTube targets
            live_yt_sessions: list[StreamSession] = []
            token_mgr = None
            if self.settings.youtube_tokens_path and self.settings.youtube_tokens_path.is_file():
                try:
                    token_mgr = YouTubeTokenManager(
                        tokens_path=self.settings.youtube_tokens_path,
                        client_id=self.settings.youtube_client_id,
                        client_secret=self.settings.youtube_client_secret,
                    )
                except Exception:
                    pass

            for cid in yt_candidates:
                yt_status = check_youtube_channel_live_status(
                    channel_id=cid,
                    token_manager=token_mgr,
                    api_key=self.settings.gemini_api_key,
                )
                if yt_status.get("is_live"):
                    c_name = resolve_channel_name(yt_status.get("name") or cid)
                    sess = StreamSession(
                        channel=c_name,
                        settings=self.settings,
                        platform="youtube",
                        video_id=yt_status.get("video_id"),
                    )
                    live_yt_sessions.append(sess)
                    self.monitored_channels_meta[c_name] = {
                        "badge": "Target",
                        "is_fallback": False,
                        "platform": "youtube",
                        "viewers": yt_status.get("viewers", 0),
                        "start_time": time.time(),
                    }

            # 2. Check Twitch targets
            live_twitch_channels: list[str] = []
            if twitch_candidates:
                try:
                    finder = StreamFinder(self.settings)
                    t_res = finder.check_live_channels(twitch_candidates)
                    for ch, s_live in t_res.items():
                        if s_live:
                            ch_disp = s_live.display_name or ch
                            live_twitch_channels.append(ch)
                            self.monitored_channels_meta[ch] = {
                                "badge": "Target",
                                "is_fallback": False,
                                "platform": "twitch",
                                "viewers": s_live.viewer_count,
                                "start_time": time.time(),
                            }
                except Exception as exc:
                    log.warning("Twitch live check failed during start: %s", exc)

            # If user explicitly supplied selected channels that are considered active, respect them
            if effective_channels and not live_yt_sessions and not live_twitch_channels:
                # User specifically chose channels in modal; monitor those directly
                for c in effective_channels:
                    if c.startswith("UC") or target_platform == "youtube":
                        sess = StreamSession(channel=resolve_channel_name(c), settings=self.settings, platform="youtube")
                        live_yt_sessions.append(sess)
                        self.monitored_channels_meta[sess.channel] = {
                            "badge": "Target",
                            "is_fallback": False,
                            "platform": "youtube",
                            "viewers": 0,
                            "start_time": time.time(),
                        }
                    else:
                        ch_norm = c.lstrip("#").lower()
                        live_twitch_channels.append(ch_norm)
                        self.monitored_channels_meta[ch_norm] = {
                            "badge": "Target",
                            "is_fallback": False,
                            "platform": "twitch",
                            "viewers": 0,
                            "start_time": time.time(),
                        }

            # Enforce max concurrent streams
            max_allowed = (
                self.settings.max_concurrent_streams
                if self.settings.multi_stream_enabled
                else 1
            )

            total_live_targets = len(live_yt_sessions) + len(live_twitch_channels)

            # Case A: Live target channels found -> start recording target streams
            if total_live_targets > 0:
                selected_yt = live_yt_sessions[:max_allowed]
                rem = max(0, max_allowed - len(selected_yt))
                selected_twitch = live_twitch_channels[:rem]

                return await self._launch_manager(
                    twitch_channels=selected_twitch,
                    custom_sessions=selected_yt,
                    platform=target_platform,
                )

            # Case B: NO target channels are live -> Enter Fallback Discovery (Mode B)
            target_names = [resolve_channel_name(c) for c in (yt_candidates + twitch_candidates)]
            offline_msg = f"Target channels offline: {', '.join(target_names) if target_names else 'None configured'}"
            log.info(offline_msg)
            ws_manager.broadcast_sync("live_log", {
                "level": "info",
                "message": offline_msg,
            })

            discovered_alternatives = await self._discover_fallback_streams(target_platform)

            if discovered_alternatives:
                for alt in discovered_alternatives[:5]:
                    c_name = alt.get("channel_name") or alt.get("channel_id")
                    v_num = alt.get("viewer_count", 0)
                    v_str = f"{v_num // 1000}k" if v_num >= 1000 else str(v_num)
                    alt_msg = f"Discovered alternatives: {c_name} ({v_str} viewers, {alt.get('category', 'Gaming')}, {alt.get('language', 'en')})"
                    log.info(alt_msg)
                    ws_manager.broadcast_sync("live_log", {
                        "level": "info",
                        "message": alt_msg,
                    })

                # Requirement 6: Auto-approve option
                if self.settings.auto_approve_fallback:
                    top_stream = discovered_alternatives[0]
                    c_name = top_stream.get("channel_name") or top_stream.get("channel_id")
                    v_count = top_stream.get("viewer_count", 0)
                    log.info("Auto-approving fallback stream '%s' (%d viewers).", c_name, v_count)
                    ws_manager.broadcast_sync("live_log", {
                        "level": "info",
                        "message": f"Auto-approving fallback stream '{c_name}' ({v_count} viewers).",
                    })
                    return await self._start_with_fallback_stream(top_stream)

                # Requirement 1 & 5: Waiting for user approval
                wait_msg = "Waiting for user approval or target channel to go live..."
                log.info(wait_msg)
                ws_manager.broadcast_sync("live_log", {
                    "level": "info",
                    "message": wait_msg,
                })
                self.discovered_fallback_streams = discovered_alternatives
                ws_manager.broadcast_sync("fallback_streams_discovered", {
                    "message": "None of your target channels are live. Found these live streams instead:",
                    "streams": discovered_alternatives,
                })
                return {
                    "status": "awaiting_fallback_approval",
                    "message": "None of your target channels are live. Found these live streams instead:",
                    "streams": discovered_alternatives,
                }

            # Case C: No alternative streams discovered either
            log.info("No alternative streams found. Polling for target channels to come online...")
            self.waiting_for_targets = True
            self._start_target_polling_task(yt_candidates, twitch_candidates)
            return {
                "status": "waiting_for_targets",
                "message": "Target channels are offline and no alternative streams were found. Polling targets.",
            }

    async def _discover_fallback_streams(self, platform: str) -> list[dict[str, Any]]:
        """Run Mode B Category Discovery on YouTube and/or Twitch."""
        discovered: list[dict[str, Any]] = []

        # YouTube category discovery
        if platform in ("youtube", "all"):
            try:
                token_mgr = None
                if self.settings.youtube_tokens_path and self.settings.youtube_tokens_path.is_file():
                    try:
                        token_mgr = YouTubeTokenManager(
                            tokens_path=self.settings.youtube_tokens_path,
                            client_id=self.settings.youtube_client_id,
                            client_secret=self.settings.youtube_client_secret,
                        )
                    except Exception:
                        pass

                yt_finder = YouTubeStreamFinder(
                    token_manager=token_mgr,
                    api_key=self.settings.gemini_api_key,
                )
                cat_streams = yt_finder.discover_category_streams(
                    query=self.settings.category_name or "GTA 6 live",
                    min_viewers=20,
                    max_results=8,
                )
                for s in cat_streams:
                    if s.channel_id in self.settings.excluded_youtube_channels:
                        continue
                    discovered.append({
                        "channel_id": s.channel_id,
                        "channel_name": resolve_channel_name(s.channel_name or s.channel_id),
                        "platform": "youtube",
                        "viewer_count": s.viewer_count,
                        "stream_title": s.stream_title,
                        "category": self.settings.category_name,
                        "language": s.language or "en",
                        "video_id": s.video_id,
                    })
            except Exception as exc:
                log.warning("YouTube category discovery failed: %s", exc)

        # Twitch category discovery
        if platform in ("twitch", "all"):
            try:
                finder = StreamFinder(self.settings)
                _, t_streams = finder.discover()
                for ts in t_streams:
                    if ts.channel.lower() in [e.lower() for e in self.settings.excluded_target_channels]:
                        continue
                    discovered.append({
                        "channel_id": ts.channel,
                        "channel_name": ts.display_name or ts.channel,
                        "platform": "twitch",
                        "viewer_count": ts.viewer_count,
                        "stream_title": ts.title,
                        "category": self.settings.category_name,
                        "language": ts.language or "en",
                    })
            except Exception as exc:
                log.warning("Twitch category discovery failed: %s", exc)

        # Sort descending by viewer count
        discovered.sort(key=lambda s: int(s.get("viewer_count", 0)), reverse=True)
        return discovered

    async def _start_with_fallback_stream(self, stream_info: dict[str, Any]) -> dict[str, Any]:
        """Start monitoring a single approved fallback stream."""
        c_name = stream_info.get("channel_name") or stream_info.get("channel_id") or "fallback_stream"
        plat = stream_info.get("platform", "twitch")
        v_count = stream_info.get("viewer_count", 0)

        self.monitored_channels_meta[c_name] = {
            "badge": "Fallback",
            "is_fallback": True,
            "platform": plat,
            "viewers": v_count,
            "start_time": time.time(),
        }

        if plat == "youtube":
            sess = StreamSession(
                channel=c_name,
                settings=self.settings,
                platform="youtube",
                video_id=stream_info.get("video_id"),
            )
            return await self._launch_manager(twitch_channels=[], custom_sessions=[sess], platform="youtube")
        else:
            return await self._launch_manager(twitch_channels=[stream_info.get("channel_id", c_name)], custom_sessions=[], platform="twitch")

    async def _launch_manager(
        self,
        twitch_channels: list[str],
        custom_sessions: list[StreamSession],
        platform: str,
    ) -> dict[str, Any]:
        """Internal helper to initialize and run MultiStreamManager."""
        self.active_platform = platform
        self.active_channels = twitch_channels + [s.channel for s in custom_sessions]
        self._start_time = time.time()

        max_streams = (
            self.settings.max_concurrent_streams
            if self.settings.multi_stream_enabled
            else 1
        )

        self.manager = MultiStreamManager(
            settings=self.settings,
            channels=twitch_channels,
            max_concurrent_streams=max_streams,
            print_messages=False,
            auto_restart_online=True,
            enable_liveness_check=(platform in ("twitch", "all")),
            enable_disk_budget=True,
            custom_sessions=custom_sessions,
            on_extracted=self._on_extracted,
            on_state_change=self._on_state_change,
            on_verdict=self._on_verdict,
        )

        await self.manager.start()

        async def _run_manager() -> None:
            try:
                await self.manager.wait_for_shutdown()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.error("Monitoring task encountered unexpected error: %s", e)

        self._task = asyncio.create_task(_run_manager())

        status_data = self.get_status()
        ws_manager.broadcast_sync("monitoring_started", status_data)
        for ch in self.active_channels:
            meta = self.monitored_channels_meta.get(ch, {})
            ws_manager.broadcast_sync("recording_started", {
                "channel": ch,
                "platform": meta.get("platform", platform),
                "badge": meta.get("badge", "Target"),
                "message": f"Started recording stream for {ch}",
            })

        return {"status": "started", "platform": platform, "channels": self.active_channels}

    def _start_target_polling_task(
        self,
        yt_candidates: list[str] | None = None,
        twitch_candidates: list[str] | None = None,
    ) -> None:
        """Start a periodic background polling loop checking if any target channel went live."""
        if self._poll_task and not self._poll_task.done():
            return

        y_targets = list(yt_candidates or self.settings.target_youtube_channels)
        t_targets = list(twitch_candidates or self.settings.target_channels)
        poll_interval = self.settings.liveness_poll_seconds or 60.0

        async def _poll_loop() -> None:
            while self.waiting_for_targets:
                try:
                    await asyncio.sleep(poll_interval)
                    if not self.waiting_for_targets:
                        break

                    # Check YouTube targets
                    for cid in y_targets:
                        st = check_youtube_channel_live_status(
                            channel_id=cid,
                            api_key=self.settings.gemini_api_key,
                        )
                        if st.get("is_live"):
                            c_name = resolve_channel_name(st.get("name") or cid)
                            log.info("Target YouTube channel '%s' is now LIVE! Starting recording...", c_name)
                            ws_manager.broadcast_sync("live_log", {
                                "level": "info",
                                "message": f"Target YouTube channel '{c_name}' came online! Starting recording...",
                            })
                            ws_manager.broadcast_sync("target_channel_online", {"channel": c_name, "platform": "youtube"})
                            self.waiting_for_targets = False
                            sess = StreamSession(channel=c_name, settings=self.settings, platform="youtube", video_id=st.get("video_id"))
                            self.monitored_channels_meta[c_name] = {
                                "badge": "Target",
                                "is_fallback": False,
                                "platform": "youtube",
                                "viewers": st.get("viewers", 0),
                                "start_time": time.time(),
                            }
                            await self._launch_manager(twitch_channels=[], custom_sessions=[sess], platform="youtube")
                            return

                    # Check Twitch targets
                    if t_targets:
                        try:
                            f = StreamFinder(self.settings)
                            t_live = f.check_live_channels(t_targets)
                            for ch, live_stream in t_live.items():
                                if live_stream:
                                    log.info("Target Twitch channel '%s' is now LIVE! Starting recording...", ch)
                                    ws_manager.broadcast_sync("live_log", {
                                        "level": "info",
                                        "message": f"Target Twitch channel '{ch}' came online! Starting recording...",
                                    })
                                    ws_manager.broadcast_sync("target_channel_online", {"channel": ch, "platform": "twitch"})
                                    self.waiting_for_targets = False
                                    self.monitored_channels_meta[ch] = {
                                        "badge": "Target",
                                        "is_fallback": False,
                                        "platform": "twitch",
                                        "viewers": live_stream.viewer_count,
                                        "start_time": time.time(),
                                    }
                                    await self._launch_manager(twitch_channels=[ch], custom_sessions=[], platform="twitch")
                                    return
                        except Exception:
                            pass
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    log.debug("Target polling iteration failed: %s", exc)

        self._poll_task = asyncio.create_task(_poll_loop())

    async def approve_fallback(self, channel_id: str) -> dict[str, Any]:
        """Approve a discovered fallback stream and start monitoring it."""
        async with self._lock:
            stream = None
            clean_id = (channel_id or "").strip()
            for s in self.discovered_fallback_streams:
                if (
                    s.get("channel_id") == clean_id
                    or str(s.get("channel_name", "")).lower() == clean_id.lower()
                    or str(s.get("id", "")).lower() == clean_id.lower()
                ):
                    stream = s
                    break

            if not stream:
                # Use as-is
                stream = {
                    "channel_id": clean_id,
                    "channel_name": resolve_channel_name(clean_id),
                    "platform": "youtube" if clean_id.startswith("UC") else "twitch",
                    "viewer_count": 0,
                    "stream_title": "Approved Fallback Stream",
                    "category": self.settings.category_name,
                    "language": "en",
                }

            c_name = stream.get("channel_name") or stream.get("channel_id")
            log.info("Fallback stream '%s' approved by user. Starting recording...", c_name)
            ws_manager.broadcast_sync("live_log", {
                "level": "info",
                "message": f"Fallback stream '{c_name}' approved by user. Starting recording...",
            })

            # Clear fallback state and cancel polling
            self.discovered_fallback_streams = []
            self.waiting_for_targets = False
            if self._poll_task and not self._poll_task.done():
                self._poll_task.cancel()

            res = await self._start_with_fallback_stream(stream)
            ws_manager.broadcast_sync("fallback_stream_approved", {
                "channel": c_name,
                "platform": stream.get("platform", "twitch"),
            })
            return res

    async def wait_for_targets(self) -> dict[str, Any]:
        """User chose to skip alternatives and wait for target channels."""
        async with self._lock:
            self.discovered_fallback_streams = []
            self.waiting_for_targets = True
            poll_secs = self.settings.liveness_poll_seconds or 60.0

            log.info("User chose to wait for target channels. Polling targets every %.1fs...", poll_secs)
            ws_manager.broadcast_sync("live_log", {
                "level": "info",
                "message": f"User chose to wait for target channels. Polling targets every {poll_secs:.0f}s...",
            })
            ws_manager.broadcast_sync("waiting_target_channels", {
                "message": f"User chose to wait for target channels. Polling targets every {poll_secs:.0f}s...",
            })

            self._start_target_polling_task()
            return {
                "status": "waiting_for_targets",
                "message": f"Polling target channels every {poll_secs:.0f}s...",
            }

    async def stop(self) -> dict[str, Any]:
        """Gracefully stop monitoring."""
        async with self._lock:
            self.waiting_for_targets = False
            self.discovered_fallback_streams = []
            if self._poll_task and not self._poll_task.done():
                self._poll_task.cancel()
                self._poll_task = None

            if not self.is_running or self.manager is None:
                return {"status": "not_running"}

            await self.manager.stop()
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

            stopped_channels = list(self.active_channels)
            self.manager = None
            self._task = None
            self._start_time = None
            self.active_channels = []

            status_data = self.get_status()
            ws_manager.broadcast_sync("monitoring_stopped", status_data)
            for ch in stopped_channels:
                ws_manager.broadcast_sync("recording_stopped", {
                    "channel": ch,
                    "message": f"Recording stopped for {ch}.",
                })
            return {"status": "stopped"}

    def get_status(self) -> dict[str, Any]:
        """Collect and return full system status snapshot."""
        is_mon = self.is_running
        mgr = self.manager
        mgr_status = mgr.status() if (mgr and is_mon) else None

        # Disk usage calculations
        clips_dir = self.settings.clips_output_dir
        buffer_dir = self.settings.recording_buffer_dir
        shorts_dir = self.settings.shorts_output_dir

        clips_bytes = _get_dir_size_bytes(clips_dir)
        buffer_bytes = _get_dir_size_bytes(buffer_dir)
        shorts_bytes = _get_dir_size_bytes(shorts_dir)
        total_disk_bytes = clips_bytes + buffer_bytes + shorts_bytes

        # System disk free space
        disk_free_bytes = 0
        disk_total_bytes = 0
        try:
            usage = shutil.disk_usage(clips_dir if clips_dir.exists() else Path.cwd())
            disk_free_bytes = usage.free
            disk_total_bytes = usage.total
        except Exception:
            pass

        # Gemini statistics
        gemini_stats = {
            "enabled": self.settings.gemini_enabled,
            "model": self.settings.gemini_model,
            "submitted": mgr_status.gemini.submitted_count if mgr_status else 0,
            "analyzed": mgr_status.gemini.analyzed_count if mgr_status else 0,
            "failed": mgr_status.gemini.failed_count if mgr_status else 0,
            "dropped": mgr_status.gemini.dropped_count if mgr_status else 0,
            "pending": mgr_status.gemini.pending_count if mgr_status else 0,
        }

        # Active channels & sessions with badge, viewers, platform, and duration
        active_ch = mgr_status.active_channels if mgr_status else []
        sessions_data = {}
        if mgr_status:
            for ch, s_snap in mgr_status.sessions.items():
                resolved_ch = resolve_channel_name(ch)
                meta = self.monitored_channels_meta.get(ch) or self.monitored_channels_meta.get(resolved_ch) or {}
                badge = meta.get("badge", "Target")
                is_fallback = meta.get("is_fallback", False)
                plat = meta.get("platform", "youtube" if ch.startswith("UC") else "twitch")
                viewers = meta.get("viewers", 0)
                started_at = meta.get("start_time", self._start_time or time.time())
                dur = time.time() - started_at if started_at else 0.0

                sessions_data[resolved_ch] = {
                    "state": s_snap.state.value,
                    "segments_registered": s_snap.segments_registered,
                    "buffer_bytes": s_snap.buffer_bytes,
                    "clips_extracted": s_snap.clips_extracted,
                    "clips_failed": s_snap.clips_failed,
                    "error_message": s_snap.error_message,
                    "badge": badge,
                    "is_fallback": is_fallback,
                    "platform": plat,
                    "viewers": viewers,
                    "duration_seconds": round(dur, 1),
                }

        return {
            "is_monitoring": is_mon,
            "waiting_target_channels": self.waiting_for_targets,
            "fallback_streams": self.discovered_fallback_streams,
            "uptime_seconds": (time.time() - self._start_time) if self._start_time else 0.0,
            "live_stats": self.get_live_stats(),
            "platform": self.active_platform if is_mon else self.settings.monitor_platform,
            "target_channels": [
                resolve_channel_name(c)
                for c in (
                    self.active_channels
                    if is_mon
                    else (
                        list(self.settings.target_youtube_channels)
                        if self.settings.monitor_platform == "youtube"
                        else list(self.settings.target_channels)
                    )
                )
            ],
            "active_channels": [resolve_channel_name(c) for c in active_ch],
            "sessions": sessions_data,
            "disk": {
                "clips_bytes": clips_bytes,
                "buffer_bytes": buffer_bytes,
                "shorts_bytes": shorts_bytes,
                "total_used_bytes": total_disk_bytes,
                "disk_free_bytes": disk_free_bytes,
                "disk_total_bytes": disk_total_bytes,
                "max_buffer_mb": self.settings.max_total_buffer_mb,
            },
            "gemini": gemini_stats,
            "recording_enabled": self.settings.recording_enabled,
        }


# Global monitoring supervisor singleton
supervisor = MonitoringSupervisor()


def get_shorts_pipeline(settings: Settings | None = None) -> ShortsPipeline:
    """Return an instance of ShortsPipeline initialized with current settings."""
    cfg = settings or load_settings()
    return ShortsPipeline(settings=cfg)


def get_quota_tracker(settings: Settings | None = None) -> DailyQuotaTracker:
    """Return DailyQuotaTracker initialized with current settings."""
    cfg = settings or load_settings()
    return DailyQuotaTracker(
        quota_path=cfg.shorts_output_dir / ".youtube_quota.json",
        daily_limit=cfg.youtube_daily_limit,
    )
