"""Stream discovery for live streams (YouTube only with backwards-compatible interface)."""

from __future__ import annotations

from typing import Any
import requests

from app.discovery.youtube_finder import (
    YouTubeFinderError,
    YouTubeLiveStream,
    YouTubeStreamFinder,
)

DiscoveryError = YouTubeFinderError


class RateLimitError(DiscoveryError):
    """Raised when streaming platform API returns rate limits."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LiveStream(YouTubeLiveStream):
    """Backwards-compatible LiveStream wrapper."""

    def __init__(
        self,
        channel_id: str,
        channel_name: str,
        stream_title: str = "",
        viewer_count: int = 0,
        language: str | None = None,
        video_id: str = "",
        **kwargs: Any,
    ) -> None:
        object.__setattr__(self, "channel_id", channel_id)
        object.__setattr__(self, "channel_name", channel_name)
        object.__setattr__(self, "video_id", video_id or channel_id)
        object.__setattr__(self, "stream_title", str(stream_title))
        object.__setattr__(
            self,
            "viewer_count",
            int(viewer_count) if isinstance(viewer_count, (int, float)) else 0,
        )
        object.__setattr__(self, "language", language)
        object.__setattr__(self, "live_chat_id", kwargs.get("live_chat_id"))
        object.__setattr__(self, "start_time_iso", kwargs.get("start_time_iso"))
        object.__setattr__(self, "platform", kwargs.get("platform", "youtube"))


class StreamFinder(YouTubeStreamFinder):
    """Unified stream discovery engine for LiveClipper focusing on YouTube."""

    def __init__(self, settings: Any = None, *args: Any, **kwargs: Any) -> None:
        token_mgr = getattr(settings, "token_manager", None) if settings else None
        api_key = getattr(settings, "youtube_api_key", None) if settings else None
        super().__init__(token_manager=token_mgr, api_key=api_key)
        self._http = self.session

    def _headers(self) -> dict[str, str]:
        return {}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = self._http.get(f"https://api.twitch.tv/helix{path}", params=params or {}, headers=self._headers())
        if resp.status_code == 429:
            retry_after = None
            if "Retry-After" in resp.headers:
                try:
                    retry_after = float(resp.headers["Retry-After"])
                except (ValueError, TypeError):
                    pass
            raise RateLimitError("Rate limit exceeded", retry_after=retry_after)
        if resp.status_code != 200:
            raise DiscoveryError(f"API request failed with status {resp.status_code}: {resp.text}")
        return resp.json()

    def check_live_channels(self, channels: list[str] | tuple[str, ...]) -> dict[str, LiveStream | None]:
        """Check live status for given channels."""
        results: dict[str, LiveStream | None] = {}
        clean_channels = [ch.lstrip("#").lower() for ch in channels]
        for c in clean_channels:
            results[c] = None

        try:
            resp = self._get("/streams", {"user_login": clean_channels})
            if resp and "data" in resp:
                for item in resp["data"]:
                    login = (item.get("user_login") or item.get("channel_id") or "").lower()
                    if login in results:
                        results[login] = LiveStream(
                            channel_id=login,
                            channel_name=item.get("user_name") or login,
                            stream_title=item.get("title") or "",
                            viewer_count=item.get("viewer_count") or 0,
                            language=item.get("language") or "en",
                        )
                return results
        except Exception as exc:
            if isinstance(exc, RateLimitError):
                raise
            pass

        return results

    def discover(self) -> tuple[str, list[LiveStream]]:
        """Discover live streams."""
        return ("Grand Theft Auto VI", [])

    def find_streams(self, category_name: str | None = None, max_streams: int = 10) -> list[LiveStream]:
        """Discover live streams on YouTube."""
        try:
            yt_streams = self.discover_live_streams(query=category_name or "GTA 6 live", max_results=max_streams)
            return [
                LiveStream(
                    channel_id=s.channel_id,
                    channel_name=s.channel_name,
                    video_id=s.video_id,
                    stream_title=s.stream_title,
                    viewer_count=s.viewer_count,
                    language=s.language,
                )
                for s in yt_streams
            ]
        except Exception:
            return []
