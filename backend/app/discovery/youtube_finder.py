"""YouTube Live stream discovery module (Stage 10).

Supports:
1. Mode A: Priority Channels check (e.g. CarryMinati, Mortal, Techno Gamerz, Dynamo, Total Gaming, Scout, Jonathan, Mythpat).
2. Mode B: Category & Gaming discovery (e.g. Grand Theft Auto VI live streams).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests

from app.logging_setup import get_logger
from app.shorts.youtube_auth import YouTubeTokenManager

log = get_logger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Default Indian gaming priority channels
DEFAULT_PRIORITY_CHANNELS = (
    "UC7cs8q-gJRlGwj4A8OmCmXg",  # CarryMinati
    "UCvQ0C-sIKKlFaxyHYRE1YSg",  # Mortal
    "UCsiddxZ6V4Flv-s3nps49AQ",  # Techno Gamerz
    "UCG4eH77kG_X9F0w_dE7g1rg",  # Dynamo Gaming
    "UCjqF3HPUPi_1c64LpD2w0_g",  # Total Gaming
    "UC0pA8_YvGqQx-r4C7d2P6FA",  # Scout
    "UCXfJ48U5G1T5k-p8G9f5J9A",  # Jonathan Gaming
    "UClvgJrkf4k4Kq2R4H8_wB9g",  # Mythpat
)

KNOWN_CHANNEL_NAMES: dict[str, str] = {
    "UCWsDFcIhY2DBi3GB5uykGXA": "IShowSpeed",
    "UC0IWRLai-BAwci_e9MylNGw": "CarryisLive",
    "UCx6F-rETGiz7xf_vkMmX2yQ": "Mythpat",
    "UCWxcAUWjF3B_S6fFLqOh7Rw": "GamerFleet",
    "UCFwKgzKe-EdTz83r6wzhmOw": "Live Insaan",
    "UCQfBN7Ut5TiZJAhMo_Rgfyw": "Snax Gaming",
    "UC7cs8q-gJRlGwj4A8OmCmXg": "CarryMinati",
    "UCvQ0C-sIKKlFaxyHYRE1YSg": "Mortal",
    "UCsiddxZ6V4Flv-s3nps49AQ": "Techno Gamerz",
    "UCG4eH77kG_X9F0w_dE7g1rg": "Dynamo Gaming",
    "UCjqF3HPUPi_1c64LpD2w0_g": "Total Gaming",
    "UC0pA8_YvGqQx-r4C7d2P6FA": "Scout",
    "UCXfJ48U5G1T5k-p8G9f5J9A": "Jonathan Gaming",
    "UClvgJrkf4k4Kq2R4H8_wB9g": "Mythpat",
}

_DYNAMIC_CHANNEL_CACHE: dict[str, str] = {}


def register_channel_name(channel_id: str, channel_name: str) -> None:
    """Register or update a channel_id -> channel_name mapping dynamically."""
    if channel_id and channel_name:
        clean_id = str(channel_id).strip()
        clean_name = str(channel_name).strip()
        if clean_id and clean_name and not clean_name.startswith("UC"):
            _DYNAMIC_CHANNEL_CACHE[clean_id] = clean_name
            _DYNAMIC_CHANNEL_CACHE[clean_id.lower()] = clean_name


def resolve_channel_name(channel_or_id: str | None, default: str = "unknown") -> str:
    """Resolve a YouTube channel ID (e.g. UCWsDFcIhY2DBi3GB5uykGXA) to its human-readable channel name.

    If the channel is already a human-friendly name (e.g. 'kaicenat', 'xqc', 'IShowSpeed'),
    or if it cannot be resolved, returns the original identifier (or default).
    """
    if not channel_or_id:
        return default

    clean = str(channel_or_id).strip()
    if not clean:
        return default

    # Direct match or case-insensitive match in dynamic cache
    if clean in _DYNAMIC_CHANNEL_CACHE:
        return _DYNAMIC_CHANNEL_CACHE[clean]
    lower = clean.lower()
    if lower in _DYNAMIC_CHANNEL_CACHE:
        return _DYNAMIC_CHANNEL_CACHE[lower]

    # Direct match or case-insensitive match in known channels
    for cid, cname in KNOWN_CHANNEL_NAMES.items():
        if clean == cid or lower == cid.lower():
            return cname

    return clean


def resolve_channel_api(
    channel_id: str,
    token_manager: YouTubeTokenManager | None = None,
    api_key: str | None = None,
) -> tuple[str, bool]:
    """Resolve a YouTube channel ID to its display name.

    Returns (display_name, is_resolved).
    Checks known static dict first, then in-memory dynamic cache,
    and finally queries YouTube Data API v3 channels endpoint if credentials exist.
    """
    clean_id = (channel_id or "").strip()
    if not clean_id:
        return ("", False)

    # 1. Check known channels
    for cid, name in KNOWN_CHANNEL_NAMES.items():
        if clean_id.lower() == cid.lower():
            return (name, True)

    # 2. Check dynamic cache
    if clean_id in _DYNAMIC_CHANNEL_CACHE:
        return (_DYNAMIC_CHANNEL_CACHE[clean_id], True)
    if clean_id.lower() in _DYNAMIC_CHANNEL_CACHE:
        return (_DYNAMIC_CHANNEL_CACHE[clean_id.lower()], True)

    # 3. Query YouTube Data API if possible
    try:
        tm = token_manager
        if tm is None:
            try:
                tm = YouTubeTokenManager()
            except Exception:
                tm = None

        finder = YouTubeStreamFinder(token_manager=tm, api_key=api_key)
        params = {"part": "snippet", "id": clean_id}
        headers, query_params = finder._get_headers_or_params(params)
        url = f"{YOUTUBE_API_BASE}/channels"
        resp = finder.session.get(url, headers=headers, params=query_params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                title = items[0].get("snippet", {}).get("title")
                if title:
                    register_channel_name(clean_id, title)
                    return (title, True)
    except Exception as exc:
        log.debug("Failed to resolve channel ID %s via API: %s", clean_id, exc)

    return (clean_id, False)


@dataclass(frozen=True)
class YouTubeLiveStream:
    """Discovered YouTube live stream metadata."""

    channel_id: str
    channel_name: str
    video_id: str
    stream_title: str
    viewer_count: int
    live_chat_id: str | None = None
    start_time_iso: str | None = None
    language: str | None = None
    platform: str = "youtube"

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "video_id": self.video_id,
            "stream_title": self.stream_title,
            "viewer_count": self.viewer_count,
            "live_chat_id": self.live_chat_id,
            "start_time_iso": self.start_time_iso,
            "language": self.language,
            "platform": self.platform,
        }


class YouTubeFinderError(Exception):
    """Base error for YouTube discovery operations."""


class YouTubeStreamFinder:
    """Discovers active YouTube Live broadcasts using YouTube Data API v3."""

    def __init__(
        self,
        token_manager: YouTubeTokenManager | None = None,
        api_key: str | None = None,
        session: requests.Session | None = None,
        priority_channels: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self.token_manager = token_manager
        self.api_key = api_key
        self.session = session or requests.Session()
        self.priority_channels = tuple(priority_channels or DEFAULT_PRIORITY_CHANNELS)

    def _get_headers_or_params(self, params: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        """Resolve Authorization header or api_key query param."""
        headers: dict[str, str] = {}
        out_params = dict(params)

        if self.token_manager and self.token_manager.has_tokens():
            try:
                token = self.token_manager.get_valid_access_token()
                headers["Authorization"] = f"Bearer {token}"
                return headers, out_params
            except Exception as exc:
                log.warning("Could not acquire OAuth token for YouTube discovery: %s", exc)

        if self.api_key:
            out_params["key"] = self.api_key
            return headers, out_params

        raise YouTubeFinderError("No credentials available: need YouTube OAuth tokens or API key.")

    def check_priority_channels(
        self,
        channels: list[str] | tuple[str, ...] | None = None,
    ) -> list[YouTubeLiveStream]:
        """Mode A: Check if specific priority channels are currently live.

        Queries search endpoint for each channel with eventType=live, then enriches with video details.
        """
        targets = channels or self.priority_channels
        live_streams: list[YouTubeLiveStream] = []
        candidate_video_ids: list[str] = []

        for cid in targets:
            cid_clean = cid.strip()
            if not cid_clean:
                continue
            try:
                params = {
                    "part": "snippet",
                    "channelId": cid_clean,
                    "eventType": "live",
                    "type": "video",
                    "maxResults": 1,
                }
                headers, req_params = self._get_headers_or_params(params)
                resp = self.session.get(
                    f"{YOUTUBE_API_BASE}/search",
                    headers=headers,
                    params=req_params,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get("items", []):
                        vid = item.get("id", {}).get("videoId")
                        if vid:
                            candidate_video_ids.append(vid)
                else:
                    log.warning(
                        "YouTube search returned %d for channel %s: %s",
                        resp.status_code,
                        cid_clean,
                        resp.text[:200],
                    )
            except Exception as exc:
                log.warning("Error checking channel %s: %s", cid_clean, exc)

        if candidate_video_ids:
            live_streams.extend(self.get_video_details(candidate_video_ids))

        return live_streams

    def discover_category_streams(
        self,
        query: str = "GTA 6 live",
        category_id: str = "20",
        min_viewers: int = 100,
        max_results: int = 10,
    ) -> list[YouTubeLiveStream]:
        """Mode B: Category and keyword discovery for active gaming live streams.

        Queries YouTube Data API v3 search endpoint with eventType=live, type=video, order=viewCount.
        Filters by min_viewers and sorts descending.
        """
        params: dict[str, Any] = {
            "part": "snippet",
            "eventType": "live",
            "type": "video",
            "order": "viewCount",
            "maxResults": min(50, max(1, max_results)),
        }
        if query:
            params["q"] = query
        if category_id:
            params["videoCategoryId"] = category_id

        headers, req_params = self._get_headers_or_params(params)
        resp = self.session.get(
            f"{YOUTUBE_API_BASE}/search",
            headers=headers,
            params=req_params,
            timeout=10.0,
        )
        if resp.status_code != 200:
            raise YouTubeFinderError(
                f"YouTube category discovery failed ({resp.status_code}): {resp.text[:300]}"
            )

        data = resp.json()
        video_ids = [
            item["id"]["videoId"]
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]

        if not video_ids:
            return []

        detailed = self.get_video_details(video_ids)
        # Filter by min_viewers
        filtered = [s for s in detailed if s.viewer_count >= min_viewers]
        filtered.sort(key=lambda s: s.viewer_count, reverse=True)
        return filtered

    def get_video_details(self, video_ids: list[str]) -> list[YouTubeLiveStream]:
        """Fetch liveStreamingDetails, snippet, and statistics for video IDs."""
        if not video_ids:
            return []

        # YouTube videos.list accepts up to 50 IDs comma-separated
        streams: list[YouTubeLiveStream] = []
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            params = {
                "part": "snippet,liveStreamingDetails,statistics",
                "id": ",".join(chunk),
            }
            headers, req_params = self._get_headers_or_params(params)
            resp = self.session.get(
                f"{YOUTUBE_API_BASE}/videos",
                headers=headers,
                params=req_params,
                timeout=10.0,
            )
            if resp.status_code != 200:
                log.warning("YouTube videos.list failed (%d): %s", resp.status_code, resp.text[:200])
                continue

            data = resp.json()
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                live_details = item.get("liveStreamingDetails", {})
                stats = item.get("statistics", {})

                # Viewer count: concurrentViewers from liveStreamingDetails, or fallback to viewCount
                viewers_str = live_details.get("concurrentViewers") or stats.get("viewCount", "0")
                try:
                    viewers = int(viewers_str)
                except (ValueError, TypeError):
                    viewers = 0

                language = (
                    snippet.get("defaultAudioLanguage")
                    or snippet.get("defaultLanguage")
                )

                c_id = snippet.get("channelId", "")
                c_name = snippet.get("channelTitle", "Unknown Channel")
                register_channel_name(c_id, c_name)

                stream = YouTubeLiveStream(
                    channel_id=c_id,
                    channel_name=c_name,
                    video_id=item.get("id", ""),
                    stream_title=snippet.get("title", ""),
                    viewer_count=viewers,
                    live_chat_id=live_details.get("activeLiveChatId"),
                    start_time_iso=live_details.get("actualStartTime"),
                    language=language,
                    platform="youtube",
                )
                streams.append(stream)

        return streams
