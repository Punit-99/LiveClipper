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
    """Check live/offline status for all configured YouTube channels."""
    settings = load_settings()
    results: list[dict[str, Any]] = []

    # YouTube channels (targets + excluded)
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

    return {"channels": results}

