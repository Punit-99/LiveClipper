"""Entry point: one-time Twitch login to get a User Access Token.

Run it from the backend/ folder:
    python authorize.py

It opens your browser, asks you to authorize LiveClipper (scopes: chat:read,
clips:edit), then saves the token locally. This does NOT connect to chat yet
 that's the next milestone.

Re-run it any time you need to log in again (e.g. after revoking access).
"""

from __future__ import annotations

import sys

from app.auth.user_token import UserTokenError, get_valid_access_token, run_login_flow
from app.config import ConfigError, load_settings
from app.logging_setup import get_logger

log = get_logger("authorize")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        log.error("Configuration problem: %s", exc)
        return 2

    # If we already have a valid/refreshable token, say so instead of forcing a
    # new browser login.
    try:
        info = get_valid_access_token(settings)
        print(
            f"\nAlready authorized as '{info['login']}'. "
            f"Scopes: {', '.join(info['scopes']) or '(none)'}."
        )
        print("Delete backend/.tokens.json if you want to re-authorize from scratch.\n")
        return 0
    except UserTokenError:
        pass  # no usable token yet -> do the full login below

    try:
        info = run_login_flow(settings)
    except UserTokenError as exc:
        log.error("Login failed: %s", exc)
        return 1

    print(
        f"\n✓ Login flow works. Authorized as '{info['login']}'.\n"
        f"  Scopes granted: {', '.join(info['scopes']) or '(none)'}\n"
        f"  Token saved to backend/.tokens.json (git-ignored, owner-only).\n"
        f"  Next milestone: connect to a channel's chat.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
