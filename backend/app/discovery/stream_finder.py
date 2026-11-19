"""Discover currently-live streams for a given game category (e.g. GTA 6).

Two Twitch Helix API calls, in order:
  1. Search Categories  -> turn a NAME ("Grand Theft Auto VI") into a category ID.
  2. Get Streams        -> list live streams playing that category ID.

We never hardcode the category ID; we resolve it live every run. Both calls use
the App Access Token (public data), so no user login is involved here.

Each Helix request needs two headers:
  - Authorization: Bearer <app access token>
  - Client-Id: <your client id>
"""

from __future__ import annotations

from dataclasses import dataclass
import time

import requests

from app.auth.app_token import get_app_token
from app.config import Settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_HELIX = "https://api.twitch.tv/helix"


class DiscoveryError(Exception):
    """Raised when discovery fails in a way the user should see."""


class RateLimitError(DiscoveryError):
    """Raised when Twitch Helix returns HTTP 429."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class LiveStream:
    """A single live stream, trimmed to the fields we care about."""

    channel: str          # user_login, used to join chat later
    display_name: str     # user_name, nicer for display
    title: str
    viewer_count: int
    language: str

    @property
    def url(self) -> str:
        return f"https://twitch.tv/{self.channel}"


class StreamFinder:
    """Finds the category ID and lists live streams for it."""

    def __init__(self, settings: Settings):
        self._settings = settings
        # A reusable HTTP session keeps connections warm and tidy.
        self._http = requests.Session()

    # -- internal: build auth headers, refreshing the token on a 401 ------------

    def _headers(self, *, force_new_token: bool = False) -> dict[str, str]:
        token = get_app_token(
            self._settings.client_id,
            self._settings.client_secret,
            force=force_new_token,
        )
        return {
            "Authorization": f"Bearer {token}",
            "Client-Id": self._settings.client_id,
        }

    def _get(self, path: str, params: dict) -> dict:
        """GET a Helix endpoint, retrying ONCE with a fresh token on HTTP 401."""
        url = f"{_HELIX}{path}"
        for attempt in (1, 2):
            force = attempt == 2  # second try: force a brand-new token
            try:
                resp = self._http.get(
                    url, headers=self._headers(force_new_token=force),
                    params=params, timeout=10,
                )
            except requests.RequestException as exc:
                raise DiscoveryError(f"Network error calling Twitch: {exc}") from exc

            if resp.status_code == 401 and attempt == 1:
                log.warning("Twitch returned 401; refreshing app token and retrying once.")
                continue
            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp)
                raise RateLimitError(
                    f"Twitch API rate limited (HTTP 429) on {path}: "
                    f"{self._safe_message(resp)}",
                    retry_after=retry_after,
                )
            if resp.status_code != 200:
                raise DiscoveryError(
                    f"Twitch API error (HTTP {resp.status_code}) on {path}: "
                    f"{self._safe_message(resp)}"
                )
            return resp.json()

        # Unreachable, but keeps type-checkers and logic honest.
        raise DiscoveryError("Exhausted retries calling Twitch.")

    # -- public steps -----------------------------------------------------------

    def resolve_category_id(self, name: str) -> tuple[str, str]:
        """Return (category_id, canonical_name) for a category NAME.

        Search Categories does a fuzzy search, so we prefer an exact
        (case-insensitive) name match and fall back to the first result.
        """
        log.info("Searching Twitch categories for %r ...", name)
        data = self._get("/search/categories", {"query": name, "first": 20})
        results = data.get("data", [])
        if not results:
            raise DiscoveryError(
                f"No Twitch category matched {name!r}. "
                f"It may not exist yet or be named differently  "
                f"try changing GTA6_CATEGORY_NAME in your .env."
            )

        for item in results:
            if item.get("name", "").strip().lower() == name.strip().lower():
                log.info("Matched category %r (id=%s).", item["name"], item["id"])
                return item["id"], item["name"]

        best = results[0]
        log.info(
            "No exact match; using closest result %r (id=%s). "
            "Other candidates: %s",
            best["name"], best["id"],
            ", ".join(r["name"] for r in results[1:5]) or "(none)",
        )
        return best["id"], best["name"]

    def list_live_streams(self, category_id: str) -> list[LiveStream]:
        """Return live streams for a category ID (most-viewed first)."""
        params = {"game_id": category_id, "first": self._settings.max_streams}
        if self._settings.stream_language:
            params["language"] = self._settings.stream_language

        log.info("Fetching up to %d live streams...", self._settings.max_streams)
        data = self._get("/streams", params)

        streams = [
            LiveStream(
                channel=s.get("user_login", ""),
                display_name=s.get("user_name", s.get("user_login", "")),
                title=(s.get("title") or "").strip(),
                viewer_count=int(s.get("viewer_count", 0)),
                language=s.get("language", "?"),
            )
            for s in data.get("data", [])
            if s.get("user_login")
        ]
        return streams

    def discover(self) -> tuple[str, list[LiveStream]]:
        """Full pipeline: name -> category id -> live streams.

        Returns (canonical_category_name, streams).
        """
        category_id, canonical = self.resolve_category_id(self._settings.category_name)
        streams = self.list_live_streams(category_id)
        return canonical, streams

    def check_live_channels(self, channels: list[str]) -> dict[str, LiveStream | None]:
        """Batch-check live status for up to 100 channels per Helix API call.

        Returns a dict mapping each normalized channel login to its `LiveStream`
        if currently live, or `None` if offline.
        """
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in channels:
            ch = raw.lstrip("#").strip().lower()
            if ch and ch not in seen:
                seen.add(ch)
                normalized.append(ch)

        if not normalized:
            return {}

        results: dict[str, LiveStream | None] = {ch: None for ch in normalized}

        # Twitch Helix /streams supports up to 100 user_login query params per request.
        for i in range(0, len(normalized), 100):
            chunk = normalized[i : i + 100]
            data = self._get("/streams", {"user_login": chunk})
            for s in data.get("data", []):
                ch = (s.get("user_login") or "").strip().lower()
                if ch in results:
                    results[ch] = LiveStream(
                        channel=ch,
                        display_name=s.get("user_name", ch),
                        title=(s.get("title") or "").strip(),
                        viewer_count=int(s.get("viewer_count", 0)),
                        language=s.get("language", "?"),
                    )

        return results

    @staticmethod
    def _parse_retry_after(resp: requests.Response) -> float | None:
        raw_retry = resp.headers.get("Retry-After")
        if raw_retry:
            try:
                return max(1.0, float(raw_retry))
            except ValueError:
                pass
        raw_reset = resp.headers.get("Ratelimit-Reset")
        if raw_reset:
            try:
                return max(1.0, float(raw_reset) - time.time())
            except ValueError:
                pass
        return None

    @staticmethod
    def _safe_message(resp: requests.Response) -> str:
        try:
            return str(resp.json().get("message", resp.text[:200]))
        except ValueError:
            return resp.text[:200]
