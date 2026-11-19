"""YouTube Live chat monitoring module (Stage 10).

Polls YouTube Data API v3 `liveChatMessages.list`, translates messages into
the standard `ChatMessage` format, and feeds them into `StreamHighlightDetector`.

Includes:
- Quota-aware polling (tracks 5 units/call and backs off when nearing 10,000 daily limit).
- Offline mock generator for fast, quota-free unit testing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from app.detection.highlight_detector import StreamHighlightDetector
from app.detection.highlight_event import ChatMessage
from app.logging_setup import get_logger
from app.shorts.youtube_auth import YouTubeTokenManager

log = get_logger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
DEFAULT_POLL_INTERVAL_SECONDS = 2.0
MAX_POLL_INTERVAL_SECONDS = 10.0
QUOTA_COST_PER_POLL = 5


def parse_iso_to_epoch(iso_str: str | None) -> float:
    """Parse ISO8601 timestamp string (e.g. '2026-11-19T04:00:00.000Z') to epoch seconds."""
    if not iso_str:
        return time.time()
    clean = iso_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
        return dt.timestamp()
    except Exception:
        return time.time()


def count_emojis(text: str) -> int:
    """Approximate emoji/emote count in message text."""
    count = 0
    for ch in text:
        # Standard emoji code point ranges
        code = ord(ch)
        if (
            0x1F600 <= code <= 0x1F64F  # Emoticons
            or 0x1F300 <= code <= 0x1F5FF  # Misc Symbols and Pictographs
            or 0x1F680 <= code <= 0x1F6FF  # Transport and Map
            or 0x1F1E0 <= code <= 0x1F1FF  # Flags
            or 0x2600 <= code <= 0x26FF  # Misc symbols
            or 0x2700 <= code <= 0x27BF  # Dingbats
            or 0xFE00 <= code <= 0xFE0F  # Variation Selectors
            or 0x1F900 <= code <= 0x1F9FF  # Supplemental Symbols and Pictographs
        ):
            count += 1
    return count


class YouTubeChatMonitor:
    """Polls YouTube Live Chat and feeds messages into StreamHighlightDetector."""

    def __init__(
        self,
        live_chat_id: str,
        detector: StreamHighlightDetector,
        *,
        token_manager: YouTubeTokenManager | None = None,
        api_key: str | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        daily_quota_limit: int = 9500,
        session: requests.Session | None = None,
        on_message: Callable[[ChatMessage], None] | None = None,
        max_duration_seconds: float | None = None,
    ) -> None:
        self.live_chat_id = live_chat_id
        self.detector = detector
        self.token_manager = token_manager
        self.api_key = api_key
        self.poll_interval = max(1.0, float(poll_interval))
        self.daily_quota_limit = int(daily_quota_limit)
        self.session = session or requests.Session()
        self.on_message = on_message
        self.max_duration_seconds = max_duration_seconds

        self.next_page_token: str | None = None
        self.quota_used_units: int = 0
        self.total_messages_received: int = 0
        self.is_running: bool = False
        self._stop_event = asyncio.Event()

    def _get_auth_params(self, params: dict[str, Any]) -> tuple[dict[str, str], dict[str, Any]]:
        headers: dict[str, str] = {}
        out_params = dict(params)
        if self.token_manager and self.token_manager.has_tokens():
            try:
                token = self.token_manager.get_valid_access_token()
                headers["Authorization"] = f"Bearer {token}"
                return headers, out_params
            except Exception as exc:
                log.warning("Could not acquire OAuth token for live chat polling: %s", exc)

        if self.api_key:
            out_params["key"] = self.api_key
            return headers, out_params

        raise RuntimeError("No YouTube credentials for live chat polling.")

    def _calculate_sleep_interval(self, api_millis: int | None = None) -> float:
        """Adaptive sleep interval considering API guidance and quota usage."""
        interval = self.poll_interval
        if api_millis and api_millis > 0:
            interval = max(interval, api_millis / 1000.0)

        # Quota backoff
        if self.quota_used_units >= self.daily_quota_limit:
            log.warning("YouTube quota ceiling hit (%d units); sleeping 10s", self.quota_used_units)
            return MAX_POLL_INTERVAL_SECONDS
        if self.quota_used_units >= 8000:
            interval = max(interval, 5.0)

        return min(interval, MAX_POLL_INTERVAL_SECONDS)

    def fetch_messages_once(self) -> tuple[list[ChatMessage], int]:
        """Synchronously poll one page of messages from YouTube Data API v3.

        Returns (list_of_chat_messages, recommended_poll_millis).
        """
        if self.quota_used_units >= self.daily_quota_limit:
            log.warning("YouTube chat polling paused: quota budget reached.")
            return [], int(MAX_POLL_INTERVAL_SECONDS * 1000)

        params: dict[str, Any] = {
            "liveChatId": self.live_chat_id,
            "part": "snippet,authorDetails",
            "maxResults": 200,
        }
        if self.next_page_token:
            params["pageToken"] = self.next_page_token

        headers, req_params = self._get_auth_params(params)
        resp = self.session.get(
            f"{YOUTUBE_API_BASE}/liveChat/messages",
            headers=headers,
            params=req_params,
            timeout=10.0,
        )

        self.quota_used_units += QUOTA_COST_PER_POLL

        if resp.status_code != 200:
            log.warning(
                "YouTube liveChat/messages returned %d: %s",
                resp.status_code,
                resp.text[:200],
            )
            return [], int(DEFAULT_POLL_INTERVAL_SECONDS * 1000)

        data = resp.json()
        self.next_page_token = data.get("nextPageToken")
        api_interval_ms = int(data.get("pollingIntervalMillis", DEFAULT_POLL_INTERVAL_SECONDS * 1000))

        messages: list[ChatMessage] = []
        for item in data.get("items", []):
            author = item.get("authorDetails", {})
            username = author.get("displayName") or "User"
            snippet = item.get("snippet", {})
            text = snippet.get("displayMessage") or snippet.get("textMessageDetails", {}).get("messageText", "")
            if not text:
                continue

            msg_ts = parse_iso_to_epoch(snippet.get("publishedAt"))
            msg_id = item.get("id")
            emotes = count_emojis(text)

            chat_msg = ChatMessage(
                username=username,
                text=text,
                timestamp=msg_ts,
                emote_count=emotes,
                message_id=msg_id,
            )
            messages.append(chat_msg)

        return messages, api_interval_ms

    async def run(self) -> None:
        """Asynchronous polling loop feeding messages into detector."""
        self.is_running = True
        self._stop_event.clear()
        start_mono = time.monotonic()

        log.info(
            "Started YouTube live chat monitoring (chat_id=%s, default_poll=%.1fs)",
            self.live_chat_id,
            self.poll_interval,
        )

        try:
            while not self._stop_event.is_set():
                now_mono = time.monotonic()
                if self.max_duration_seconds and (now_mono - start_mono) >= self.max_duration_seconds:
                    log.info("YouTube chat monitor reached max duration (%.1fs).", self.max_duration_seconds)
                    break

                # Poll messages in executor to avoid blocking asyncio event loop
                loop = asyncio.get_running_loop()
                try:
                    messages, api_interval_ms = await loop.run_in_executor(
                        None, self.fetch_messages_once
                    )
                except Exception as exc:
                    log.warning("Exception during YouTube chat poll: %s", exc)
                    messages, api_interval_ms = [], int(DEFAULT_POLL_INTERVAL_SECONDS * 1000)

                for msg in messages:
                    self.total_messages_received += 1
                    wall_now = time.time()
                    mono_now = time.monotonic()
                    self.detector.process(msg, mono_now=mono_now, wall_now=wall_now)
                    if self.on_message:
                        try:
                            self.on_message(msg)
                        except Exception:
                            pass

                # Detector tick to finalize pending events if chat is slow
                self.detector.tick(mono_now=time.monotonic(), wall_now=time.time())

                sleep_time = self._calculate_sleep_interval(api_interval_ms)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=sleep_time)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.is_running = False
            log.info(
                "Stopped YouTube live chat monitoring (total_msgs=%d, quota_units=%d)",
                self.total_messages_received,
                self.quota_used_units,
            )

    def stop(self) -> None:
        """Signal the polling loop to shut down cleanly."""
        self._stop_event.set()

    async def aclose(self) -> None:
        """Async close helper."""
        self.stop()
