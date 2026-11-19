"""Chat monitoring (Twitch IRC & YouTube Live REST polling).

- irc_parser.py   -> pure text parsing of raw IRC lines.
- twitch_chat.py  -> Twitch WebSocket connection & read loop.
- youtube_chat.py -> YouTube Live Data API v3 chat polling & quota management.
"""

from app.chat.irc_parser import IrcMessage
from app.chat.twitch_chat import ChatMonitor
from app.chat.youtube_chat import YouTubeChatMonitor

# Alias for backwards and multi-platform naming clarity
TwitchChatMonitor = ChatMonitor

__all__ = ["ChatMonitor", "IrcMessage", "TwitchChatMonitor", "YouTubeChatMonitor"]
