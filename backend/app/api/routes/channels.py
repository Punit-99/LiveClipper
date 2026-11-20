"""Channel resolution and live status API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import load_settings
from app.discovery.stream_finder import StreamFinder
from app.discovery.youtube_finder import (
    check_youtube_channel_live_status,
    resolve_channel_api,
    resolve_channel_name,
)
from app.shorts.youtube_auth import YouTubeTokenManager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/channels", tags=["channels"])


class ChannelResolutionResponse(BaseModel):
    id: str
    name: str
    resolved: bool


@router.get("/resolve", response_model=ChannelResolutionResponse)
def resolve_channel(id: str = Query(..., description="YouTube Channel ID")) -> ChannelResolutionResponse:
    """Resolve a YouTube channel ID to its display name."""
    clean_id = (id or "").strip()
    if not clean_id:
        return ChannelResolutionResponse(id="", name="", resolved=False)

    name, resolved = resolve_channel_api(clean_id)
    return ChannelResolutionResponse(id=clean_id, name=name, resolved=resolved)


@router.get("/live-status")
def get_channels_live_status() -> dict[str, Any]:
    """Check live/offline status for all configured channels (YouTube and Twitch)."""
    settings = load_settings()
    results: list[dict[str, Any]] = []

    # 1. YouTube channels (targets + excluded)
    all_yt: list[str] = []
    for c in list(settings.target_youtube_channels) + list(settings.excluded_youtube_channels):
        c_str = str(c).strip()
        if c_str and c_str not in all_yt:
            all_yt.append(c_str)

    tm = None
    if settings.youtube_tokens_path and settings.youtube_tokens_path.is_file():
        try:
            tm = YouTubeTokenManager(
                tokens_path=settings.youtube_tokens_path,
                client_id=settings.youtube_client_id,
                client_secret=settings.youtube_client_secret,
            )
        except Exception:
            pass

    for cid in all_yt:
        st = check_youtube_channel_live_status(
            channel_id=cid,
            token_manager=tm,
            api_key=settings.gemini_api_key,
        )
        st["is_target"] = cid in settings.target_youtube_channels
        st["is_excluded"] = cid in settings.excluded_youtube_channels
        results.append(st)

    # 2. Twitch channels (targets + excluded)
    all_twitch: list[str] = []
    for c in list(settings.target_channels) + list(settings.excluded_target_channels):
        norm = str(c).lstrip("#").strip().lower()
        if norm and norm not in all_twitch:
            all_twitch.append(norm)

    twitch_live: dict[str, Any] = {}
    if all_twitch:
        try:
            finder = StreamFinder(settings)
            twitch_live = finder.check_live_channels(all_twitch)
        except Exception as exc:
            log.warning("Twitch live status check failed: %s", exc)

        for ch in all_twitch:
            livestream = twitch_live.get(ch)
            is_live = livestream is not None
            display_name = livestream.display_name if livestream else ch
            viewers = livestream.viewer_count if livestream else 0
            title = livestream.title if livestream else ""
            lang = livestream.language if livestream else "en"

            results.append({
                "id": ch,
                "name": display_name,
                "platform": "twitch",
                "is_live": is_live,
                "viewers": viewers,
                "title": title,
                "category": settings.category_name,
                "language": lang,
                "is_target": ch in [t.lower() for t in settings.target_channels],
                "is_excluded": ch in [t.lower() for t in settings.excluded_target_channels],
            })

    return {"channels": results}

