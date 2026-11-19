"""Viral YouTube Shorts metadata optimization engine.

Transforms analytical highlight detection output (titles, categories, reaction summaries,
key chat quotes) into high-converting YouTube Shorts metadata (titles, descriptions, tags)
designed to maximize click-through rate, search visibility, and algorithm performance.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.logging_setup import get_logger
from app.shorts.metadata import SHORTS_TAG, YOUTUBE_TITLE_MAX, sanitize_title

log = get_logger(__name__)

# Emoji pattern for stripping when VIRAL_TITLE_EMOJI=false
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)

VIRAL_METADATA_SYSTEM_PROMPT = """You are an elite short-form video content strategist and viral YouTube Shorts optimization expert.
Your job is to transform Twitch stream highlight metadata into viral-optimized YouTube Shorts titles, description, and tags.

Title Requirements:
- Output exactly 3 distinct viral title options ranked 1 to 3 by predicted click-through potential (Rank 1: Highest potential).
- Maximum 100 characters per title (hard limit, including '#Shorts').
- Each title MUST end with '#Shorts'.
- Use proven viral patterns:
  1. Curiosity gap ("He did NOT expect this to happen... #Shorts")
  2. Emotional hook / reaction bait ("Chat went CRAZY when this happened #Shorts")
  3. Pattern interrupt ("Wait for the end... #Shorts")
  4. Shock / disbelief ("This moment completely broke the stream #Shorts")
- Use at most 1-2 contextual emojis (💀, 😂, 😭, 🤯, 🔥, ⚡, 😳).
- Include the streamer's name when it adds strong recognizability or context (e.g. Kai Cenat, xQc).
- Avoid misleading clickbait that misrepresents the content.
- Adapt tone to the category:
  - funny_moment: humor, disbelief, laughing/skull emojis (😂, 💀).
  - streamer_fail: reaction bait, hilarious catastrophe (💀, 😭).
  - exciting_gameplay / hype_moment: high energy, excitement, shock (🔥, 🤯).
  - unexpected_moment: curiosity gap, suspense.

Description Requirements:
- Strictly under 500 characters total for optimal mobile view.
- Structure:
  Line 1: High-converting hook sentence.
  Line 2: Context about the moment (what happened and why it is crazy/funny).
  Line 3: 🎮 Streamer: @<streamer_name>
  Line 4: 🎮 Game: GTA 6
  Line 5: Follow for more daily stream highlights!
  Line 6: Hashtags block: #Shorts #<StreamerName> #GTA6 #Gaming #TwitchHighlights

Tag Requirements:
- Output a deduplicated list of search-optimized tags.
- Total comma-separated characters of all tags combined MUST be <= 480 characters.
- Include streamer name variations, GTA 6 / Grand Theft Auto variations, category tags, gaming keywords.
"""


class ViralTitleOption(BaseModel):
    """A single viral title candidate with style metadata."""

    title: str = Field(description="Viral YouTube Short title, max 100 chars, ending in #Shorts")
    style: str = Field(description="Viral pattern used (e.g. curiosity_gap, emotional_hook, reaction_bait, pattern_interrupt)")
    predicted_impact: str = Field(description="Brief rationale for click-through potential")


class ViralMetadataPayload(BaseModel):
    """Structured response expected from Gemini for viral metadata."""

    titles: list[ViralTitleOption] = Field(
        description="Exactly 3 viral title options ranked best to third best"
    )
    description: str = Field(
        description="YouTube Shorts description under 500 characters"
    )
    tags: list[str] = Field(
        description="List of optimized video tags under 480 total characters"
    )


class ViralMetadata(BaseModel):
    """Complete viral metadata package with provenance."""

    titles: list[dict[str, str]]
    description: str
    tags: list[str]
    selected_title: str | None = None
    generated_at_iso: str | None = None
    model_used: str | None = None
    is_fallback: bool = False

    def model_post_init(self, __context: Any) -> None:
        if not self.selected_title and self.titles:
            first_t = self.titles[0]
            self.selected_title = first_t.get("title") if isinstance(first_t, dict) else str(first_t)
        if not self.generated_at_iso:
            self.generated_at_iso = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "titles": self.titles,
            "description": self.description,
            "tags": self.tags,
            "selected_title": self.selected_title,
            "generated_at_iso": self.generated_at_iso,
            "model_used": self.model_used,
            "is_fallback": self.is_fallback,
        }


def strip_emojis(text: str) -> str:
    """Remove emoji characters from text and normalize whitespace."""
    no_emoji = _EMOJI_PATTERN.sub("", text)
    return " ".join(no_emoji.split())


def sanitize_viral_title(title: str, allow_emoji: bool = True) -> str:
    """Sanitize and format a viral title to be <= 100 chars with #Shorts tag."""
    cleaned = title.strip()
    if not allow_emoji:
        cleaned = strip_emojis(cleaned)

    # Use standard sanitize_title to ensure #Shorts is present and <= 100 chars
    return sanitize_title(cleaned, max_length=YOUTUBE_TITLE_MAX)


def sanitize_viral_description(description: str, max_chars: int = 500) -> str:
    """Sanitize description ensuring it remains <= max_chars (default 500)."""
    cleaned = description.strip()
    if len(cleaned) <= max_chars:
        return cleaned

    # Attempt to cut on paragraph/line boundary
    lines = cleaned.split("\n")
    truncated: list[str] = []
    current_len = 0

    for line in lines:
        line_cost = len(line) + (1 if truncated else 0)
        if current_len + line_cost <= max_chars:
            truncated.append(line)
            current_len += line_cost
        else:
            break

    if truncated:
        return "\n".join(truncated)

    return cleaned[:max_chars - 3].rstrip() + "..."


def sanitize_viral_tags(tags: list[str], max_total_chars: int = 480) -> list[str]:
    """Deduplicate tags case-insensitively and clamp combined length <= max_total_chars."""
    seen: set[str] = set()
    sanitized: list[str] = []
    total_len = 0

    for t in tags:
        clean = " ".join(str(t).strip().split()).strip(",")
        lowered = clean.lower()
        if not clean or lowered in seen:
            continue
        # Check comma-separated length constraint
        cost = len(clean) + (1 if sanitized else 0)
        if total_len + cost > max_total_chars:
            continue
        seen.add(lowered)
        sanitized.append(clean)
        total_len += cost

    return sanitized


def generate_fallback_titles(
    streamer: str = "Streamer",
    category: str = "",
    analytical_title: str = "",
    key_quotes: list[str] | None = None,
    allow_emoji: bool = True,
) -> list[dict[str, str]]:
    """Standalone helper to generate 3 viral titles based on category and streamer name."""
    clean_streamer = (streamer or "Streamer").lstrip("#").strip()
    cat_lower = (category or "").lower()

    if "fail" in cat_lower:
        opt1 = f"Instant regret in 3... 2... 1... 💀 {SHORTS_TAG}"
        opt2 = f"{clean_streamer} had the WORST timing ever 😂 {SHORTS_TAG}"
        opt3 = f"How did this even happen? 😭 {SHORTS_TAG}"
    elif "hype" in cat_lower or "gameplay" in cat_lower or "clutch" in cat_lower:
        opt1 = f"Chat went ABSOLUTELY CRAZY here 🤯 {SHORTS_TAG}"
        opt2 = f"The cleanest moment on {clean_streamer}'s stream 🔥 {SHORTS_TAG}"
        opt3 = f"Wait for the reaction... ⚡ {SHORTS_TAG}"
    elif "funny" in cat_lower:
        opt1 = f"He did NOT expect this to happen 💀 {SHORTS_TAG}"
        opt2 = f"This moment broke the entire stream 😂 {SHORTS_TAG}"
        opt3 = f"{clean_streamer} couldn't even keep a straight face 😭 {SHORTS_TAG}"
    else:
        opt1 = f"Wait for it... 🤯 {SHORTS_TAG}"
        opt2 = f"Nobody saw this coming on stream 💀 {SHORTS_TAG}"
        opt3 = f"{clean_streamer} was completely speechless {SHORTS_TAG}"

    return [
        {
            "title": sanitize_viral_title(opt1, allow_emoji),
            "style": "curiosity_gap",
            "predicted_impact": "High viral curiosity and hook",
        },
        {
            "title": sanitize_viral_title(opt2, allow_emoji),
            "style": "reaction_bait",
            "predicted_impact": "Strong emotional and viewer reaction bait",
        },
        {
            "title": sanitize_viral_title(opt3, allow_emoji),
            "style": "emotional_hook",
            "predicted_impact": "High shareability and viewer retention",
        },
    ]


def generate_fallback_description(
    streamer: str = "Streamer",
    category: str = "",
    analytical_title: str = "",
    reaction_summary: str = "",
    key_quotes: list[str] | None = None,
    game: str = "GTA 6",
) -> str:
    """Standalone helper to generate an optimized description under 500 characters."""
    clean_streamer = (streamer or "Streamer").lstrip("#").strip()
    clean_summary = (reaction_summary or "").strip()
    raw_title = (analytical_title or "Live stream moment").strip()

    hook = clean_summary if clean_summary else f"Unbelievable moment from {clean_streamer}'s stream!"
    if not hook.endswith((".", "!")):
        hook += "!"

    lines = [
        hook,
        f"{raw_title} caught live on stream.",
        f"🎮 Streamer: @{clean_streamer}",
        f"🎮 Game: {game}",
        "Follow for more daily highlights!",
        f"#Shorts #{clean_streamer} #GTA6 #Gaming #TwitchHighlights",
    ]
    desc = "\n".join(lines)
    return sanitize_viral_description(desc, max_chars=500)


def generate_fallback_tags(
    streamer: str = "Streamer",
    game: str = "GTA 6",
    category: str = "",
) -> list[str]:
    """Standalone helper to generate high-value search tags under 480 characters."""
    clean_streamer = (streamer or "Streamer").lstrip("#").strip()
    clean_category = (category or "highlight").replace("_", " ")

    candidates = [
        "Shorts",
        "YouTube Shorts",
        clean_streamer,
        f"{clean_streamer} clips",
        f"{clean_streamer} funny moments",
        game,
        "GTA VI",
        "Grand Theft Auto 6",
        clean_category,
        "stream highlights",
        "twitch fails",
        "gaming",
        "twitch clips",
        "funny moments",
        "viral",
    ]
    return sanitize_viral_tags(candidates, max_total_chars=480)


class ViralMetadataGenerator:
    """Generates viral-optimized YouTube Shorts metadata using Gemini or rule-based fallback."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.5-flash",
        enabled: bool = True,
        allow_emoji: bool = True,
        language: str = "en",
        client: Any | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.api_key = api_key or ""
        self.model = model
        self.enabled = bool(enabled)
        self.allow_emoji = bool(allow_emoji)
        self.language = language
        self.timeout_seconds = float(timeout_seconds)

        self._client = client
        if self._client is None and self.api_key and self.enabled:
            try:
                self._client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                log.warning("Could not initialize Gemini Client for viral metadata: %s", exc)
                self._client = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> ViralMetadataGenerator:
        """Construct generator from global configuration settings."""
        cfg = settings or load_settings()
        return cls(
            api_key=cfg.gemini_api_key,
            model=getattr(cfg, "viral_metadata_model", "gemini-2.5-flash"),
            enabled=getattr(cfg, "viral_metadata_enabled", True),
            allow_emoji=getattr(cfg, "viral_title_emoji", True),
            language=getattr(cfg, "viral_title_language", "en"),
            timeout_seconds=getattr(cfg, "gemini_timeout_seconds", 20.0),
        )

    def generate_metadata(
        self,
        sidecar_data: dict[str, Any],
    ) -> ViralMetadata:
        """Generate 3 viral titles, description, and tags for a clip sidecar."""
        if not self.enabled or not self.api_key:
            log.info("Viral metadata disabled or no API key present; using rule-based fallback.")
            return self.generate_fallback_metadata(sidecar_data)

        try:
            return self._generate_with_gemini(sidecar_data)
        except Exception as exc:
            log.warning("Gemini viral metadata generation failed (%s); using fallback.", exc)
            return self.generate_fallback_metadata(sidecar_data)

    def _generate_with_gemini(self, sidecar_data: dict[str, Any]) -> ViralMetadata:
        """Call Gemini API with structured JSON output for viral metadata."""
        if self._client is None:
            self._client = genai.Client(api_key=self.api_key)

        prompt = self._build_prompt(sidecar_data)

        gen_config = types.GenerateContentConfig(
            system_instruction=VIRAL_METADATA_SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ViralMetadataPayload,
            temperature=0.8,
        )

        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=gen_config,
        )

        raw_text = response.text or ""
        parsed = json.loads(raw_text)

        # Parse & sanitize title options
        raw_titles = parsed.get("titles") or []
        sanitized_titles: list[dict[str, str]] = []
        for opt in raw_titles[:3]:
            raw_t = opt.get("title", "")
            clean_t = sanitize_viral_title(raw_t, allow_emoji=self.allow_emoji)
            sanitized_titles.append({
                "title": clean_t,
                "style": opt.get("style", "viral_hook"),
                "predicted_impact": opt.get("predicted_impact", "High engagement potential"),
            })

        # Ensure we always have exactly 3 options
        if len(sanitized_titles) < 3:
            fallback = self.generate_fallback_metadata(sidecar_data)
            for fb_opt in fallback.titles:
                if len(sanitized_titles) >= 3:
                    break
                if fb_opt["title"] not in [t["title"] for t in sanitized_titles]:
                    sanitized_titles.append(fb_opt)

        raw_desc = parsed.get("description", "")
        clean_desc = sanitize_viral_description(raw_desc, max_chars=500)
        if not clean_desc or len(clean_desc) < 30:
            clean_desc = self.generate_fallback_description(sidecar_data)

        raw_tags = parsed.get("tags") or []
        clean_tags = sanitize_viral_tags(raw_tags, max_total_chars=480)
        if not clean_tags or len(clean_tags) < 5:
            clean_tags = self.generate_fallback_tags(sidecar_data)

        return ViralMetadata(
            titles=sanitized_titles,
            description=clean_desc,
            tags=clean_tags,
            selected_title=sanitized_titles[0]["title"],
            generated_at_iso=datetime.now(timezone.utc).isoformat(),
            model_used=self.model,
            is_fallback=False,
        )

    def _build_prompt(self, sidecar_data: dict[str, Any]) -> str:
        """Construct the prompt sent to Gemini from sidecar details."""
        analysis = sidecar_data.get("analysis") or {}
        streamer = (sidecar_data.get("streamer_name") or "Streamer").lstrip("#").strip()
        category = analysis.get("category") or "general_highlight"
        raw_title = analysis.get("title") or "Live Stream Highlight"
        reaction_summary = analysis.get("reaction_summary") or "Exciting stream moment"
        key_quotes = analysis.get("key_quotes") or []
        duration = sidecar_data.get("processing", {}).get("final_duration_seconds") or 55.0

        quotes_str = ", ".join(f'"{q}"' for q in key_quotes[:5]) if key_quotes else "None"

        return f"""Analyze this GTA 6 Twitch highlight and generate viral YouTube Shorts metadata:

Streamer: {streamer}
Game: Grand Theft Auto VI (GTA 6)
Category: {category}
Original Analytical Title: {raw_title}
Viewer Reaction Summary: {reaction_summary}
Top Chat Quotes: {quotes_str}
Clip Duration: {duration:.1f}s
Allow Emojis in Titles: {"YES" if self.allow_emoji else "NO"}
Language: {self.language}

Generate 3 viral titles (ending with #Shorts), an optimized description (<500 chars), and SEO tags (<480 chars total)."""

    def generate_fallback_metadata(self, sidecar_data: dict[str, Any]) -> ViralMetadata:
        """Deterministic rule-based viral metadata generation (offline / fallback)."""
        titles = self.generate_fallback_titles(sidecar_data)
        description = self.generate_fallback_description(sidecar_data)
        tags = self.generate_fallback_tags(sidecar_data)

        return ViralMetadata(
            titles=titles,
            description=description,
            tags=tags,
            selected_title=titles[0]["title"],
            generated_at_iso=datetime.now(timezone.utc).isoformat(),
            model_used=None,
            is_fallback=True,
        )

    def generate_fallback_titles(self, sidecar_data: dict[str, Any]) -> list[dict[str, str]]:
        """Generate 3 viral titles based on category and streamer name."""
        analysis = sidecar_data.get("analysis") or {}
        streamer = sidecar_data.get("streamer_name") or "Streamer"
        category = analysis.get("category") or ""
        raw_title = analysis.get("title") or ""
        key_quotes = analysis.get("key_quotes") or []
        return generate_fallback_titles(
            streamer=streamer,
            category=category,
            analytical_title=raw_title,
            key_quotes=key_quotes,
            allow_emoji=self.allow_emoji,
        )

    def generate_fallback_description(self, sidecar_data: dict[str, Any]) -> str:
        """Generate optimized description under 500 chars with required structure."""
        analysis = sidecar_data.get("analysis") or {}
        streamer = sidecar_data.get("streamer_name") or "Streamer"
        category = analysis.get("category") or ""
        raw_title = analysis.get("title") or ""
        reaction_summary = analysis.get("reaction_summary") or ""
        key_quotes = analysis.get("key_quotes") or []
        return generate_fallback_description(
            streamer=streamer,
            category=category,
            analytical_title=raw_title,
            reaction_summary=reaction_summary,
            key_quotes=key_quotes,
        )

    def generate_fallback_tags(self, sidecar_data: dict[str, Any]) -> list[str]:
        """Generate high-value search tags under 480 characters."""
        analysis = sidecar_data.get("analysis") or {}
        streamer = sidecar_data.get("streamer_name") or "Streamer"
        category = analysis.get("category") or ""
        return generate_fallback_tags(
            streamer=streamer,
            category=category,
        )
