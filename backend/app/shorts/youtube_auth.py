"""YouTube OAuth 2.0 token handling and lifecycle management.

Handles secure persistence, expiration detection, and automatic refresh
for Google YouTube Data API v3 access tokens.

Security rules:
- Token files are saved atomically with owner-only (0600) permissions.
- Raw token values are never logged in full; only truncated hints are logged.
- Distinguishes permanent authentication failures (invalid_grant) from transient
  network/5xx errors so valid refresh tokens are never discarded accidentally.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import requests

from app.logging_setup import get_logger

log = get_logger(__name__)

# Google OAuth2 endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Scopes needed for uploading and managing YouTube videos
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


class YouTubeAuthError(Exception):
    """Base error for YouTube OAuth operations."""


class TransientAuthError(YouTubeAuthError):
    """Temporary network or server failure (5xx, 429, timeout).

    Saved tokens MUST NOT be cleared on transient errors.
    """


class ReauthRequired(YouTubeAuthError):
    """Refresh token missing, revoked, or expired. User must authorize again."""


def redact(token: str | None) -> str:
    """Return a safe-to-log snippet of a token (e.g. 'ya29...4a9f')."""
    if not token:
        return "<none>"
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


class YouTubeTokenManager:
    """Thread-safe manager for YouTube OAuth2 tokens with atomic persistence."""

    def __init__(
        self,
        tokens_path: Path | str,
        client_id: str | None = None,
        client_secret: str | None = None,
        expiry_buffer_seconds: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.tokens_path = Path(tokens_path)
        self.client_id = client_id
        self.client_secret = client_secret
        self.expiry_buffer_seconds = max(0.0, float(expiry_buffer_seconds))
        self._session = session or requests.Session()
        self._lock = threading.Lock()

    def has_tokens(self) -> bool:
        """Check if a readable tokens file exists with a refresh token."""
        with self._lock:
            data = self._read_tokens_file_locked()
            return bool(data and data.get("refresh_token"))

    def load_tokens(self) -> dict[str, Any]:
        """Read and parse the current token file. Returns empty dict if absent/corrupt."""
        with self._lock:
            data = self._read_tokens_file_locked()
            return dict(data) if data else {}

    def save_tokens(
        self,
        access_token: str,
        refresh_token: str,
        expires_in: int | float,
        scope: str | None = None,
        token_type: str = "Bearer",
    ) -> None:
        """Atomically persist tokens with owner-only (0600) permissions."""
        with self._lock:
            expires_at = time.time() + float(expires_in)
            payload = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
                "scope": scope,
                "token_type": token_type,
                "updated_at": time.time(),
            }
            self._write_tokens_file_locked(payload)
            log.info(
                "Saved YouTube OAuth tokens (access=%s, expires_in=%.0fs)",
                redact(access_token),
                float(expires_in),
            )

    def clear_tokens(self) -> None:
        """Delete stored tokens file (e.g., when explicitly revoked)."""
        with self._lock:
            try:
                if self.tokens_path.exists():
                    self.tokens_path.unlink()
                    log.info("Cleared YouTube token file at %s", self.tokens_path)
            except OSError as exc:
                log.warning("Failed to remove token file %s: %s", self.tokens_path, exc)

    def get_valid_access_token(self) -> str:
        """Return a valid access token, auto-refreshing proactively if expired or expiring soon."""
        with self._lock:
            data = self._read_tokens_file_locked()
            if not data or not data.get("refresh_token"):
                raise ReauthRequired(
                    f"No YouTube tokens found at {self.tokens_path}. "
                    "Run 'python backend/authorize_youtube.py' first."
                )

            access_token = data.get("access_token")
            expires_at = float(data.get("expires_at", 0.0))
            now = time.time()

            # If token is still valid with safety buffer, use it directly
            if access_token and now < (expires_at - self.expiry_buffer_seconds):
                return str(access_token)

            # Otherwise refresh using the refresh token
            refresh_token = str(data["refresh_token"])
            log.info(
                "YouTube access token expired or expiring soon (now=%.0f, expires_at=%.0f). Refreshing...",
                now,
                expires_at,
            )
            return self._refresh_locked(refresh_token, existing_data=data)

    def refresh_access_token(self) -> str:
        """Force a token refresh immediately."""
        with self._lock:
            data = self._read_tokens_file_locked()
            if not data or not data.get("refresh_token"):
                raise ReauthRequired(
                    f"No refresh token available at {self.tokens_path}. Re-authorization required."
                )
            return self._refresh_locked(str(data["refresh_token"]), existing_data=data)

    def _refresh_locked(self, refresh_token: str, existing_data: dict[str, Any] | None = None) -> str:
        """Execute token refresh HTTP call and update file. Must be called under self._lock."""
        if not self.client_id or not self.client_secret:
            raise YouTubeAuthError(
                "YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET are required for token refresh."
            )

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            resp = self._session.post(GOOGLE_TOKEN_URL, data=payload, timeout=15.0)
        except requests.RequestException as exc:
            log.warning("Transient network error during YouTube token refresh: %s", exc)
            raise TransientAuthError(f"Network error refreshing YouTube token: {exc}") from exc

        # Handle HTTP response codes
        if resp.status_code in (500, 502, 503, 504, 429):
            log.warning("Transient HTTP %d from Google token endpoint", resp.status_code)
            raise TransientAuthError(f"Google token endpoint returned HTTP {resp.status_code}")

        if resp.status_code in (400, 401):
            try:
                err_body = resp.json()
                err_code = err_body.get("error")
                err_desc = err_body.get("error_description", "")
            except Exception:
                err_code = "unknown"
                err_desc = resp.text

            log.error("Google token refresh rejected: %s - %s", err_code, err_desc)
            if err_code in ("invalid_grant", "unauthorized_client", "invalid_client"):
                raise ReauthRequired(
                    f"YouTube authorization expired or revoked ({err_code}: {err_desc}). "
                    "Run 'python backend/authorize_youtube.py' to re-authenticate."
                )
            raise YouTubeAuthError(f"Token refresh failed: {err_code} - {err_desc}")

        if not resp.ok:
            raise YouTubeAuthError(f"Unexpected HTTP {resp.status_code} during token refresh: {resp.text}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise TransientAuthError("Invalid JSON received from Google token endpoint") from exc

        new_access_token = data.get("access_token")
        if not new_access_token:
            raise YouTubeAuthError("Token refresh response missing 'access_token'")

        expires_in = float(data.get("expires_in", 3600))
        # Keep old refresh token if Google didn't rotate it
        new_refresh_token = data.get("refresh_token") or refresh_token
        scope = data.get("scope") or (existing_data.get("scope") if existing_data else None)
        token_type = data.get("token_type", "Bearer")

        updated = {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "expires_at": time.time() + expires_in,
            "scope": scope,
            "token_type": token_type,
            "updated_at": time.time(),
        }
        self._write_tokens_file_locked(updated)
        log.info(
            "Successfully refreshed YouTube access token (token=%s, expires_in=%.0fs)",
            redact(new_access_token),
            expires_in,
        )
        return str(new_access_token)

    def _read_tokens_file_locked(self) -> dict[str, Any] | None:
        """Read and parse tokens file. Must be called under self._lock."""
        if not self.tokens_path.exists():
            return None
        try:
            with open(self.tokens_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Could not read YouTube tokens file at %s: %s", self.tokens_path, exc)
            return None

    def _write_tokens_file_locked(self, data: dict[str, Any]) -> None:
        """Atomic write with 0600 permissions. Must be called under self._lock."""
        self.tokens_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".youtube_tokens.",
            suffix=".tmp",
            dir=str(self.tokens_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            os.chmod(tmp_path, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.replace(tmp_path, self.tokens_path)
            except OSError as err:
                if err.errno in (16, 18):  # EBUSY or EXDEV (Docker bind mount)
                    with open(self.tokens_path, "w", encoding="utf-8") as fh:
                        json.dump(data, fh, indent=2)
                        fh.flush()
                        os.fsync(fh.fileno())
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                else:
                    raise
        except BaseException:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
