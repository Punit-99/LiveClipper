"""Interactive CLI helper for one-time Google YouTube OAuth 2.0 authorization.

Run it from the backend/ folder:
    python authorize_youtube.py

Opens your browser for Google consent, receives the callback on localhost,
and saves the refresh token securely to .youtube_tokens.json (0600 permissions).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import requests

from app.config import ConfigError, load_settings
from app.logging_setup import get_logger
from app.shorts.youtube_auth import (
    GOOGLE_AUTH_URL,
    GOOGLE_TOKEN_URL,
    YOUTUBE_SCOPES,
    YouTubeTokenManager,
)

log = get_logger("authorize_youtube")

DEFAULT_PORT = 8085


class _YouTubeCallbackHandler(BaseHTTPRequestHandler):
    """Handles OAuth 2.0 redirect from Google to localhost."""

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/", "/callback", ""):
            self.send_response(404)
            self.end_headers()
            return

        query = urllib.parse.parse_qs(parsed.query)
        self.server.oauth_result = {  # type: ignore[attr-defined]
            "code": query.get("code", [None])[0],
            "state": query.get("state", [None])[0],
            "error": query.get("error", [None])[0],
        }

        ok = self.server.oauth_result.get("code") is not None  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if ok:
            body = (
                "<html><body style='font-family:system-ui,sans-serif;padding:40px;line-height:1.6'>"
                "<h2 style='color:#16a34a'>✓ YouTube Authorization Complete!</h2>"
                "<p>LiveClipper has successfully received your authorization code.</p>"
                "<p>You can close this tab and return to your terminal.</p>"
                "</body></html>"
            )
        else:
            err = self.server.oauth_result.get("error") or "Unknown error"  # type: ignore[attr-defined]
            body = (
                "<html><body style='font-family:system-ui,sans-serif;padding:40px;line-height:1.6'>"
                f"<h2 style='color:#dc2626'>✗ YouTube Authorization Failed ({err})</h2>"
                "<p>Return to your terminal for error details.</p>"
                "</body></html>"
            )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args: Any) -> None:
        # Silence default HTTP server access logs
        pass


def run_google_login_flow(
    client_id: str,
    client_secret: str,
    tokens_path: Path,
    port: int = DEFAULT_PORT,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Execute interactive OAuth login flow."""
    redirect_uri = f"http://localhost:{port}/callback"
    state = secrets.token_urlsafe(24)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    try:
        server = HTTPServer(("localhost", port), _YouTubeCallbackHandler)
    except OSError as exc:
        raise RuntimeError(
            f"Could not bind local callback server to port {port} ({exc}). "
            f"Specify a different port with --port."
        ) from exc

    server.oauth_result = None  # type: ignore[attr-defined]
    server.timeout = 1.0

    print("\n" + "=" * 70)
    print("  LiveClipper: Google YouTube Shorts Authorization")
    print("=" * 70)
    print(f"\nOpening browser for consent (redirect: {redirect_uri})...")
    print(f"Requested Scopes: {', '.join(YOUTUBE_SCOPES)}")
    print("\nIf your browser does not open automatically, visit:")
    print(auth_url + "\n")
    print("Waiting for authorization callback (Ctrl+C to abort)...")

    webbrowser.open(auth_url)

    deadline = time.time() + timeout_seconds
    try:
        while server.oauth_result is None:  # type: ignore[attr-defined]
            if time.time() > deadline:
                raise TimeoutError("Timed out waiting for authorization in the browser.")
            server.handle_request()
    finally:
        server.server_close()

    result = server.oauth_result  # type: ignore[attr-defined]
    if result.get("error"):
        raise RuntimeError(f"Authorization denied by Google: {result['error']}")
    if result.get("state") != state:
        raise RuntimeError("State parameter mismatch (possible CSRF attack). Aborting.")
    code = result.get("code")
    if not code:
        raise RuntimeError("No authorization code received from Google.")

    print("\nExchanging authorization code for OAuth tokens...")
    token_resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=15.0,
    )

    if not token_resp.ok:
        raise RuntimeError(
            f"Google token exchange failed (HTTP {token_resp.status_code}): {token_resp.text}"
        )

    token_data = token_resp.json()
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    expires_in = token_data.get("expires_in", 3600)
    scope = token_data.get("scope")

    if not access_token or not refresh_token:
        raise RuntimeError(
            "Google did not return a refresh token. Ensure 'access_type=offline' and 'prompt=consent'."
        )

    manager = YouTubeTokenManager(
        tokens_path=tokens_path,
        client_id=client_id,
        client_secret=client_secret,
    )
    manager.save_tokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        scope=scope,
    )

    return token_data


def main() -> int:
    parser = argparse.ArgumentParser(description="Authorize LiveClipper to upload to YouTube Shorts.")
    parser.add_argument("--client-id", help="Google OAuth2 Client ID (overrides .env)")
    parser.add_argument("--client-secret", help="Google OAuth2 Client Secret (overrides .env)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Local callback port (default: {DEFAULT_PORT})")
    parser.add_argument("--tokens-path", help="Path to tokens file (default: .youtube_tokens.json)")
    parser.add_argument("--force", action="store_true", help="Re-authorize even if valid token exists")
    args = parser.parse_args()

    # Load from settings if available
    client_id = args.client_id
    client_secret = args.client_secret
    tokens_path = Path(args.tokens_path) if args.tokens_path else None

    try:
        settings = load_settings()
        client_id = client_id or settings.youtube_client_id
        client_secret = client_secret or settings.youtube_client_secret
        if not tokens_path:
            tokens_path = settings.youtube_tokens_path
    except ConfigError:
        pass

    if not tokens_path:
        tokens_path = Path(".youtube_tokens.json")

    # If client credentials missing, print actionable setup instructions
    if not client_id or not client_secret:
        print("\n" + "!" * 70)
        print("  ERROR: Missing YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET")
        print("!" * 70)
        print("\nTo set up YouTube upload:")
        print("  1. Go to Google Cloud Console (https://console.cloud.google.com).")
        print("  2. Create a project and enable 'YouTube Data API v3'.")
        print("  3. Go to 'APIs & Services' -> 'Credentials'.")
        print("  4. Click 'Create Credentials' -> 'OAuth client ID' (Type: Desktop app).")
        print("  5. Add credentials to backend/.env:")
        print("       YOUTUBE_CLIENT_ID=your-client-id.apps.googleusercontent.com")
        print("       YOUTUBE_CLIENT_SECRET=your-client-secret")
        print("     or pass them as flags: --client-id and --client-secret\n")
        return 2

    # Check if existing token is already usable
    manager = YouTubeTokenManager(
        tokens_path=tokens_path,
        client_id=client_id,
        client_secret=client_secret,
    )

    if manager.has_tokens() and not args.force:
        try:
            tok = manager.get_valid_access_token()
            print(f"\n✓ Already authorized for YouTube Shorts!")
            print(f"  Tokens location: {tokens_path.resolve()}")
            print(f"  Access token valid (hint: {tok[:8]}...)")
            print("  Use --force to re-authenticate from scratch.\n")
            return 0
        except Exception:
            print("\nExisting token expired or invalid; starting new authorization flow...")

    try:
        run_google_login_flow(
            client_id=client_id,
            client_secret=client_secret,
            tokens_path=tokens_path,
            port=args.port,
        )
    except KeyboardInterrupt:
        print("\nAuthorization cancelled by user.")
        return 130
    except Exception as exc:
        log.error("YouTube authorization failed: %s", exc)
        print(f"\n✗ Error: {exc}\n")
        return 1

    print("\n" + "=" * 70)
    print("✓ YouTube Shorts authorization successful!")
    print(f"  Token saved to {tokens_path.resolve()} (owner-only permissions).")
    print("  You are ready to upload shorts via LiveClipper.")
    print("=" * 70 + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
