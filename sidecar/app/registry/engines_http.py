"""Direct-API BYOK engines behind the `Engine` seam (ROADMAP §7 item 9, §A0.3).

Two implementations cover the whole P1 provider menu (US-OB-04 / US-SET-07):

- `AnthropicEngine` — Anthropic Messages API (`x-api-key`, `anthropic-version`).
- `OpenAICompatibleEngine` — bearer-token `/chat/completions`, which covers
  OpenRouter, OpenAI, and any user-run local server (Ollama / LM Studio / vLLM)
  via `base_url`.

Both satisfy `complete(system, user) -> (text, EngineUsage)` — the same contract
the shared `ClaudeCliEngine` implements — so the operation wrappers and the
registry never learn which engine they hold. HTTP is stdlib `urllib` (httpx is
dev-only; mirrors the scraper's `Fetcher`), reached through an injectable
`HttpTransport` seam so tests never touch the network.

Cost (`usd`) is a best-effort lookup in a small pricing map for well-known
models — **`None` when the model is unknown, never a guessed number** (ethos:
cost honesty). Token counts come straight from the provider response.

Verification (`verify_anthropic` / `verify_openai_compatible`) uses the
industry-standard free authenticated models-list call — `GET /v1/models` — so a
Verify click costs zero tokens. Providers without a models endpoint would fall
back to a 1-token completion; both P1 direct APIs expose the list endpoint.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from sidecar.modules._shared.claude_engine import EngineError, EngineUsage

DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
# Verification never needs a large budget; a completion probe (providers without
# a models endpoint) asks for a single token.
_VERIFY_MAX_TOKENS = 1
# Direct-API completions default cap — generous for a tailored resume / cover
# letter; overridable per engine instance.
_DEFAULT_MAX_TOKENS = 8192
_MAX_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# HTTP transport seam (test-injectable, stdlib default)
# ---------------------------------------------------------------------------


@dataclass
class HttpResponse:
    status: int
    body: bytes

    def json(self) -> object:
        return json.loads(self.body.decode("utf-8", errors="replace"))


class HttpTransport(Protocol):
    """The one surface the engines use to reach the network. Tests inject a fake
    with the same shape; production uses `UrllibTransport`."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout_s: int = 60,
    ) -> HttpResponse: ...


class UrllibTransport:
    """stdlib `urllib` transport. Returns non-2xx as an `HttpResponse` (with the
    error body) rather than raising — the engines translate status → typed error
    so the verbatim provider message survives to the UI."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None = None,
        timeout_s: int = 60,
    ) -> HttpResponse:
        if not url.startswith("https://") and not url.startswith("http://"):
            raise EngineError(f"refusing non-http(s) engine URL: {url}")
        req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
                return HttpResponse(status=resp.status, body=resp.read(_MAX_BYTES))
        except urllib.error.HTTPError as e:
            # A 4xx/5xx carries the provider's verbatim JSON error in the body.
            return HttpResponse(status=e.code, body=e.read(_MAX_BYTES) if e.fp else b"")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise EngineError(f"could not reach {url}: {e}") from e


def _default_transport() -> HttpTransport:
    return UrllibTransport()


# ---------------------------------------------------------------------------
# Pricing (best-effort, honest None) — USD per 1M tokens (input, output)
# ---------------------------------------------------------------------------

# Small curated map for common models. Absent → usd is None (never guessed).
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    # Anthropic (public list prices)
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (0.80, 4.0),
    # OpenAI
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def price_usd(model: str | None, tokens_in: int | None, tokens_out: int | None) -> float | None:
    """Best-effort cost from the pricing map. None when the model is unknown or
    token counts are missing — an honest gap, never a fabricated figure."""
    if model is None or tokens_in is None or tokens_out is None:
        return None
    # OpenRouter prefixes models (e.g. "anthropic/claude-opus-4-8"); try the tail.
    rates = _PRICING_PER_MTOK.get(model) or _PRICING_PER_MTOK.get(model.split("/")[-1])
    if rates is None:
        return None
    per_in, per_out = rates
    return round((tokens_in * per_in + tokens_out * per_out) / 1_000_000, 6)


# ---------------------------------------------------------------------------
# Verify result
# ---------------------------------------------------------------------------


@dataclass
class VerifyResult:
    ok: bool
    detail: str


def _verbatim(resp: HttpResponse) -> str:
    """The provider's error body, verbatim (trimmed), for the UI."""
    text = resp.body.decode("utf-8", errors="replace").strip()
    return text[:2000] if text else f"HTTP {resp.status}"


# ---------------------------------------------------------------------------
# Anthropic Messages API
# ---------------------------------------------------------------------------


class AnthropicEngine:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_ANTHROPIC_BASE,
        transport: HttpTransport | None = None,
        timeout_s: int = 120,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.transport = transport or _default_transport()
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        started = time.monotonic()
        resp = self.transport.request(
            "POST",
            f"{self.base_url}/v1/messages",
            headers=self._headers(),
            body=json.dumps(payload).encode(),
            timeout_s=self.timeout_s,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status != 200:
            raise EngineError(f"Anthropic API {resp.status}: {_verbatim(resp)}")
        data = resp.json()
        if not isinstance(data, dict):
            raise EngineError("Anthropic API returned a non-object response")
        blocks = data.get("content") or []
        text = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        )
        if not text.strip():
            raise EngineError("Anthropic API returned empty content")
        usage_raw = data.get("usage") or {}
        tokens_in = usage_raw.get("input_tokens")
        tokens_out = usage_raw.get("output_tokens")
        model = data.get("model", self.model)
        return text, EngineUsage(
            internal_calls=1,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            tokens_cache_write=usage_raw.get("cache_creation_input_tokens"),
            tokens_cache_read=usage_raw.get("cache_read_input_tokens"),
            usd=price_usd(model, tokens_in, tokens_out),
            latency_ms=latency_ms,
            model=model,
        )


def verify_anthropic(
    api_key: str,
    *,
    base_url: str = DEFAULT_ANTHROPIC_BASE,
    transport: HttpTransport | None = None,
) -> VerifyResult:
    """Free authenticated check: `GET /v1/models`. 200 → verified; anything else
    surfaces the provider's verbatim message (a 401 tells the user their key is
    wrong, verbatim)."""
    transport = transport or _default_transport()
    resp = transport.request(
        "GET",
        f"{base_url.rstrip('/')}/v1/models",
        headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
    )
    if resp.status == 200:
        return VerifyResult(ok=True, detail="Anthropic key verified")
    return VerifyResult(ok=False, detail=f"Anthropic API {resp.status}: {_verbatim(resp)}")


# ---------------------------------------------------------------------------
# OpenAI-compatible (OpenRouter / OpenAI / Ollama / LM Studio / vLLM)
# ---------------------------------------------------------------------------


class OpenAICompatibleEngine:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        transport: HttpTransport | None = None,
        timeout_s: int = 120,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.transport = transport or _default_transport()
        self.timeout_s = timeout_s
        self.max_tokens = max_tokens

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        payload = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        started = time.monotonic()
        resp = self.transport.request(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            body=json.dumps(payload).encode(),
            timeout_s=self.timeout_s,
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        if resp.status != 200:
            raise EngineError(f"LLM API {resp.status}: {_verbatim(resp)}")
        data = resp.json()
        if not isinstance(data, dict):
            raise EngineError("LLM API returned a non-object response")
        choices = data.get("choices") or []
        text = ""
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            text = message.get("content") or ""
        if not text.strip():
            raise EngineError("LLM API returned empty content")
        usage_raw = data.get("usage") or {}
        tokens_in = usage_raw.get("prompt_tokens")
        tokens_out = usage_raw.get("completion_tokens")
        model = data.get("model", self.model)
        return text, EngineUsage(
            internal_calls=1,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            usd=price_usd(model, tokens_in, tokens_out),
            latency_ms=latency_ms,
            model=model,
        )


def verify_openai_compatible(
    base_url: str,
    api_key: str | None = None,
    *,
    transport: HttpTransport | None = None,
) -> VerifyResult:
    """Free authenticated check: `GET {base_url}/models`. Covers OpenRouter,
    OpenAI, and local servers (a running Ollama/LM Studio/vLLM answers this even
    with no key). 200 → verified; else verbatim provider message."""
    transport = transport or _default_transport()
    headers = {}
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"
    resp = transport.request("GET", f"{base_url.rstrip('/')}/models", headers=headers)
    if resp.status == 200:
        return VerifyResult(ok=True, detail="Endpoint verified")
    return VerifyResult(ok=False, detail=f"LLM API {resp.status}: {_verbatim(resp)}")
