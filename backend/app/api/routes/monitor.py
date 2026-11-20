"""Stream monitoring lifecycle control routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.state import supervisor

router = APIRouter(tags=["Monitoring"])


class MonitorStartRequest(BaseModel):
    platform: str | None = Field(default=None, description="Platform to monitor: 'twitch', 'youtube', or 'all'")
    channels: list[str] | None = Field(default=None, description="Explicit target channel names/IDs")
    languages: list[str] | None = Field(default=None, description="Language codes to filter (e.g. ['en', 'hi'])")


@router.post("/monitor/start")
async def start_monitoring(req: MonitorStartRequest | None = None) -> dict[str, Any]:
    """Start live stream monitoring with specified or configured platforms and channels."""
    payload = req or MonitorStartRequest()
    try:
        res = await supervisor.start(
            platform=payload.platform,
            channels=payload.channels,
            languages=payload.languages,
        )
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to start stream monitoring: {exc}",
        ) from exc


@router.post("/monitor/stop")
async def stop_monitoring() -> dict[str, Any]:
    """Gracefully stop stream monitoring and recording."""
    try:
        res = await supervisor.stop()
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to stop stream monitoring: {exc}",
        ) from exc


@router.post("/monitor/approve/{channel_id}")
async def approve_fallback(channel_id: str) -> dict[str, Any]:
    """Approve a discovered fallback stream and start monitoring it."""
    try:
        res = await supervisor.approve_fallback(channel_id)
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to approve fallback stream: {exc}",
        ) from exc


@router.post("/monitor/wait-target")
async def wait_target_channels() -> dict[str, Any]:
    """Skip alternative streams and keep polling configured target channels."""
    try:
        res = await supervisor.wait_for_targets()
        return res
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to set wait for target channels: {exc}",
        ) from exc

