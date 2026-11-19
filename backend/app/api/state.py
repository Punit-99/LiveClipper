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

from app.config import Settings, load_settings
from app.filters.language_filter import LanguageFilter
from app.multistream.manager import MultiStreamManager
from app.multistream.session import SessionState, StreamSession
from app.recording.clip_extractor import ClipExtractionResult
from app.shorts.pipeline import ShortsPipeline
from app.shorts.youtube_auth import YouTubeTokenManager
from app.shorts.youtube_uploader import DailyQuotaTracker, YouTubeUploader
from app.discovery.youtube_finder import YouTubeStreamFinder, YouTubeLiveStream, resolve_channel_name
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
        self._start_time: float | None = None
        self._lock = asyncio.Lock()
        self.active_platform: str = "none"
        self.active_channels: list[str] = []

    @property
    def is_running(self) -> bool:
        return self.manager is not None and self.manager.is_running

    def _on_extracted(self, result: ClipExtractionResult) -> None:
        """Callback when FFmpeg finishes extracting a new clip."""
        if result.status == "extracted" and result.clip_path:
            clip_id = result.event_id[:8]
            sidecar_data: dict[str, Any] = {}
            if result.metadata_path and Path(result.metadata_path).is_file():
                try:
                    with open(result.metadata_path, "r", encoding="utf-8") as fh:
                        sidecar_data = json.load(fh)
                except Exception:
                    pass

            analysis = sidecar_data.get("analysis") or {}
            event_payload = {
                "event_id": result.event_id,
                "clip_id": clip_id,
                "clip_path": str(result.clip_path),
                "filename": Path(result.clip_path).name,
                "channel": resolve_channel_name(sidecar_data.get("streamer_name") or sidecar_data.get("stream_id") or "unknown"),
                "platform": sidecar_data.get("platform") or "twitch",
                "title": analysis.get("title") or "Stream Highlight",
                "highlight_score": int(analysis.get("highlight_score", 0)),
                "category": analysis.get("category") or "highlight",
                "duration": float(result.metadata.total_media_duration if result.metadata else 0.0),
                "extracted_at": time.time(),
            }
            log.info("Broadcasting clip extracted event for %s", clip_id)
            ws_manager.broadcast_sync("clip_extracted", event_payload)

    def _on_state_change(self, session: StreamSession, old_state: SessionState, new_state: SessionState) -> None:
        """Callback when a stream session changes state."""
        ws_manager.broadcast_sync(
            "session_state",
            {
                "channel": resolve_channel_name(session.channel),
                "platform": session.platform,
                "old_state": old_state.value,
                "new_state": new_state.value,
            },
        )

    async def start(
        self,
        platform: str | None = None,
        channels: list[str] | None = None,
        languages: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start monitoring in a background task."""
        async with self._lock:
            if self.is_running:
                return {"status": "already_running", "channels": self.active_channels}

            # Reload settings from disk
            self.settings = load_settings()

            target_platform = (platform or self.settings.monitor_platform or "twitch").lower()
            effective_channels = channels or []

            # Prepare channel targets
            if not effective_channels:
                if target_platform in ("twitch", "all") and self.settings.target_channels:
                    effective_channels = list(self.settings.target_channels)

            custom_sessions: list[StreamSession] = []

            # YouTube discovery / priority channels if requested
            if target_platform in ("youtube", "all"):
                yt_candidates = channels if (channels and target_platform == "youtube") else list(self.settings.target_youtube_channels)
                token_mgr = None
                if self.settings.youtube_tokens_path and self.settings.youtube_tokens_path.is_file():
                    token_mgr = YouTubeTokenManager(
                        tokens_path=self.settings.youtube_tokens_path,
                        client_id=self.settings.youtube_client_id,
                        client_secret=self.settings.youtube_client_secret,
                    )
                yt_finder = YouTubeStreamFinder(
                    token_manager=token_mgr,
                    api_key=self.settings.gemini_api_key,
                    priority_channels=yt_candidates,
                )
                try:
                    yt_streams = yt_finder.check_priority_channels(yt_candidates)
                    for ys in yt_streams:
                        ch_name = resolve_channel_name(ys.channel_name or ys.channel_id)
                        sess = StreamSession(
                            channel=ch_name,
                            settings=self.settings,
                            platform="youtube",
                            video_id=ys.video_id,
                            live_chat_id=ys.live_chat_id,
                        )
                        custom_sessions.append(sess)
                except Exception as exc:
                    log.warning("YouTube stream check failed during API start: %s", exc)

                if not custom_sessions and yt_candidates:
                    log.info("No active streams returned via search; attaching configured YouTube channels directly: %s", yt_candidates)
                    for cid in yt_candidates:
                        sess = StreamSession(
                            channel=cid,
                            settings=self.settings,
                            platform="youtube",
                        )
                        custom_sessions.append(sess)

            twitch_channels = [resolve_channel_name(c) for c in effective_channels] if target_platform in ("twitch", "all") else []

            total_targets = len(twitch_channels) + len(custom_sessions)
            if total_targets == 0:
                raise ValueError("No live targets specified or found for monitoring.")

            self.active_platform = target_platform
            self.active_channels = twitch_channels + [s.channel for s in custom_sessions]
            self._start_time = time.time()

            self.manager = MultiStreamManager(
                settings=self.settings,
                channels=twitch_channels,
                max_concurrent_streams=self.settings.max_streams,
                print_messages=False,
                auto_restart_online=True,
                enable_liveness_check=(target_platform in ("twitch", "all")),
                enable_disk_budget=True,
                custom_sessions=custom_sessions,
                on_extracted=self._on_extracted,
                on_state_change=self._on_state_change,
            )

            # Start manager asynchronously
            await self.manager.start()

            # Schedule background execution to keep manager active until stopped
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
            return {"status": "started", "platform": target_platform, "channels": self.active_channels}

    async def stop(self) -> dict[str, Any]:
        """Gracefully stop monitoring."""
        async with self._lock:
            if not self.is_running or self.manager is None:
                return {"status": "not_running"}

            await self.manager.stop()
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass

            self.manager = None
            self._task = None
            self._start_time = None
            self.active_channels = []

            status_data = self.get_status()
            ws_manager.broadcast_sync("monitoring_stopped", status_data)
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

        # Active channels & sessions
        active_ch = mgr_status.active_channels if mgr_status else []
        sessions_data = {}
        if mgr_status:
            for ch, s_snap in mgr_status.sessions.items():
                sessions_data[ch] = {
                    "state": s_snap.state.value,
                    "segments_registered": s_snap.segments_registered,
                    "buffer_bytes": s_snap.buffer_bytes,
                    "clips_extracted": s_snap.clips_extracted,
                    "clips_failed": s_snap.clips_failed,
                    "error_message": s_snap.error_message,
                }

        # Total counts
        return {
            "is_monitoring": is_mon,
            "uptime_seconds": (time.time() - self._start_time) if self._start_time else 0.0,
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
            "sessions": {resolve_channel_name(ch): s_data for ch, s_data in sessions_data.items()},
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
