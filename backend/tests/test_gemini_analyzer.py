"""Deterministic offline async unit tests for GeminiHighlightAnalyzer."""

from __future__ import annotations

import asyncio
import json
import types as pytypes
import unittest

from google.genai import errors as genai_errors

from app.analysis.gemini_client import (
    GeminiAnalysisError,
    GeminiAuthError,
    GeminiHighlightAnalyzer,
    GeminiRateLimitError,
    apply_clip_policy,
)
from app.analysis.models import (
    AnalysisConfig,
    AnalysisStatus,
    GeminiHighlightVerdict,
    HighlightCategory,
)
from app.detection.highlight_event import ChatMessage, DetectionSignals, HighlightEvent


def _sample_event() -> HighlightEvent:
    return HighlightEvent(
        stream_id="test_ch",
        streamer_name="test_ch",
        event_id="ev-12345678",
        start_timestamp=1000.0,
        end_timestamp=1030.0,
        trigger_timestamp=1010.0,
        messages=[
            ChatMessage("a", "wait look at the bridge", 1005.0),
            ChatMessage("b", "NO WAY HE LANDED THAT", 1010.2, emote_count=2),
            ChatMessage("c", "CLIP IT BRO", 1011.0),
        ],
        message_count=3,
        unique_chatters=3,
        emote_count=2,
        activity_score=12.0,
        detection_signals=DetectionSignals(
            messages_per_sec=4.0,
            baseline_rate=1.0,
            rate_ratio=4.0,
            unique_chatters=3,
            triggered=["rate_spike"],
        ),
    )


def _sample_verdict(**overrides) -> GeminiHighlightVerdict:
    base = dict(
        is_highlight=True,
        should_clip=True,
        highlight_score=86,
        confidence=0.91,
        category=HighlightCategory.IMPRESSIVE_GAMEPLAY,
        is_spam_or_false_positive=False,
        title="Chat erupts after apparent bridge jump landing",
        reasoning="Viewers reacted with disbelief and asked to clip what sounds like an unlikely stunt landing.",
        reaction_summary="Shock and hype over stunt landing",
        key_quotes=["NO WAY HE LANDED THAT", "CLIP IT BRO"],
    )
    base.update(overrides)
    return GeminiHighlightVerdict(**base)


class _FakeModels:
    def __init__(self, behaviors: list) -> None:
        self._behaviors = list(behaviors)
        self.calls: list[dict] = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        action = self._behaviors.pop(0)
        if callable(action):
            return await action()
        if isinstance(action, Exception):
            raise action
        return action


class _FakeClient:
    def __init__(self, behaviors: list) -> None:
        self.models = _FakeModels(behaviors)
        self.closed = False
        self.aio = pytypes.SimpleNamespace(models=self.models, aclose=self._aclose)

    async def _aclose(self) -> None:
        self.closed = True


def _fake_response(
    *,
    parsed=None,
    text: str | None = None,
    prompt_tokens: int = 350,
    output_tokens: int = 120,
    thoughts_tokens: int = 0,
):
    usage = pytypes.SimpleNamespace(
        prompt_token_count=prompt_tokens,
        candidates_token_count=output_tokens,
        thoughts_token_count=thoughts_tokens,
        total_token_count=prompt_tokens + output_tokens + thoughts_tokens,
    )
    return pytypes.SimpleNamespace(parsed=parsed, text=text, usage_metadata=usage)


async def _no_sleep(_: float) -> None:
    return None


class TestGeminiAnalyzer(unittest.IsolatedAsyncioTestCase):
    def test_default_model_is_gemini_3_1_flash_lite(self):
        cfg = AnalysisConfig()
        self.assertEqual(cfg.model, "gemini-3.1-flash-lite")

    def test_clip_policy_guardrails(self):
        cfg = AnalysisConfig(min_clip_score=70, min_confidence=0.65)

        # Strong highlight passes.
        good = apply_clip_policy(_sample_verdict(highlight_score=80, confidence=0.8), cfg)
        self.assertTrue(good.is_highlight)
        self.assertTrue(good.should_clip)

        # Below score threshold -> should_clip forced False.
        low_score = apply_clip_policy(_sample_verdict(highlight_score=60, confidence=0.9), cfg)
        self.assertTrue(low_score.is_highlight)
        self.assertFalse(low_score.should_clip)

        # Below confidence threshold -> should_clip forced False.
        low_conf = apply_clip_policy(_sample_verdict(highlight_score=85, confidence=0.4), cfg)
        self.assertFalse(low_conf.should_clip)

        # Spam category -> forces is_highlight=False, should_clip=False, is_spam=True.
        spam = apply_clip_policy(
            _sample_verdict(
                is_highlight=True,
                should_clip=True,
                category=HighlightCategory.SPAM_OR_FALSE_POSITIVE,
                is_spam_or_false_positive=False,
            ),
            cfg,
        )
        self.assertFalse(spam.is_highlight)
        self.assertFalse(spam.should_clip)
        self.assertTrue(spam.is_spam_or_false_positive)

    async def test_analyze_success_with_parsed_schema_and_usage(self):
        verdict = _sample_verdict()
        client = _FakeClient([_fake_response(parsed=verdict, prompt_tokens=400, output_tokens=150)])
        cfg = AnalysisConfig()
        analyzer = GeminiHighlightAnalyzer("secret-key-xyz", cfg, client=client, sleep_fn=_no_sleep)

        result = await analyzer.analyze(_sample_event())

        self.assertEqual(result.status, AnalysisStatus.ANALYZED)
        self.assertEqual(result.model_used, "gemini-3.1-flash-lite")
        self.assertIsNotNone(result.verdict)
        self.assertTrue(result.verdict.should_clip)
        self.assertEqual(result.token_usage.prompt_tokens, 400)
        self.assertEqual(result.token_usage.output_tokens, 150)
        self.assertEqual(result.token_usage.total_tokens, 550)
        # Verify SDK call config used structured JSON output.
        call = client.models.calls[0]
        self.assertEqual(call["model"], "gemini-3.1-flash-lite")
        self.assertEqual(call["config"].response_mime_type, "application/json")
        self.assertIs(call["config"].response_schema, GeminiHighlightVerdict)
        # Verify JSON round-trip includes required persisted fields.
        payload = json.loads(result.to_json())
        self.assertEqual(payload["event_id"], "ev-12345678")
        self.assertEqual(payload["category"], "impressive_gameplay")
        self.assertEqual(payload["highlight_score"], 86)
        self.assertIn("analyzed_at_iso", payload)

    async def test_analyze_falls_back_to_raw_json_text(self):
        verdict = _sample_verdict()
        client = _FakeClient([_fake_response(parsed=None, text=verdict.model_dump_json())])
        analyzer = GeminiHighlightAnalyzer(
            "secret-key-xyz", AnalysisConfig(), client=client, sleep_fn=_no_sleep
        )
        result = await analyzer.analyze(_sample_event())
        self.assertEqual(result.verdict.title, verdict.title)

    async def test_transient_server_error_retries_and_succeeds(self):
        err_503 = genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
        client = _FakeClient([err_503, _fake_response(parsed=_sample_verdict())])
        cfg = AnalysisConfig(max_retries=2)
        analyzer = GeminiHighlightAnalyzer(
            "secret-key-xyz", cfg, client=client, sleep_fn=_no_sleep, base_backoff_seconds=0.0
        )
        result = await analyzer.analyze(_sample_event())
        self.assertEqual(result.status, AnalysisStatus.ANALYZED)
        self.assertEqual(len(client.models.calls), 2)

    async def test_timeout_retries_and_succeeds(self):
        async def _slow():
            await asyncio.sleep(0.2)
            return _fake_response(parsed=_sample_verdict())

        client = _FakeClient([_slow, _fake_response(parsed=_sample_verdict())])
        cfg = AnalysisConfig(timeout_seconds=0.02, max_retries=1)
        analyzer = GeminiHighlightAnalyzer(
            "secret-key-xyz", cfg, client=client, sleep_fn=_no_sleep, base_backoff_seconds=0.0
        )
        result = await analyzer.analyze(_sample_event())
        self.assertEqual(result.status, AnalysisStatus.ANALYZED)
        self.assertEqual(len(client.models.calls), 2)

    async def test_persistent_429_raises_rate_limit_error(self):
        err_429 = genai_errors.ClientError(429, {"error": {"message": "quota exceeded"}})
        client = _FakeClient([err_429, err_429])
        cfg = AnalysisConfig(max_retries=1)
        analyzer = GeminiHighlightAnalyzer(
            "secret-key-xyz", cfg, client=client, sleep_fn=_no_sleep, base_backoff_seconds=0.0
        )
        with self.assertRaises(GeminiRateLimitError):
            await analyzer.analyze(_sample_event())
        self.assertEqual(len(client.models.calls), 2)

    async def test_auth_error_401_fails_fast_without_retries(self):
        err_401 = genai_errors.ClientError(401, {"error": {"message": "bad key"}})
        client = _FakeClient([err_401])
        cfg = AnalysisConfig(max_retries=2)
        analyzer = GeminiHighlightAnalyzer(
            "secret-key-xyz", cfg, client=client, sleep_fn=_no_sleep
        )
        with self.assertRaises(GeminiAuthError):
            await analyzer.analyze(_sample_event())
        self.assertEqual(len(client.models.calls), 1)

    async def test_api_key_redacted_in_error_messages(self):
        secret = "AIzaSySUPER_SECRET_TEST_KEY_999"
        err_400 = genai_errors.ClientError(400, {"error": {"message": f"invalid request with {secret}"}})
        client = _FakeClient([err_400])
        analyzer = GeminiHighlightAnalyzer(secret, AnalysisConfig(), client=client, sleep_fn=_no_sleep)
        with self.assertRaises(GeminiAnalysisError) as ctx:
            await analyzer.analyze(_sample_event())
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("<redacted>", str(ctx.exception))

    async def test_malformed_json_retries_then_raises(self):
        client = _FakeClient([_fake_response(parsed=None, text="{not valid json"), _fake_response(parsed=None, text="")])
        cfg = AnalysisConfig(max_retries=1)
        analyzer = GeminiHighlightAnalyzer(
            "secret-key-xyz", cfg, client=client, sleep_fn=_no_sleep, base_backoff_seconds=0.0
        )
        with self.assertRaises(GeminiAnalysisError):
            await analyzer.analyze(_sample_event())
        self.assertEqual(len(client.models.calls), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
