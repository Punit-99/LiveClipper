"""WebSocket connection manager and event bus for LiveClipper dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("app.api.events")


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts real-time events."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop for thread-safe cross-thread dispatch."""
        self._loop = loop

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new client WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.add(websocket)
        log.info("WebSocket client connected (total=%d)", len(self.active_connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove a disconnected client WebSocket."""
        async with self._lock:
            self.active_connections.discard(websocket)
        log.info("WebSocket client disconnected (remaining=%d)", len(self.active_connections))

    async def broadcast(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Broadcast an event payload to all connected clients."""
        payload = {
            "type": event_type,
            "data": data or {},
            "timestamp": time.time(),
        }
        text = json.dumps(payload)

        async with self._lock:
            connections = list(self.active_connections)

        if not connections:
            return

        dead_connections: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_text(text)
            except Exception as exc:
                log.debug("Failed to send message to websocket client: %s", exc)
                dead_connections.append(ws)

        if dead_connections:
            async with self._lock:
                for ws in dead_connections:
                    self.active_connections.discard(ws)

    def broadcast_sync(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Thread-safe / synchronous broadcast helper."""
        try:
            loop = self._loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(self.broadcast(event_type, data), loop)
            else:
                try:
                    cur_loop = asyncio.get_running_loop()
                    cur_loop.create_task(self.broadcast(event_type, data))
                except RuntimeError:
                    pass
        except Exception as exc:
            log.warning("Could not dispatch broadcast_sync: %s", exc)


# Global singleton instance
ws_manager = ConnectionManager()
