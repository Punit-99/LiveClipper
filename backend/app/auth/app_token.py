"""App Access Token handling (for discovery / public Helix endpoints).

An App Access Token proves "this is the LiveClipper app" to Twitch. It is used
for PUBLIC data like searching categories and listing streams. It does NOT act
as any Twitch user and needs no browser login  our code mints it directly from
the Client ID + Client Secret using the OAuth "client credentials" grant.

This file is intentionally ONLY about the app token. The user token (for chat)
lives in a separate module, as you requested.

Design notes for learners:
- Tokens expire. Twitch tells us when (via `expires_in`). We cache the token in
  memory and reuse it until shortly before expiry, then mint a fresh one.
- We NEVER log the token value. If an API call later returns 401 (token invalid
  or revoked), the caller can force a refresh via `get_app_token(force=True)`.
"""

from __future__ import annotations

import time

import requests

from app.logging_setup import get_logger

log = get_logger(__name__)

_TOKEN_URL = "https://id.twitch.tv/oauth2/token"

# Simple in-memory cache: (token_string, unix_timestamp_when_it_expires).
_cached_token: str | None = None
_cached_expiry: float = 0.0


class AppTokenError(Exception):
    """Raised when we cannot obtain an App Access Token."""


def get_app_token(client_id: str, client_secret: str, *, force: bool = False) -> str:
    """Return a valid App Access Token, minting a new one only when needed.

    Args:
        client_id / client_secret: your Twitch app credentials.
        force: if True, ignore the cache and mint a fresh token (use this after
               a 401, which means the current token was revoked or invalidated).
    """
    global _cached_token, _cached_expiry

    # Reuse the cached token if it's still valid for at least 60 more seconds.
    if not force and _cached_token and time.time() < _cached_expiry - 60:
        return _cached_token

    log.info("Requesting a new App Access Token from Twitch...")
    try:
        response = requests.post(
            _TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        raise AppTokenError(f"Network error contacting Twitch: {exc}") from exc

    if response.status_code != 200:
        # Twitch returns a 'message' on failure. We surface it WITHOUT echoing
        # our credentials. A 400/403 here almost always means wrong Client ID
        # or Client Secret.
        detail = _safe_message(response)
        raise AppTokenError(
            f"Twitch rejected the token request (HTTP {response.status_code}): {detail}. "
            f"Double-check TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in your .env."
        )

    payload = response.json()
    token = payload.get("access_token")
    expires_in = payload.get("expires_in", 0)
    if not token:
        raise AppTokenError("Twitch response did not contain an access token.")

    _cached_token = token
    _cached_expiry = time.time() + float(expires_in)
    log.info("App Access Token obtained (valid for ~%s hours).", round(expires_in / 3600))
    return token


def _safe_message(response: requests.Response) -> str:
    """Extract Twitch's error message without leaking anything sensitive."""
    try:
        return str(response.json().get("message", response.text[:200]))
    except ValueError:
        return response.text[:200]
