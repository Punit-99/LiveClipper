"""Connect to ONE Twitch channel's chat over IRC-via-WebSocket and print messages.

Flow:
  1. Get a valid user token (reusing the Milestone 2 manager; auto-refreshes).
  2. Open wss://irc-ws.chat.twitch.tv:443.
  3. Handshake: CAP REQ (tags+commands) -> PASS oauth:<token> -> NICK <login>.
  4. On the "001 Welcome" reply, JOIN #<channel>.
  5. Read loop: answer PING with PONG, print each PRIVMSG with Twitch's timestamp.
  6. On disconnect, reconnect with exponential backoff. On a login rejection,
     force one token refresh; if that fails, ask the user to re-authorize.

Security: the token is sent only in the PASS line to Twitch and is NEVER logged.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

import websockets

from app.auth.user_token import (
    ReauthRequired,
    TransientTokenError,
    force_refresh_async,
    get_valid_access_token_async,
)
from app.chat.irc_parser import (
    display_name,
    parse_line,
    sent_timestamp_ms,
)
from app.config import Settings
from app.detection import ChatMessage, StreamHighlightDetector, emote_count_from_tags
from app.logging_setup import get_logger

log = get_logger(__name__)

_WS_URL = "wss://irc-ws.chat.twitch.tv:443"
_MAX_BACKOFF = 30


class ChatMonitor:
    """Monitors a single channel's chat. Call `await monitor.run()`."""

    def __init__(
        self,
        settings: Settings,
        channel: str,
        pipeline: StreamHighlightDetector | None = None,
        *,
        print_messages: bool = True,
    ):
        self._settings = settings
        self._channel = channel.lstrip("#").strip().lower()
        self._pipeline = pipeline  # optional; detection is decoupled from networking
        self._print_messages = print_messages
        self._stop = False
        self._stop_event: asyncio.Event | None = None
        self._ws: Any | None = None
        self._close_tasks: set[asyncio.Task[None]] = set()
        self._backoff = 1
        self._force_token_refresh = False  # set True after a login rejection
        self._auth_failures = 0            # consecutive login rejections
        self._last_access_token: str | None = None

    async def run(self) -> None:
        """Connect, and keep reconnecting until stopped or re-auth is needed."""
        if not self._channel:
            log.error("No channel given. Usage: python monitor_chat.py <channel>")
            return

        self._stop_event = asyncio.Event()
        if self._stop:
            self._stop_event.set()

        while not self._stop:
            try:
                await self._session()
            except ReauthRequired as exc:
                log.error("[%s] Login can no longer be refreshed: %s", self._channel, exc)
                if self._print_messages:
                    print(
                        "\nYour Twitch login is no longer valid. Please re-authorize:\n"
                        "    ./venv/bin/python authorize.py\n"
                    )
                return
            except (
                websockets.ConnectionClosed,
                TransientTokenError,
                OSError,
                asyncio.TimeoutError,
            ) as exc:
                if not self._stop:
                    log.warning("[%s] Connection lost: %s", self._channel, exc)

            if self._stop:
                break
            log.info("[%s] Reconnecting in %ds...", self._channel, self._backoff)
            await self._interruptible_sleep(self._backoff)
            self._backoff = min(self._backoff * 2, _MAX_BACKOFF)

        log.info("[%s] Chat monitor stopped.", self._channel)

    def stop(self) -> None:
        """Signal the monitor to stop and schedule closing any active WebSocket."""
        self._stop = True
        if self._stop_event is not None:
            self._stop_event.set()
        ws = self._ws
        if ws is not None:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._safe_close_ws(ws))
                self._close_tasks.add(task)
                task.add_done_callback(self._close_tasks.discard)
            except RuntimeError:
                pass

    async def aclose(self) -> None:
        """Stop monitoring and await closing the active WebSocket cleanly."""
        self._stop = True
        if self._stop_event is not None:
            self._stop_event.set()
        ws = self._ws
        if ws is not None:
            await self._safe_close_ws(ws)
        if self._close_tasks:
            await asyncio.gather(*list(self._close_tasks), return_exceptions=True)

    @staticmethod
    async def _safe_close_ws(ws: Any) -> None:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001  already closed or broken socket
            pass

    @staticmethod
    async def _safe_aexit(connect_cm: Any) -> None:
        try:
            await connect_cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001  already closed or broken socket
            pass

    async def _connect_interruptible(self, connect_cm: Any) -> Any | None:
        """Enter a WebSocket connection context manager while honoring stop/aclose."""
        if self._stop:
            return None
        stop_event = self._stop_event
        if stop_event is None:
            ws = await connect_cm.__aenter__()
            if self._stop:
                await self._safe_aexit(connect_cm)
                return None
            return ws

        connect_task = asyncio.create_task(connect_cm.__aenter__())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            done, _ = await asyncio.wait(
                {connect_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if connect_task in done:
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
                ws = connect_task.result()
                if self._stop:
                    await self._safe_aexit(connect_cm)
                    return None
                return ws

            # Stop was requested while the handshake was still in flight.
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)
            if not connect_task.cancelled() and connect_task.exception() is None:
                await self._safe_aexit(connect_cm)
            return None
        except BaseException:
            for task in (connect_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(connect_task, stop_task, return_exceptions=True)
            if (
                connect_task.done()
                and not connect_task.cancelled()
                and connect_task.exception() is None
            ):
                await self._safe_aexit(connect_cm)
            raise

    async def _interruptible_sleep(self, seconds: float) -> None:
        if self._stop or seconds <= 0:
            return
        if self._stop_event is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # -- one connection attempt -------------------------------------------------

    async def _session(self) -> None:
        """Open one connection, do the handshake, and run the read loop.

        Returns normally when the connection ends (so run() reconnects).
        """
        token_info = await self._get_token_async()
        token = token_info["access_token"]
        login = token_info["login"]
        self._last_access_token = token

        if self._stop:
            return

        log.info("Connecting to Twitch chat for channel '#%s'...", self._channel)
        connect_cm = websockets.connect(_WS_URL, open_timeout=10)
        ws = await self._connect_interruptible(connect_cm)
        if ws is None:
            return

        flush_task: asyncio.Task[None] | None = None
        try:
            self._ws = ws
            if self._stop:
                return
            # Order matters: capabilities, then password, then nick.
            await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
            await ws.send(f"PASS oauth:{token}")   # token value never logged
            await ws.send(f"NICK {login}")
            log.info("Authenticating as '%s' (token not shown)...", login)

            # Periodically flush the detector so events close even if chat pauses.
            if self._pipeline is not None:
                flush_task = asyncio.create_task(self._flush_loop())

            async for frame in ws:
                if self._stop:
                    return
                # A single WebSocket frame may hold several IRC lines.
                for line in str(frame).split("\r\n"):
                    if line:
                        if await self._handle_line(ws, line):
                            return  # a handler asked us to reconnect/stop
        finally:
            self._ws = None
            if flush_task is not None:
                flush_task.cancel()
                await asyncio.gather(flush_task, return_exceptions=True)
            await self._safe_aexit(connect_cm)

    async def _flush_loop(self) -> None:
        """Call the pipeline's tick ~once per second to finalize due events."""
        while True:
            await asyncio.sleep(1.0)
            try:
                if self._pipeline is not None:
                    self._pipeline.tick(time.monotonic(), time.time())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001  never let the flusher crash the session
                log.exception("Detector flush error (chat continues).")

    async def _get_token_async(self) -> dict:
        """Return a usable token without blocking the asyncio event loop."""
        if self._force_token_refresh:
            self._force_token_refresh = False
            return await force_refresh_async(
                self._settings, failed_access_token=self._last_access_token
            )
        return await get_valid_access_token_async(self._settings)

    # -- line handling ----------------------------------------------------------

    async def _handle_line(self, ws, line: str) -> bool:
        """Process one IRC line. Return True if the session should end."""
        msg = parse_line(line)
        cmd = msg.command.upper()

        if cmd == "PING":
            # Keepalive: we MUST answer or Twitch disconnects us.
            await ws.send("PONG :tmi.twitch.tv")
            return False

        if cmd == "001":  # RPL_WELCOME: auth succeeded.
            log.info("Authenticated. Joining #%s ...", self._channel)
            await ws.send(f"JOIN #{self._channel}")
            self._backoff = 1       # healthy connection; reset reconnect delay
            self._auth_failures = 0  # a good login clears the failure count
            return False

        if cmd == "JOIN":
            log.info("Joined #%s  now listening for messages.", self._channel)
            if self._print_messages:
                print(f"\n--- Live chat for #{self._channel} (Ctrl+C to stop) ---\n")
            return False

        if cmd == "PRIVMSG":
            if self._print_messages:
                self._print_message(msg)
            # Feed the detection pipeline (if any). Monotonic clock for interval
            # math; wall-clock (Twitch tmi-sent-ts when present) for the record.
            # A detector fault must NEVER break chat reception, so we isolate it.
            if self._pipeline is not None:
                try:
                    self._pipeline.process(
                        self._to_chat_message(msg), time.monotonic(), time.time()
                    )
                except Exception:  # noqa: BLE001  defensive: keep chat alive
                    log.exception("Detection pipeline error (chat continues).")
            return False

        if cmd == "RECONNECT":
            # Twitch is asking us to reconnect to a fresh server.
            log.info("Twitch requested RECONNECT; reconnecting.")
            return True

        if cmd == "NOTICE" and "authentication failed" in msg.text.lower():
            self._auth_failures += 1
            if self._auth_failures > 1:
                # We already refreshed once and STILL got rejected -> re-auth.
                raise ReauthRequired("Login still rejected after a token refresh.")
            log.warning("Twitch rejected the login; forcing a token refresh and retrying.")
            self._force_token_refresh = True
            return True  # end this session; run() reconnects with a fresh token

        return False

    def _print_message(self, msg) -> None:
        ms = sent_timestamp_ms(msg)
        if ms is not None:
            when = datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")
        else:
            when = datetime.now().strftime("%H:%M:%S")  # fallback: local receive time
        print(f"[{when}] {display_name(msg)}: {msg.text}")

    def _to_chat_message(self, msg) -> ChatMessage:
        """Build a detection ChatMessage from a parsed IRC message."""
        ms = sent_timestamp_ms(msg)
        wall_ts = (ms / 1000.0) if ms is not None else time.time()
        return ChatMessage(
            username=display_name(msg),
            text=msg.text,
            timestamp=wall_ts,
            emote_count=emote_count_from_tags(msg.tags),
            message_id=msg.tags.get("id"),
        )
