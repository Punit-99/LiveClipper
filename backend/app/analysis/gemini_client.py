"""Async Gemini API client for Stage 4 highlight analysis.

Uses the official `google-genai` SDK (`client.aio.models.generate_content`) with
native structured JSON output (`response_mime_type="application/json"` +
`response_schema=GeminiHighlightVerdict`).

Design highlights:
- Never logs `api_key` and redacts it if an upstream exception ever echoes it.
- Enforces a hard per-request timeout via `asyncio.wait_for`.
- Retries only transient failures (429, 5xx, timeouts, network blips, malformed
  JSON) with exponential backoff + jitter; fails fast on auth (401/403) and
  client errors (400/404).
- Applies deterministic Python post-validation guardrails (`apply_clip_policy`)
  on top of Gemini's structured verdict so clip thresholds stay tunable in `.env`.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Awaitable, Callable, Protocol

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import ValidationError

from app.analysis.models import (
    AnalysisConfig,
    AnalysisStatus,
    AnalyzedHighlight,
    GeminiHighlightVerdict,
    HighlightCategory,
    TokenUsage,
)
from app.analysis.prompt_builder import SYSTEM_INSTRUCTION, build_user_prompt
from app.detection.highlight_event import HighlightEvent
from app.logging_setup import get_logger

log = get_logger(__name__)

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
_AUTH_STATUS_CODES = frozenset({401, 403})


class GeminiAnalysisError(Exception):
    """Base error raised when Gemini analysis fails."""


class GeminiAuthError(GeminiAnalysisError):
    """Non-retryable authentication/authorization error (HTTP 401/403)."""


class GeminiRateLimitError(GeminiAnalysisError):
    """Raised when Gemini rate limits (HTTP 429) persist after retries."""


class HighlightAnalyzer(Protocol):
    """Minimal async interface consumed by `HighlightAnalysisWorker`."""

    async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
        """Analyze one candidate highlight event and return an `AnalyzedHighlight`."""

    async def close(self) -> None:
        """Release underlying HTTP/async resources."""


def apply_clip_policy(
    verdict: GeminiHighlightVerdict, config: AnalysisConfig
) -> GeminiHighlightVerdict:
    """Apply deterministic Python guardrails on top of Gemini's verdict.

    - Spam or false positives are never marked as highlights or recommended for
      clipping.
    - `should_clip` requires `is_highlight=True` AND meeting both configured
      thresholds (`min_clip_score` and `min_confidence`).
    """
    is_spam = (
        verdict.is_spam_or_false_positive
        or verdict.category == HighlightCategory.SPAM_OR_FALSE_POSITIVE
    )
    if is_spam:
        return verdict.model_copy(
            update={
                "is_highlight": False,
                "should_clip": False,
                "is_spam_or_false_positive": True,
            }
        )

    meets_thresholds = (
        verdict.is_highlight
        and verdict.should_clip
        and verdict.highlight_score >= config.min_clip_score
        and verdict.confidence >= config.min_confidence
    )
    if meets_thresholds == verdict.should_clip:
        return verdict
    return verdict.model_copy(update={"should_clip": meets_thresholds})


def _sanitize_error_message(exc: Exception, api_key: str) -> str:
    """Format an exception message while guaranteeing `api_key` is never exposed."""
    msg = f"{type(exc).__name__}: {exc}"
    if api_key:
        msg = msg.replace(api_key, "<redacted>")
    return msg


def _parse_verdict(response: Any) -> GeminiHighlightVerdict:
    """Extract and validate `GeminiHighlightVerdict` from a GenAI response."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, GeminiHighlightVerdict):
        return parsed
    if isinstance(parsed, dict):
        return GeminiHighlightVerdict.model_validate(parsed)

    raw_text = getattr(response, "text", None)
    if not raw_text or not str(raw_text).strip():
        raise ValueError("Gemini returned an empty response body.")
    return GeminiHighlightVerdict.model_validate_json(str(raw_text).strip())


def _extract_token_usage(response: Any) -> TokenUsage | None:
    """Extract token counts from `response.usage_metadata` if present."""
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None
    prompt_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
    output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
    thoughts_tokens = int(getattr(usage, "thoughts_token_count", 0) or 0)
    total_tokens = int(
        getattr(usage, "total_token_count", 0)
        or (prompt_tokens + output_tokens + thoughts_tokens)
    )
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        thoughts_tokens=thoughts_tokens,
        total_tokens=total_tokens,
    )


class GeminiHighlightAnalyzer:
    """Async Gemini client implementing `HighlightAnalyzer` using `google-genai`."""

    def __init__(
        self,
        api_key: str,
        config: AnalysisConfig,
        *,
        client: Any | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        base_backoff_seconds: float = 1.0,
    ) -> None:
        if not api_key and client is None:
            raise ValueError("api_key is required when no pre-built client is provided")
        self._api_key = api_key
        self._config = config
        self._client = client if client is not None else genai.Client(api_key=api_key)
        self._owns_client = client is None
        self._sleep = sleep_fn
        self._base_backoff = max(0.0, base_backoff_seconds)

    async def analyze(self, event: HighlightEvent) -> AnalyzedHighlight:
        """Send one `HighlightEvent` to Gemini and return a validated `AnalyzedHighlight`."""
        prompt = build_user_prompt(event, max_messages=self._config.max_messages)
        gen_config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=GeminiHighlightVerdict,
            temperature=self._config.temperature,
            max_output_tokens=self._config.max_output_tokens,
        )

        started = time.monotonic()
        attempts = self._config.max_retries + 1
        last_exc: Exception | None = None
        last_was_rate_limit = False

        for attempt in range(attempts):
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self._config.model,
                        contents=prompt,
                        config=gen_config,
                    ),
                    timeout=self._config.timeout_seconds,
                )
                raw_verdict = _parse_verdict(response)
                verdict = apply_clip_policy(raw_verdict, self._config)
                latency_ms = (time.monotonic() - started) * 1000.0
                return AnalyzedHighlight.from_event(
                    event,
                    status=AnalysisStatus.ANALYZED,
                    model_used=self._config.model,
                    verdict=verdict,
                    latency_ms=latency_ms,
                    token_usage=_extract_token_usage(response),
                )
            except genai_errors.APIError as exc:
                code = getattr(exc, "code", None)
                safe_msg = _sanitize_error_message(exc, self._api_key)
                if code in _AUTH_STATUS_CODES:
                    raise GeminiAuthError(
                        f"Gemini authentication/permission error (HTTP {code}). "
                        f"Check GEMINI_API_KEY in backend/.env."
                    ) from exc
                if code not in _RETRYABLE_STATUS_CODES:
                    raise GeminiAnalysisError(
                        f"Non-retryable Gemini API error (HTTP {code}): {safe_msg}"
                    ) from exc
                last_exc = exc
                last_was_rate_limit = code == 429
                if attempt + 1 < attempts:
                    delay = self._backoff_delay(attempt)
                    log.warning(
                        "Gemini transient error (HTTP %s) on event %s; retrying in %.2fs (%d/%d).",
                        code,
                        event.event_id[:8],
                        delay,
                        attempt + 1,
                        attempts - 1,
                    )
                    await self._sleep(delay)
            except (asyncio.TimeoutError, httpx.HTTPError, OSError, ValueError, ValidationError) as exc:
                last_exc = exc
                last_was_rate_limit = False
                if attempt + 1 < attempts:
                    delay = self._backoff_delay(attempt)
                    log.warning(
                        "Gemini request/parse error (%s) on event %s; retrying in %.2fs (%d/%d).",
                        type(exc).__name__,
                        event.event_id[:8],
                        delay,
                        attempt + 1,
                        attempts - 1,
                    )
                    await self._sleep(delay)

        safe_final = (
            _sanitize_error_message(last_exc, self._api_key)
            if last_exc is not None
            else "unknown error"
        )
        if last_was_rate_limit:
            raise GeminiRateLimitError(
                f"Gemini rate limit persisted after {attempts} attempt(s): {safe_final}"
            ) from last_exc
        raise GeminiAnalysisError(
            f"Gemini analysis failed after {attempts} attempt(s): {safe_final}"
        ) from last_exc

    async def close(self) -> None:
        """Close the underlying async SDK client."""
        if not self._owns_client:
            return
        aio = getattr(self._client, "aio", None)
        aclose = getattr(aio, "aclose", None)
        if callable(aclose):
            try:
                await aclose()
            except Exception:  # noqa: BLE001  best-effort cleanup on shutdown
                log.debug("Ignoring error while closing Gemini async client.", exc_info=True)

    def _backoff_delay(self, attempt: int) -> float:
        if self._base_backoff == 0.0:
            return 0.0
        base = self._base_backoff * (2 ** attempt)
        jitter = random.uniform(0.0, 0.35 * base)
        return base + jitter
