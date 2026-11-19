"""Channel resolution API route."""

from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.discovery.youtube_finder import resolve_channel_api

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
