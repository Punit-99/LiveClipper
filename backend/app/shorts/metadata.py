"""YouTube Shorts metadata formatting and title sanitization.

Transforms LiveClipper highlight analysis sidecars into YouTube snippet payloads
compliant with YouTube Data API v3 constraints (100-char title limit, 5000-char description,
gaming category, and #Shorts discoverability tags).
"""

from __future__ import annotations

import re
from typing import Any

YOUTUBE_TITLE_MAX = 100
SHORTS_TAG = "#Shorts"
GAMING_CATEGORY_ID = "20"


def sanitize_title(raw_title: str | None, max_length: int = YOUTUBE_TITLE_MAX) -> str:
    """Sanitize and truncate a title to fit YouTube's limit with #Shorts included.

    Truncates on word boundaries where possible and avoids duplicate #Shorts tags.
    Result is guaranteed to be <= max_length characters.
    """
    cleaned = " ".join(str(raw_title or "").strip().split())
    if not cleaned:
        cleaned = "Live Stream Highlight"

    # Check if #Shorts is already present (case-insensitive)
    has_shorts = bool(re.search(r"#shorts\b", cleaned, re.IGNORECASE))

    if has_shorts:
        if len(cleaned) <= max_length:
            return cleaned
        # Need to truncate even though #shorts is present
        # Keep #Shorts tag at the end if possible
        tag_suffix = f" {SHORTS_TAG}"
        available = max_length - len(tag_suffix)
        # Strip existing #shorts tag variations from the base title before truncation
        base = re.sub(r"#shorts\b", "", cleaned, flags=re.IGNORECASE).strip()
        base = " ".join(base.split())
        return _truncate_on_word_boundary(base, available) + tag_suffix

    # #Shorts not present: reserve space for " #Shorts"
    tag_suffix = f" {SHORTS_TAG}"
    available = max_length - len(tag_suffix)
    if len(cleaned) <= available:
        return f"{cleaned}{tag_suffix}"

    truncated_base = _truncate_on_word_boundary(cleaned, available)
    return f"{truncated_base}{tag_suffix}"


def _truncate_on_word_boundary(text: str, max_len: int) -> str:
    """Truncate text to max_len on a word boundary without breaking words."""
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    space_pos = cut.rfind(" ")
    # If the last space is reasonably positioned, cut there; otherwise hard cut
    if space_pos >= max(10, max_len // 2):
        cut = cut[:space_pos]
    return cut.rstrip(" ,.;:-_!?'\"")


def format_description(sidecar_data: dict[str, Any], default_streamer: str = "Streamer") -> str:
    """Build a rich, engaging YouTube Shorts description from LiveClipper sidecar metadata."""
    analysis = sidecar_data.get("analysis") or {}
    streamer = sidecar_data.get("streamer_name") or default_streamer
    # Strip any leading '#' from streamer name
    clean_streamer = streamer.lstrip("#").strip()

    reaction_summary = (analysis.get("reaction_summary") or "").strip()
    category = (analysis.get("category") or "").strip()
    key_quotes = analysis.get("key_quotes") or []

    sections: list[str] = []

    # 1. Lead summary
    if reaction_summary:
        sections.append(reaction_summary)

    # 2. Streamer & moment metadata
    meta_lines: list[str] = [
        f"Streamer: {clean_streamer} (https://twitch.tv/{clean_streamer})"
    ]
    if category:
        clean_category = category.replace("_", " ").title()
        meta_lines.append(f"Highlight Type: {clean_category}")
    sections.append("\n".join(meta_lines))

    # 3. Viewer quotes
    valid_quotes = [str(q).strip().strip('"') for q in key_quotes if str(q).strip()]
    if valid_quotes:
        quote_lines = ["Top Chat Reactions:"]
        for q in valid_quotes[:5]:
            quote_lines.append(f'• "{q}"')
        sections.append("\n".join(quote_lines))

    # 4. Attribution
    sections.append("Captured live and detected by LiveClipper.")

    # 5. Hashtags for discoverability
    tags = [
        SHORTS_TAG,
        f"#{clean_streamer}",
        "#GTA6",
        "#GrandTheftAuto6",
        "#Gaming",
        "#TwitchClips",
        "#LiveStreamHighlights",
    ]
    if category:
        tag_cat = "".join(w.capitalize() for w in category.split("_"))
        tags.append(f"#{tag_cat}")
    sections.append(" ".join(tags))

    full_description = "\n\n".join(sections)
    # YouTube description limit is 5000 characters
    if len(full_description) > 5000:
        full_description = full_description[:4990] + "..."
    return full_description


def format_tags(sidecar_data: dict[str, Any], default_streamer: str = "Streamer") -> list[str]:
    """Generate relevant YouTube video tags from sidecar metadata."""
    analysis = sidecar_data.get("analysis") or {}
    streamer = sidecar_data.get("streamer_name") or default_streamer
    clean_streamer = streamer.lstrip("#").strip()
    category = (analysis.get("category") or "").replace("_", " ").strip()

    candidate_tags = [
        "Shorts",
        "YouTube Shorts",
        clean_streamer,
        f"{clean_streamer} twitch",
        "GTA 6",
        "GTA6",
        "Grand Theft Auto VI",
        "Gaming",
        "Twitch",
        "Live Stream",
        "Funny Moments",
        "Clips",
    ]
    if category:
        candidate_tags.append(category)

    # Deduplicate while preserving order and filter empty
    seen = set()
    result = []
    total_len = 0
    for tag in candidate_tags:
        cleaned = tag.strip()
        lowered = cleaned.lower()
        if cleaned and lowered not in seen:
            # YouTube total tags limit is 500 characters
            if total_len + len(cleaned) + 1 > 480:
                break
            seen.add(lowered)
            result.append(cleaned)
            total_len += len(cleaned) + 1

    return result


def format_youtube_snippet(
    sidecar_data: dict[str, Any],
    default_streamer: str = "Streamer",
    category_id: str = GAMING_CATEGORY_ID,
) -> dict[str, Any]:
    """Format full YouTube Data API v3 video snippet resource.

    Prefers viral-optimized metadata (viral_metadata, selected_viral_title)
    when present, falling back to analytical metadata.
    """
    viral_meta = sidecar_data.get("viral_metadata") or {}
    selected_title = sidecar_data.get("selected_viral_title")

    # Title resolution: selected_viral_title -> viral_metadata title -> analytical title
    if selected_title:
        title = sanitize_title(selected_title)
    elif viral_meta.get("selected_title"):
        title = sanitize_title(viral_meta["selected_title"])
    elif viral_meta.get("titles") and isinstance(viral_meta["titles"], list) and viral_meta["titles"]:
        first_t = viral_meta["titles"][0]
        t_str = first_t.get("title") if isinstance(first_t, dict) else str(first_t)
        title = sanitize_title(t_str)
    else:
        analysis = sidecar_data.get("analysis") or {}
        raw_title = analysis.get("title") or "Stream Highlight"
        title = sanitize_title(raw_title)

    # Description resolution: viral_metadata description -> standard description
    if viral_meta.get("description"):
        description = str(viral_meta["description"]).strip()
    else:
        description = format_description(sidecar_data, default_streamer=default_streamer)

    # Tags resolution: viral_metadata tags -> standard tags
    if viral_meta.get("tags") and isinstance(viral_meta["tags"], list):
        tags = [str(t).strip() for t in viral_meta["tags"] if str(t).strip()]
    else:
        tags = format_tags(sidecar_data, default_streamer=default_streamer)

    return {
        "title": title,
        "description": description,
        "tags": tags,
        "categoryId": str(category_id),
    }
