r"""Parse raw Twitch IRC lines into something easy to use.

This file has NO networking  it's pure text in, structured data out  so it can
be unit-tested offline. That matters because message parsing is the part most
likely to have subtle bugs.

A Twitch IRC line can have up to three parts before the parameters:

    @key=val;key2=val2 :nick!nick@nick.tmi.twitch.tv PRIVMSG #channel :hello world
    \________________/ \_______________________________/ \_____/ \______/ \_________/
         tags (opt)              source/prefix (opt)       command  params   trailing

- tags    : optional, starts with '@', semicolon-separated key=value metadata
            (this is where tmi-sent-ts and display-name live).
- source  : optional, starts with ':', tells us who sent it.
- command : e.g. PRIVMSG, PING, NOTICE, RECONNECT, or a numeric like 001.
- params  : space-separated; the last one may be "trailing" (prefixed by ':'),
            which for PRIVMSG is the actual chat text.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IrcMessage:
    """A parsed IRC line."""

    raw: str
    tags: dict[str, str] = field(default_factory=dict)
    nick: str = ""            # the sending user's login (from the source prefix)
    command: str = ""         # PRIVMSG, PING, NOTICE, 001, ...
    params: list[str] = field(default_factory=list)
    text: str = ""           # the trailing param (chat text for PRIVMSG)

    @property
    def channel(self) -> str:
        """The '#channel' this line targets, if any (without the '#')."""
        for p in self.params:
            if p.startswith("#"):
                return p[1:]
        return ""


def _parse_tags(raw_tags: str) -> dict[str, str]:
    """Turn 'a=1;b=;c=hello' into {'a': '1', 'b': '', 'c': 'hello'}."""
    tags: dict[str, str] = {}
    for pair in raw_tags.split(";"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        # Twitch escapes some characters in tag values per the IRCv3 spec.
        tags[key] = _unescape_tag(value)
    return tags


def _unescape_tag(value: str) -> str:
    """Decode IRCv3 tag escapes (\\s -> space, etc.)."""
    if "\\" not in value:
        return value
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append(
                {"s": " ", ":": ";", "r": "\r", "n": "\n", "\\": "\\"}.get(nxt, nxt)
            )
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _nick_from_source(source: str) -> str:
    """Extract the login from a source like 'nick!nick@nick.tmi.twitch.tv'."""
    # Strip a leading ':' if present, then take everything before '!'.
    source = source.lstrip(":")
    return source.split("!", 1)[0]


def parse_line(line: str) -> IrcMessage:
    """Parse one raw IRC line (without the trailing \\r\\n) into an IrcMessage."""
    raw = line
    tags: dict[str, str] = {}
    nick = ""

    # 1) Optional tags.
    if line.startswith("@"):
        tag_part, _, line = line.partition(" ")
        tags = _parse_tags(tag_part[1:])

    # 2) Optional source/prefix.
    if line.startswith(":"):
        source_part, _, line = line.partition(" ")
        nick = _nick_from_source(source_part)

    # 3) Command + params. The trailing param begins at the first " :".
    text = ""
    if " :" in line:
        before, _, trailing = line.partition(" :")
        text = trailing
        tokens = before.split()
    else:
        tokens = line.split()

    command = tokens[0] if tokens else ""
    params = tokens[1:]
    if text:
        params = params + [text]

    return IrcMessage(
        raw=raw, tags=tags, nick=nick, command=command, params=params, text=text
    )


def display_name(msg: IrcMessage) -> str:
    """Best available username: the 'display-name' tag, else the source nick."""
    return msg.tags.get("display-name") or msg.nick or "unknown"


def sent_timestamp_ms(msg: IrcMessage) -> int | None:
    """Twitch's server send time in epoch milliseconds, if present."""
    ts = msg.tags.get("tmi-sent-ts")
    if ts and ts.isdigit():
        return int(ts)
    return None
