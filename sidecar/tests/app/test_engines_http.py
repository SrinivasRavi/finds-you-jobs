"""Covers: direct-API BYOK engines + verify (ROADMAP §7 items 3/9, FR-SET-06).

The HTTP layer is faked (no network): a scripted `FakeTransport` returns canned
`HttpResponse`s and records every request, so we assert both the request shape
(URL, auth header, body) and the response parse (text + usage). Verify is
checked per provider, including that a 401 surfaces the provider's verbatim body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from sidecar.app.registry.engine_config import verify_provider
from sidecar.app.registry.engines_http import (
    AnthropicEngine,
    HttpResponse,
    OpenAICompatibleEngine,
    price_usd,
    verify_anthropic,
    verify_openai_compatible,
)
from sidecar.modules._shared.claude_engine import EngineError


@dataclass
class _Recorded:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


@dataclass
class FakeTransport:
    """Returns queued responses in order; records the requests it saw."""

    responses: list[HttpResponse]
    calls: list[_Recorded] = field(default_factory=list)

    def request(self, method, url, *, headers, body=None, timeout_s=60):  # noqa: ANN001, ANN201
        self.calls.append(_Recorded(method, url, headers, body))
        return self.responses.pop(0)


def _resp(status: int, payload: object) -> HttpResponse:
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(status=status, body=data)


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------


def test_anthropic_complete_parses_text_and_usage():
    transport = FakeTransport(
        [
            _resp(
                200,
                {
                    "model": "claude-opus-4-8",
                    "content": [{"type": "text", "text": "Hello world"}],
                    "usage": {"input_tokens": 12, "output_tokens": 5},
                },
            )
        ]
    )
    engine = AnthropicEngine(api_key="sk-ant-xyz", model="claude-opus-4-8", transport=transport)
    text, usage = engine.complete("be brief", "hi")

    assert text == "Hello world"
    assert usage.tokens_in == 12
    assert usage.tokens_out == 5
    assert usage.model == "claude-opus-4-8"
    # usd is a real lookup for a known model (15/75 per Mtok).
    assert usage.usd == pytest.approx((12 * 15.0 + 5 * 75.0) / 1_000_000, rel=1e-6)

    call = transport.calls[0]
    assert call.method == "POST"
    assert call.url.endswith("/v1/messages")
    assert call.headers["x-api-key"] == "sk-ant-xyz"
    assert "anthropic-version" in call.headers
    assert call.body is not None
    sent = json.loads(call.body)
    assert sent["system"] == "be brief"
    assert sent["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_error_surfaces_verbatim():
    transport = FakeTransport([_resp(401, {"error": {"message": "invalid x-api-key"}})])
    engine = AnthropicEngine(api_key="bad", model="claude-opus-4-8", transport=transport)
    with pytest.raises(EngineError) as ei:
        engine.complete("", "hi")
    assert "401" in str(ei.value)
    assert "invalid x-api-key" in str(ei.value)


def test_anthropic_empty_content_raises():
    transport = FakeTransport([_resp(200, {"content": [], "usage": {}})])
    engine = AnthropicEngine(api_key="k", model="claude-opus-4-8", transport=transport)
    with pytest.raises(EngineError):
        engine.complete("", "hi")


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenRouter / OpenAI / local)
# ---------------------------------------------------------------------------


def test_openai_compatible_complete_parses_and_sends_bearer():
    transport = FakeTransport(
        [
            _resp(
                200,
                {
                    "model": "gpt-5",
                    "choices": [{"message": {"role": "assistant", "content": "Yo"}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 2},
                },
            )
        ]
    )
    engine = OpenAICompatibleEngine(
        base_url="https://openrouter.ai/api/v1",
        model="gpt-5",
        api_key="sk-or-123",
        transport=transport,
    )
    text, usage = engine.complete("sys", "user")

    assert text == "Yo"
    assert usage.tokens_in == 8
    assert usage.tokens_out == 2
    call = transport.calls[0]
    assert call.url == "https://openrouter.ai/api/v1/chat/completions"
    assert call.headers["authorization"] == "Bearer sk-or-123"
    assert call.body is not None
    sent = json.loads(call.body)
    assert sent["messages"][0] == {"role": "system", "content": "sys"}


def test_openai_compatible_no_key_omits_auth_header():
    # A local server (Ollama/LM Studio) needs no key → no auth header.
    transport = FakeTransport(
        [_resp(200, {"choices": [{"message": {"content": "ok"}}], "usage": {}})]
    )
    engine = OpenAICompatibleEngine(
        base_url="http://localhost:11434/v1", model="llama3.1", transport=transport
    )
    engine.complete("", "hi")
    assert "authorization" not in transport.calls[0].headers


def test_openrouter_requests_and_uses_exact_reported_cost():
    # openrouter/auto routes to a different concrete model per call, so a
    # static pricing table can never keep up — OpenRouter's own `usage.cost`
    # (requested via `usage.include=true`) is the only way this stays honest.
    transport = FakeTransport(
        [
            _resp(
                200,
                {
                    # A model with no entry in the static pricing map at all —
                    # if the static table were consulted, usd would come back None.
                    "model": "openai/gpt-5.6-sol",
                    "choices": [{"message": {"content": "Tailored resume text"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.0034},
                },
            )
        ]
    )
    engine = OpenAICompatibleEngine(
        base_url="https://openrouter.ai/api/v1",
        model="openrouter/auto",
        api_key="sk-or-123",
        transport=transport,
    )
    text, usage = engine.complete("sys", "user")

    assert text == "Tailored resume text"
    assert usage.usd == pytest.approx(0.0034)
    assert usage.model == "openai/gpt-5.6-sol"
    body = transport.calls[0].body
    assert body is not None
    sent = json.loads(body)
    assert sent["usage"] == {"include": True}


def test_openrouter_falls_back_to_static_map_when_cost_absent():
    # Some OpenRouter responses (or a request that didn't ask for usage.cost)
    # omit it — fall back to the static map for a model that IS in it.
    transport = FakeTransport(
        [
            _resp(
                200,
                {
                    "model": "anthropic/claude-opus-4-8",
                    "choices": [{"message": {"content": "hi"}}],
                    "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
                },
            )
        ]
    )
    engine = OpenAICompatibleEngine(
        base_url="https://openrouter.ai/api/v1",
        model="anthropic/claude-opus-4-8",
        transport=transport,
    )
    _, usage = engine.complete("", "hi")
    assert usage.usd == pytest.approx(15.0)


def test_non_openrouter_endpoint_does_not_request_usage_cost():
    # A local Ollama/LM Studio server may not tolerate an unrecognized
    # `usage.include` field — only send it to OpenRouter.
    transport = FakeTransport(
        [_resp(200, {"choices": [{"message": {"content": "ok"}}], "usage": {}})]
    )
    engine = OpenAICompatibleEngine(
        base_url="http://localhost:11434/v1", model="llama3.1", transport=transport
    )
    engine.complete("", "hi")
    body = transport.calls[0].body
    assert body is not None
    sent = json.loads(body)
    assert "usage" not in sent


def test_openai_compatible_empty_content_reports_finish_reason_not_bare_message():
    # A reasoning model can spend its whole token budget on hidden reasoning
    # and return empty visible content — the error should say so, not just
    # "empty content" with no way to tell why.
    transport = FakeTransport(
        [
            _resp(
                200,
                {
                    "model": "openai/o-reasoner",
                    "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 8192},
                },
            )
        ]
    )
    engine = OpenAICompatibleEngine(
        base_url="https://api.openai.com/v1", model="o-reasoner", transport=transport
    )
    with pytest.raises(EngineError, match="reasoning"):
        engine.complete("", "hi")


def test_openai_compatible_empty_content_surfaces_refusal():
    transport = FakeTransport(
        [
            _resp(
                200,
                {
                    "choices": [
                        {"message": {"content": "", "refusal": "cannot help with that"}}
                    ],
                },
            )
        ]
    )
    engine = OpenAICompatibleEngine(
        base_url="https://api.openai.com/v1", model="gpt-5", transport=transport
    )
    with pytest.raises(EngineError, match="cannot help with that"):
        engine.complete("", "hi")


# ---------------------------------------------------------------------------
# Pricing honesty
# ---------------------------------------------------------------------------


def test_price_usd_unknown_model_is_none_not_guessed():
    assert price_usd("some-mystery-model", 1000, 1000) is None
    assert price_usd(None, 1000, 1000) is None
    assert price_usd("claude-opus-4-8", None, 5) is None
    # OpenRouter-prefixed known model resolves via the tail.
    assert price_usd("anthropic/claude-opus-4-8", 1_000_000, 0) == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# Verify per provider
# ---------------------------------------------------------------------------


def test_verify_anthropic_ok_and_401():
    ok = verify_anthropic("k", transport=FakeTransport([_resp(200, {"data": []})]))
    assert ok.ok is True

    bad = FakeTransport([_resp(401, {"error": {"message": "invalid x-api-key"}})])
    res = verify_anthropic("bad", transport=bad)
    assert res.ok is False
    assert "invalid x-api-key" in res.detail
    assert bad.calls[0].url.endswith("/v1/models")
    assert bad.calls[0].method == "GET"


def test_verify_openai_compatible_ok_and_error():
    ok = verify_openai_compatible(
        "https://api.openai.com/v1", "k", transport=FakeTransport([_resp(200, {"data": []})])
    )
    assert ok.ok is True
    res = verify_openai_compatible(
        "https://api.openai.com/v1",
        "bad",
        transport=FakeTransport([_resp(401, {"error": {"message": "Incorrect API key"}})]),
    )
    assert res.ok is False
    assert "Incorrect API key" in res.detail


def test_verify_provider_dispatch_and_validation():
    # anthropic via provider id → GET /v1/models
    t = FakeTransport([_resp(200, {"data": []})])
    assert verify_provider("anthropic", api_key="k", base_url=None, transport=t).ok
    assert t.calls[0].url.endswith("/v1/models")

    # openrouter → the OpenAI-compatible models endpoint at its default base
    t2 = FakeTransport([_resp(200, {"data": []})])
    assert verify_provider("openrouter", api_key="k", base_url=None, transport=t2).ok
    assert t2.calls[0].url == "https://openrouter.ai/api/v1/models"

    # local requires a base URL — missing one fails fast, no HTTP call
    empty = FakeTransport([])
    res = verify_provider("local", api_key=None, base_url=None, transport=empty)
    assert res.ok is False
    assert "base URL" in res.detail
    assert empty.calls == []

    # anthropic requires a key — missing fails fast
    res2 = verify_provider("anthropic", api_key=None, base_url=None, transport=FakeTransport([]))
    assert res2.ok is False
    assert "API key" in res2.detail

    # unknown provider
    assert verify_provider("nope", api_key="k", base_url=None).ok is False
