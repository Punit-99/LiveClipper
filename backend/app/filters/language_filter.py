"""Stream language detection and filtering (Stage 10).

Detects stream language using:
1. API metadata (Twitch Helix language tag / YouTube broadcast language).
2. Chat script and vocabulary sampling (Devanagari script + Hinglish lexicon vs English).
3. Filtering decision: accepts streams matching TARGET_LANGUAGES (default: en, hi),
   accepts undetermined streams by default to prevent false negatives, and skips non-target streams.
"""

from __future__ import annotations

import re
from typing import Any

from app.logging_setup import get_logger

log = get_logger(__name__)

DEFAULT_TARGET_LANGUAGES = ("en", "hi")

# Devanagari script unicode range: U+0900 to U+097F
DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
WORD_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Common Hinglish / Romanized Hindi tokens
HINGLISH_WORDS = {
    "bhai", "kya", "hai", "yaar", "accha", "nahi", "karo", "khel", "op",
    "haan", "kahan", "kaise", "kripya", "namaste", "shukriya", "badhiya",
    "are", "arey", "arrey", "bhaiya", "didi", "ab", "sab", "hum", "tum",
    "aap", "ye", "woh", "mera", "meri", "tera", "teri", "apna", "apni",
    "karna", "dekh", "dekho", "suno", "bol", "bolo", "ruk", "ruko",
    "chalo", "chal", "khatam", "shuru", "maja", "maza", "pata", "matlab",
    "kuch", "sahi", "galat", "aao", "jao", "batao", "sun", "dekha",
    "mar", "gaya", "gayi", "raha", "rahi", "theek", "thik", "waah",
}

# Common English tokens
ENGLISH_WORDS = {
    "the", "is", "are", "and", "lol", "lmao", "gg", "bro", "omg", "what",
    "why", "how", "who", "this", "that", "with", "for", "you", "your",
    "play", "game", "good", "bad", "nice", "wow", "lets", "go", "dude",
    "man", "yes", "no", "just", "like", "can", "will", "got", "get",
    "cool", "great", "win", "lose", "clutch", "headshot", "kill", "stream",
}


def analyze_chat_sample(messages: list[str]) -> tuple[str, float]:
    """Analyze a list of sample chat messages and detect language.

    Returns (language_code, confidence).
    """
    if not messages:
        return "und", 0.0

    total_messages = len(messages)
    devanagari_count = 0
    hinglish_word_count = 0
    english_word_count = 0
    total_words = 0

    for msg in messages:
        if DEVANAGARI_RE.search(msg):
            devanagari_count += 1

        words = WORD_TOKEN_RE.findall(msg.lower())
        for w in words:
            total_words += 1
            if w in HINGLISH_WORDS:
                hinglish_word_count += 1
            elif w in ENGLISH_WORDS:
                english_word_count += 1

    # If significant Devanagari script is present (>5% of messages), it is Hindi
    devanagari_ratio = devanagari_count / total_messages
    if devanagari_ratio >= 0.05:
        confidence = min(1.0, 0.7 + devanagari_ratio * 0.3)
        return "hi", round(confidence, 2)

    if total_words > 0:
        hinglish_ratio = hinglish_word_count / total_words
        english_ratio = english_word_count / total_words

        # If >15% of words are Hinglish markers
        if hinglish_ratio >= 0.15:
            confidence = min(1.0, 0.6 + hinglish_ratio * 2.0)
            return "hi", round(confidence, 2)

        if english_ratio >= 0.20 and hinglish_ratio < 0.10:
            confidence = min(1.0, 0.6 + english_ratio)
            return "en", round(confidence, 2)

    return "und", 0.0


class LanguageFilter:
    """Evaluates whether live streams match configured target languages."""

    def __init__(
        self,
        target_languages: tuple[str, ...] | list[str] | str | None = None,
        enabled: bool = True,
    ) -> None:
        if isinstance(target_languages, str):
            langs = [
                token.strip().lower()
                for token in target_languages.replace(",", " ").split()
                if token.strip()
            ]
        elif target_languages:
            langs = [str(x).strip().lower() for x in target_languages if str(x).strip()]
        else:
            langs = list(DEFAULT_TARGET_LANGUAGES)

        self.target_languages = tuple(langs)
        self.enabled = bool(enabled)

    def detect_stream_language(
        self,
        metadata_language: str | None = None,
        chat_messages: list[str] | None = None,
    ) -> tuple[str, float]:
        """Combine API metadata and chat script analysis to determine language."""
        chat_lang, chat_conf = analyze_chat_sample(chat_messages or [])

        # Normalize metadata language tag (e.g. 'en-US' -> 'en', 'hi-IN' -> 'hi')
        meta_clean = None
        if metadata_language:
            meta_clean = metadata_language.strip().lower().split("-")[0].split("_")[0]

        # Rule c: If metadata says 'hi' OR chat has >15% Hindi/Hinglish -> 'hi'
        if meta_clean == "hi":
            return "hi", 0.95 if chat_lang == "hi" else 0.85
        if chat_lang == "hi":
            return "hi", chat_conf

        # Rule c: If metadata says 'en' OR chat is predominantly English -> 'en'
        if meta_clean == "en":
            return "en", 0.95 if chat_lang == "en" else 0.85
        if chat_lang == "en":
            return "en", chat_conf

        # If metadata specifies a different known language (e.g. 'es', 'pt', 'ru', 'fr', 'de')
        if meta_clean and meta_clean not in ("other", "und", ""):
            return meta_clean, 0.80

        # If undetermined from both
        return chat_lang, chat_conf

    def is_stream_accepted(
        self,
        metadata_language: str | None = None,
        chat_messages: list[str] | None = None,
        channel_name: str | None = None,
    ) -> tuple[bool, str, str]:
        """Evaluate if stream matches target languages.

        Returns (accepted, detected_language, reason).
        """
        if not self.enabled:
            return True, "any", "language_filter_disabled"

        lang, conf = self.detect_stream_language(
            metadata_language=metadata_language,
            chat_messages=chat_messages,
        )

        # Fallback rule: If language cannot be determined with confidence, accept by default
        if lang == "und" or conf < 0.3:
            log.info(
                "[%s] Language undetermined (conf=%.2f); accepted by default to avoid false negative.",
                channel_name or "stream",
                conf,
            )
            return True, "und", "accepted_undetermined_default"

        if lang in self.target_languages:
            log.info(
                "[%s] Stream accepted: language '%s' matches target %s (confidence=%.2f).",
                channel_name or "stream",
                lang,
                self.target_languages,
                conf,
            )
            return True, lang, f"accepted_target_language_{lang}"

        # Otherwise reject
        log.warning(
            "[%s] Stream skipped: language '%s' not in target %s (confidence=%.2f).",
            channel_name or "stream",
            lang,
            self.target_languages,
            conf,
        )
        return False, lang, f"skipped_non_target_language_{lang}"
