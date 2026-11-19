"""User Access Token handling (for reading chat as your Twitch account).

This is deliberately SEPARATE from app_token.py:
- app_token.py  -> "this is the app"      (public data: discovery)
- user_token.py -> "this acts as a user"  (chat read, clip creation)

What this module does:
1. run_login_flow()        -> one-time browser consent -> saves access+refresh token.
2. get_valid_access_token()-> returns a usable token later, auto-refreshing it,
                              rotating (re-saving) the refresh token, and raising
                              ReauthRequired if we must ask the user to log in again.

Security rules enforced here:
- Tokens are written to .tokens.json with owner-only (0600) permissions.
- Token VALUES are never logged  only lengths/hints via redact() if ever needed.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from weakref import WeakKeyDictionary

import requests

from app.config import Settings
from app.logging_setup import get_logger

log = get_logger(__name__)

# Single shared lock serializing token reads & refreshes across sync and async callers.
_TOKEN_LOCK = threading.Lock()
# Guard for `_ASYNC_LOCKS` so threads running separate event loops never race on lookup/insert.
_ASYNC_LOCKS_GUARD = threading.Lock()
# Per-event-loop asyncio.Lock so concurrent coroutines coalesce without occupying threads.
_ASYNC_LOCKS: WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = WeakKeyDictionary()


def _get_async_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    with _ASYNC_LOCKS_GUARD:
        lock = _ASYNC_LOCKS.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            _ASYNC_LOCKS[loop] = lock
        return lock

# Twitch OAuth endpoints.
_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
_VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"

# The permissions we ask the user to grant. Requested together so the user
# authorizes once for both chat reading and (future) clip creation.
SCOPES = ["chat:read", "clips:edit"]

# Where we persist tokens (git-ignored). Sits in backend/.
_TOKENS_PATH = Path(__file__).resolve().parent.parent.parent / ".tokens.json"


class UserTokenError(Exception):
    """A problem obtaining or refreshing the user token."""


class TransientTokenError(UserTokenError):
    """Temporary network/5xx/429 failure contacting Twitch (do NOT clear saved tokens)."""


class ReauthRequired(UserTokenError):
    """We have no usable token / refresh failed -> the user must log in again."""


# --------------------------------------------------------------------------- #
# Token storage (owner-only file)
# --------------------------------------------------------------------------- #

def _save_tokens(data: dict) -> None:
    """Atomically write tokens to disk with secure (0600) permissions."""
    _TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".tokens.", suffix=".tmp", dir=_TOKENS_PATH.parent
    )
    tmp_path = Path(tmp_name)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, _TOKENS_PATH)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    log.info("Saved user token to %s (owner-only).", _TOKENS_PATH.name)


def _load_tokens() -> dict | None:
    if not _TOKENS_PATH.exists():
        return None
    try:
        with open(_TOKENS_PATH) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def clear_tokens() -> None:
    """Delete stored tokens (used when a token is revoked/invalid)."""
    try:
        _TOKENS_PATH.unlink(missing_ok=True)
        log.info("Cleared stored user token.")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Step 1: one-time browser login (Authorization Code flow)
# --------------------------------------------------------------------------- #

def _build_authorize_url(settings: Settings, state: str) -> str:
    """Construct the Twitch consent URL the user's browser will open."""
    params = {
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches the single redirect Twitch sends back to localhost."""

    # Filled in by the server loop; set on the server instance.
    def do_GET(self) -> None:  # noqa: N802 (name mandated by http.server)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/", ""):  # ignore /favicon.ico etc.
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_result = {  # type: ignore[attr-defined]
            "code": query.get("code", [None])[0],
            "state": query.get("state", [None])[0],
            "error": query.get("error", [None])[0],
            "error_description": query.get("error_description", [None])[0],
        }

        ok = self.server.oauth_result.get("code") is not None  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = (
            "<h2>LiveClipper: authorization complete ✓</h2>"
            "<p>You can close this tab and return to the terminal.</p>"
            if ok else
            "<h2>LiveClipper: authorization failed</h2>"
            "<p>Return to the terminal for details.</p>"
        )
        self.wfile.write(f"<html><body style='font-family:sans-serif'>{msg}</body></html>".encode())

    def log_message(self, *args) -> None:  # silence default HTTP logging
        pass


def run_login_flow(settings: Settings, *, timeout_seconds: int = 300) -> dict:
    """Perform the one-time browser login. Returns validated token info.

    Raises UserTokenError on failure (e.g. user denies, mismatched state).
    """
    host, port = _parse_host_port(settings.redirect_uri)
    state = secrets.token_urlsafe(24)
    authorize_url = _build_authorize_url(settings, state)

    server = HTTPServer((host, port), _CallbackHandler)
    server.oauth_result = None  # type: ignore[attr-defined]
    server.timeout = 1

    log.info("Opening your browser to authorize LiveClipper (scopes: %s)...", ", ".join(SCOPES))
    print("\nIf your browser didn't open, paste this URL into it:\n" + authorize_url + "\n")
    webbrowser.open(authorize_url)

    deadline = time.time() + timeout_seconds
    try:
        while server.oauth_result is None:  # type: ignore[attr-defined]
            if time.time() > deadline:
                raise UserTokenError("Timed out waiting for authorization in the browser.")
            server.handle_request()
    finally:
        server.server_close()

    result = server.oauth_result  # type: ignore[attr-defined]
    if result.get("error"):
        raise UserTokenError(
            f"Authorization was denied or failed: {result['error']} "
            f"({result.get('error_description') or 'no detail'})."
        )
    if result.get("state") != state:
        raise UserTokenError("State mismatch  possible tampering. Aborting for safety.")
    code = result.get("code")
    if not code:
        raise UserTokenError("No authorization code received from Twitch.")

    tokens = _exchange_code_for_tokens(settings, code)
    with _TOKEN_LOCK:
        info = _validate_and_store(settings, tokens)
    return info


def _exchange_code_for_tokens(settings: Settings, code: str) -> dict:
    """Swap the one-time code for access + refresh tokens."""
    log.info("Exchanging authorization code for tokens...")
    resp = _post_token(
        {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": settings.redirect_uri,
        }
    )
    return resp


# --------------------------------------------------------------------------- #
# Step 2: get a usable token later (auto-refresh + rotation)
# --------------------------------------------------------------------------- #

def get_valid_access_token(settings: Settings) -> dict:
    """Return {'access_token', 'login', 'scopes'} for use by the chat module.

    Thread-safe: acquires `_TOKEN_LOCK` around loading `.tokens.json` and
    refreshing so sync and async callers share one synchronization primitive
    and never collide on refresh-token rotation. Raises `TransientTokenError`
    on temporary network/5xx errors without deleting saved tokens.
    """
    with _TOKEN_LOCK:
        stored = _load_tokens()
        if not stored or not stored.get("refresh_token"):
            raise ReauthRequired("No stored login found. Please run the login first.")

        # Refresh if the access token expires within the next 2 minutes.
        if time.time() >= float(stored.get("expires_at", 0)) - 120:
            stored = _refresh(settings, stored["refresh_token"])

        return {
            "access_token": stored["access_token"],
            "login": stored.get("login", ""),
            "scopes": stored.get("scopes", []),
        }


def force_refresh(
    settings: Settings, *, failed_access_token: str | None = None
) -> dict:
    """Force a token refresh regardless of expiry (used after an auth rejection).

    If `failed_access_token` is provided and `.tokens.json` already contains a
    newer unexpired token rotated by another caller, returns that fresh token
    without issuing a duplicate refresh request.
    """
    with _TOKEN_LOCK:
        stored = _load_tokens()
        if not stored or not stored.get("refresh_token"):
            raise ReauthRequired("No stored login found. Please run the login first.")

        if (
            failed_access_token
            and stored.get("access_token") != failed_access_token
            and time.time() < float(stored.get("expires_at", 0)) - 120
        ):
            return {
                "access_token": stored["access_token"],
                "login": stored.get("login", ""),
                "scopes": stored.get("scopes", []),
            }

        fresh = _refresh(settings, stored["refresh_token"])
        return {
            "access_token": fresh["access_token"],
            "login": fresh.get("login", ""),
            "scopes": fresh.get("scopes", []),
        }


async def get_valid_access_token_async(settings: Settings) -> dict:
    """Non-blocking async wrapper over `get_valid_access_token`.

    Coalesces concurrent coroutines on a per-loop `asyncio.Lock` and executes
    the shared `_TOKEN_LOCK`-guarded refresh inside `asyncio.to_thread()`.
    """
    async with _get_async_lock():
        return await asyncio.to_thread(get_valid_access_token, settings)


async def force_refresh_async(
    settings: Settings, *, failed_access_token: str | None = None
) -> dict:
    """Non-blocking async wrapper over `force_refresh`."""
    async with _get_async_lock():
        return await asyncio.to_thread(
            force_refresh, settings, failed_access_token=failed_access_token
        )


def _refresh(settings: Settings, refresh_token: str) -> dict:
    """Use the refresh token to get a new access token, rotating storage."""
    log.info("Refreshing user access token...")
    previous = _load_tokens() or {}
    try:
        resp = _post_token(
            {
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )
    except TransientTokenError:
        # Preserve valid saved tokens during temporary network/5xx blips.
        raise
    except UserTokenError as exc:
        # A 4xx rejection means the refresh token was revoked/invalid.
        clear_tokens()
        raise ReauthRequired(
            f"Could not refresh your login ({exc}). Please authorize again."
        ) from exc

    # Twitch invalidates the old refresh token immediately upon issuing a new
    # one. Persist the rotated tokens right away (preserving existing login and
    # scope metadata) so a transient /oauth2/validate failure can NEVER lose
    # the rotated refresh token.
    rotated_refresh = resp.get("refresh_token") or refresh_token
    prev_login = str(previous.get("login", ""))
    raw_scopes = resp.get("scope")
    if isinstance(raw_scopes, list) and raw_scopes:
        preserved_scopes = [str(s) for s in raw_scopes]
    else:
        preserved_scopes = list(previous.get("scopes", []))
    expires_at = time.time() + float(resp.get("expires_in", 0))

    _save_tokens(
        {
            "access_token": resp["access_token"],
            "refresh_token": rotated_refresh,
            "login": prev_login,
            "scopes": preserved_scopes,
            "expires_at": expires_at,
        }
    )

    token_resp_for_validate = (
        resp
        if resp.get("refresh_token")
        else {**resp, "refresh_token": rotated_refresh}
    )
    try:
        return _validate_and_store(settings, token_resp_for_validate)
    except TransientTokenError:
        # Rotated access/refresh tokens + preserved login/scopes are already
        # durably saved on disk; let the caller retry safely.
        raise
    except UserTokenError as exc:
        # Permanent 4xx rejection from /oauth2/validate requires re-auth.
        clear_tokens()
        raise ReauthRequired(
            f"Could not validate refreshed login ({exc}). Please authorize again."
        ) from exc


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #

def _post_token(data: dict) -> dict:
    """POST to the token endpoint and return parsed JSON, or raise."""
    try:
        resp = requests.post(_TOKEN_URL, data=data, timeout=10)
    except requests.RequestException as exc:
        raise TransientTokenError(f"Network error contacting Twitch: {exc}") from exc

    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientTokenError(
            f"Twitch temporary token error (HTTP {resp.status_code}): {_safe_msg(resp)}"
        )
    if resp.status_code != 200:
        raise UserTokenError(f"Twitch token error (HTTP {resp.status_code}): {_safe_msg(resp)}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise TransientTokenError(
            f"Malformed JSON from Twitch token endpoint: {_safe_msg(resp)}"
        ) from exc
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise UserTokenError("Twitch response missing access_token.")
    return payload


def _validate_and_store(settings: Settings, token_response: dict) -> dict:
    """Validate the access token, confirm scopes, and persist everything.

    'Validate' also gives us the account login name, which we need later for the
    IRC NICK line.
    """
    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token", "")
    expires_in = float(token_response.get("expires_in", 0))

    try:
        v = requests.get(
            _VALIDATE_URL,
            headers={"Authorization": f"OAuth {access_token}"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise TransientTokenError(f"Network error validating token: {exc}") from exc

    if v.status_code == 429 or v.status_code >= 500:
        raise TransientTokenError(
            f"Temporary error validating token (HTTP {v.status_code}): {_safe_msg(v)}"
        )
    if v.status_code != 200:
        raise UserTokenError(f"Token validation failed (HTTP {v.status_code}): {_safe_msg(v)}")

    try:
        vinfo = v.json()
    except ValueError as exc:
        raise TransientTokenError(
            f"Malformed JSON validating token: {_safe_msg(v)}"
        ) from exc
    if not isinstance(vinfo, dict):
        raise TransientTokenError("Unexpected JSON payload validating token.")

    existing = _load_tokens() or {}
    login = vinfo.get("login") or existing.get("login", "")
    granted = vinfo.get("scopes")
    if granted is None:
        granted = list(existing.get("scopes", []))

    missing = [s for s in SCOPES if s not in granted]
    if missing:
        log.warning(
            "Some requested scopes were NOT granted: %s. "
            "Chat/clips features needing them may fail; re-authorize to fix.",
            ", ".join(missing),
        )

    _save_tokens(
        {
            "access_token": access_token,
            "refresh_token": refresh_token or existing.get("refresh_token", ""),
            "login": login,
            "scopes": granted,
            "expires_at": time.time() + expires_in,
        }
    )
    log.info("Logged in as '%s'. Granted scopes: %s", login, ", ".join(granted) or "(none)")
    return {"access_token": access_token, "login": login, "scopes": granted}


def _parse_host_port(redirect_uri: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 80
    return host, port


def _safe_msg(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("message", resp.text[:200]))
    except ValueError:
        return resp.text[:200]
