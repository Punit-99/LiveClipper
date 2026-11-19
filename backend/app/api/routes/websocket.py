"""WebSocket live feed route."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.events import ws_manager
from app.api.state import supervisor

log = logging.getLogger("app.api.websocket")
router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws/live")
async def websocket_live_endpoint(websocket: WebSocket) -> None:
    """Live bidirectional WebSocket channel pushing real-time monitoring and processing events."""
    await ws_manager.connect(websocket)

    # Push immediate initial status on connection
    try:
        initial_status = supervisor.get_status()
        await websocket.send_json({
            "type": "init_status",
            "data": initial_status,
        })
    except Exception as exc:
        log.warning("Could not send initial status to WebSocket: %s", exc)

    try:
        while True:
            # Keep connection open; client can send pings or requests
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except asyncio.CancelledError:
        await ws_manager.disconnect(websocket)
    except Exception as exc:
        log.debug("WebSocket exception: %s", exc)
        await ws_manager.disconnect(websocket)
