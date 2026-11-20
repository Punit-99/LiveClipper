"""WebSocket connection manager and event bus for LiveClipper dashboard."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

log = logging.getLogger("app.api.events")


class _SuppressSocketSendFilter(logging.Filter):
    """Filter to suppress repeated 'socket.send() raised exception.' warnings from closed sockets."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "socket.send() raised exception" not in record.getMessage()


# Install the filter on the asyncio logger once at module import
logging.getLogger("asyncio").addFilter(_SuppressSocketSendFilter())


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts real-time events."""

    def __init__(self) -> None:
        self.active_connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running event loop for thread-safe cross-thread dispatch and suppress broken socket noise."""
        self._loop = loop
        orig_handler = loop.get_exception_handler()

        def _suppress_closed_socket_handler(lp: asyncio.AbstractEventLoop, ctx: dict[str, Any]) -> None:
            msg = ctx.get("message", "")
            if "socket.send() raised exception" in msg:
                return
            if orig_handler is not None:
                orig_handler(lp, ctx)
            else:
                lp.default_exception_handler(ctx)

        loop.set_exception_handler(_suppress_closed_socket_handler)

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
                # Fast-check connection state: skip if already disconnected
                if getattr(ws, "client_state", None) != WebSocketState.CONNECTED:
                    dead_connections.append(ws)
                    continue
                await ws.send_text(text)
            except (WebSocketDisconnect, ConnectionResetError, BrokenPipeError, RuntimeError, OSError) as exc:
                log.debug("WebSocket client disconnected during send: %s", exc)
                dead_connections.append(ws)
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
