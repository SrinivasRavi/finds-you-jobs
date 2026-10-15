"""Engine wiring from Settings (architecture sections 5.4 and 9).

`claude-cli` stays the default engine, registered as a per-model factory so a
per-kind model override works. The BYOK engines (`anthropic`, and the
OpenAI-compatible `openrouter`, `openai`, `local`) register from EngineSettings
rows with a usable config. Routing to an unregistered engine raises
`EngineNotConfiguredError`: never a hang or a silent fallback."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from sidecar.modules._shared.claude_engine import DEFAULT_MODEL, ClaudeCliEngine
from sidecar.modules._shared.cli_engines import AntigravityCliEngine, CodexCliEngine

from ..logging_setup import get_logger
from ..security import get_app_key, open_secret
from .engines import Engine, EngineRegistry
from .engines_http import (
    DEFAULT_ANTHROPIC_BASE,
    DEFAULT_OPENAI_BASE,
    DEFAULT_OPENROUTER_BASE,
    AnthropicEngine,
    HttpTransport,
    OpenAICompatibleEngine,
    VerifyResult,
    _default_transport,
    _verbatim,
    verify_anthropic,
    verify_openai_compatible,
)

# The dev engine of record (architecture section 9). Stays the routing default — the
# BYOK engines are opt-in via the Settings routing map, never auto-selected.
DEFAULT_ENGINE = "claude-cli"

# Subscription-CLI providers: builtin engines driven through the user's
# logged-in coding CLI, with no key and no stored row. Not in PROVIDERS, so POST
# /api/engines rejects them as BYOK configs; an uninstalled one fails at call time
# with a clear EngineError.
CLI_PROVIDERS = (DEFAULT_ENGINE, "codex-cli", "antigravity-cli")

# The LLM-backed kinds that need a routed engine; one missing here is never
# routed and dies EngineNotConfiguredError in production. `extract` is the small
# application-profile call at master save (FR-APP-01); `draft` is the Networker;
# `apply` is the Applier loop. `prep` is retired (applier-as-built.md section 2).
LLM_KINDS = ("score", "tailor", "cover", "extract", "draft", "apply", "chat")

# The routing value onboarding's Skip writes (S-C60): the kind stays unrouted,
# so it fails with EngineNotConfiguredError, until a provider is saved or a CLI
# verified, which takes over every kind still set to it (`adopt_off_kinds`).
AI_OFF = "none"


# ---------------------------------------------------------------------------
# Provider catalog — the P1 BYOK menu (US-OB-04 / US-SET-07)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    """A selectable provider tile → the engine that backs it."""

    id: str
    engine_kind: str  # "anthropic" | "openai-compatible"
    default_base_url: str | None
    needs_key: bool
    needs_base_url: bool  # a user-run local server has no default URL
    # The model used when the user saved no explicit one (onboarding saves just
    # the key). Routing an engine without a model falls back to the row's
    # default_model, so this keeps a fresh BYOK provider actually usable.
    # None → no safe guess (local: the user's server names its own models).
    default_model: str | None = None


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        "anthropic", "anthropic", DEFAULT_ANTHROPIC_BASE, True, False,
        default_model="claude-sonnet-5",
    ),
    "openrouter": ProviderSpec(
        "openrouter", "openai-compatible", DEFAULT_OPENROUTER_BASE, True, False,
        # OpenRouter's own auto-router — their designed default for "just works".
        default_model="openrouter/auto",
    ),
    "openai": ProviderSpec(
        "openai", "openai-compatible", DEFAULT_OPENAI_BASE, True, False,
        default_model="gpt-4o-mini",
    ),
    # Local LLM: user provides the base URL; key optional (Ollama needs none).
    "local": ProviderSpec("local", "openai-compatible", None, False, True),
    # Plans people already pay for, over the same engine (10-10 report 4.6).
    "zai": ProviderSpec(
        "zai", "openai-compatible", "https://api.z.ai/api/paas/v4", True, False,
        default_model="glm-5",
    ),
    "zai-coding": ProviderSpec(
        "zai-coding", "openai-compatible", "https://api.z.ai/api/coding/paas/v4", True, False,
        default_model="glm-5",
    ),
    "opencode": ProviderSpec(
        "opencode", "openai-compatible", "https://opencode.ai/inference/v1", True, False,
        default_model="glm-5.3",
    ),
}

# Providers whose `/models` answers without a key, so it proves nothing about one.
KEYLESS_MODEL_LISTS = frozenset({"opencode"})


def _prove_key(
    base_url: str, api_key: str, listed: VerifyResult, transport: HttpTransport | None
) -> VerifyResult:
    """A chat call naming no real model: auth runs first, so 401 or 403 is a bad
    key and anything else means the key passed. No model runs, nothing is spent."""
    resp = (transport or _default_transport()).request(
        "POST",
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
        body=json.dumps({"model": "fyj-verify-probe", "max_tokens": 1,
                         "messages": [{"role": "user", "content": "."}]}).encode(),
    )
    if resp.status in (401, 403):
        return VerifyResult(ok=False, detail=f"LLM API {resp.status}: {_verbatim(resp)}")
    return listed


# Verify suggests the first of these the account's `/models` lists. OpenCode's
# Claude and GPT ids use other wire formats, so only chat-completions ids appear.
PREFERRED_MODELS: dict[str, tuple[str, ...]] = {
    "zai": ("glm-5.3", "glm-5.2", "glm-5.1", "glm-5"),
    "zai-coding": ("glm-5.3", "glm-5.2", "glm-5.1", "glm-5"),
    "opencode": (
        "glm-5.3", "kimi-k3", "deepseek-v4-pro", "qwen3.8-max", "glm-5.2", "kimi-k2.7-code",
    ),
}


def build_engine(
    spec: ProviderSpec,
    *,
    api_key: str | None,
    base_url: str | None,
    model: str,
    transport: HttpTransport | None = None,
) -> Engine:
    """Construct the concrete engine a provider spec names."""
    resolved_base = base_url or spec.default_base_url
    if spec.engine_kind == "anthropic":
        return AnthropicEngine(
            api_key=api_key or "",
            model=model,
            base_url=resolved_base or DEFAULT_ANTHROPIC_BASE,
            transport=transport,
        )
    # openai-compatible
    return OpenAICompatibleEngine(
        base_url=resolved_base or "",
        model=model,
        api_key=api_key,
        transport=transport,
    )


def verify_provider(
    provider: str,
    *,
    api_key: str | None,
    base_url: str | None,
    transport: HttpTransport | None = None,
) -> VerifyResult:
    """Dispatch a provider-appropriate verification (the free models-list call).
    Validates required fields up front so the failure is a clear message, not an
    opaque HTTP error."""
    spec = PROVIDERS.get(provider)
    if spec is None:
        return VerifyResult(ok=False, detail=f"unknown provider {provider!r}")
    resolved_base = base_url or spec.default_base_url
    if spec.needs_key and not api_key:
        return VerifyResult(ok=False, detail="an API key is required for this provider")
    if spec.needs_base_url and not resolved_base:
        return VerifyResult(ok=False, detail="a base URL is required for a local LLM")
    if spec.engine_kind == "anthropic":
        return verify_anthropic(
            api_key or "", base_url=resolved_base or DEFAULT_ANTHROPIC_BASE, transport=transport
        )
    result = verify_openai_compatible(resolved_base or "", api_key, transport=transport)
    if result.ok and provider in KEYLESS_MODEL_LISTS:
        result = _prove_key(resolved_base or "", api_key or "", result, transport)
    listed = set(result.models)
    suggested = next((m for m in PREFERRED_MODELS.get(provider, ()) if m in listed), None)
    return replace(result, suggested_model=suggested)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class FakeInstantEngine:
    """FYJ_FAKE_LLM's engine: an instant canned completion, 0 subprocesses. The
    Playwright suite once made real `claude -p` calls on every profile save. Env-gated
    like FYJ_APPLY_DEV, never set by the shell, 0 tokens recorded."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "fake-instant"

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Any]:
        from sidecar.modules._shared.claude_engine import EngineUsage

        text = _fake_chat(user_prompt) if system_prompt.startswith(_CHAT_MARK) else "{}"
        return text, EngineUsage(
            internal_calls=1, tokens_in=0, tokens_out=0, usd=0.0, model=self.model
        )


_CHAT_MARK = "You are the assistant inside finds-you-jobs"


def _fake_chat(transcript: str) -> str:
    """The chat agent's e2e script: after a tool result, say what came back; on
    "invite", call reach_out (a confirmation card with autopilot off); else read
    the applications."""
    last = [ln for ln in transcript.splitlines() if ln.startswith("[")][-1]
    if last.startswith("[tool "):
        tool = last[len("[tool "):].split("]", 1)[0]
        return json.dumps({"say": f"Done: {tool} answered."})
    if "invite" in last.lower():
        return json.dumps({"call": {"tool": "reach_out", "args": {"body": {}}}})
    return json.dumps({"call": {"tool": "list_applications", "args": {}}})


def register_builtin_engines(registry: EngineRegistry) -> None:
    """Register the always-present CLI engines: `claude-cli` at its pinned default,
    the others at their own default when no model is routed. FYJ_FAKE_LLM=1 swaps
    them for `FakeInstantEngine`."""
    if os.environ.get("FYJ_FAKE_LLM"):
        for name in CLI_PROVIDERS:
            registry.register_factory(name, lambda model: FakeInstantEngine(model))
        return
    registry.register_factory(
        DEFAULT_ENGINE, lambda model: ClaudeCliEngine(model=model or DEFAULT_MODEL)
    )
    registry.register_factory("codex-cli", lambda model: CodexCliEngine(model=model))
    registry.register_factory(
        "antigravity-cli", lambda model: AntigravityCliEngine(model=model)
    )


def register_byok_engines(
    registry: EngineRegistry,
    engine_rows: Iterable[Any],
    data_dir: Path,
) -> None:
    """Register a routable engine for every enabled `EngineSettings` row that
    carries a usable config. The sealed key is opened once here and captured in
    the factory closure. A row missing a required key/base_url is skipped (so
    routing to it stays honestly unconfigured)."""
    log = get_logger()
    for row in engine_rows:
        if not getattr(row, "enabled", True):
            continue
        spec = PROVIDERS.get(row.engine)
        if spec is None:
            continue
        api_key: str | None = None
        if row.key_encrypted:
            try:
                api_key = open_secret(row.key_encrypted, get_app_key(data_dir))
            except Exception:  # noqa: BLE001 — a corrupt/rotated key must not block boot
                log.exception("could not open sealed key for engine %s", row.engine)
                continue
        base_url = row.base_url or spec.default_base_url
        if spec.needs_key and not api_key:
            continue
        if spec.needs_base_url and not base_url:
            continue
        default_model = row.default_model or ""

        def _factory(
            model: str | None,
            _spec: ProviderSpec = spec,
            _key: str | None = api_key,
            _base: str | None = base_url,
            _default_model: str = default_model,
        ) -> Engine:
            return build_engine(
                _spec, api_key=_key, base_url=_base, model=model or _default_model
            )

        registry.register_factory(row.engine, _factory)


def apply_routing(registry: EngineRegistry, engine_routing: dict[str, Any] | None) -> None:
    """(Re)apply the per-kind routing map: each LLM kind to its `(engine, model)`, or
    `claude-cli` at the default. Non-LLM kinds stay unrouted."""
    routing = engine_routing or {}
    registry.clear_routing()
    for kind in LLM_KINDS:
        entry = routing.get(kind)
        entry = entry if isinstance(entry, dict) else {}
        if entry.get("engine") == AI_OFF:
            continue
        engine_name = entry.get("engine") or DEFAULT_ENGINE
        model = entry.get("model") or (DEFAULT_MODEL if engine_name == DEFAULT_ENGINE else None)
        registry.route(kind, engine=engine_name, model=model)


def configure_engines(
    registry: EngineRegistry,
    engine_routing: dict[str, Any] | None,
    *,
    engine_rows: Iterable[Any] | None = None,
    data_dir: Path | None = None,
) -> None:
    """Register the builtin + BYOK engines and apply the routing map (startup +
    on Settings change). Resets the registry first so removed providers leave no
    stale factory behind."""
    registry.reset()
    register_builtin_engines(registry)
    if engine_rows is not None and data_dir is not None:
        register_byok_engines(registry, engine_rows, data_dir)
    apply_routing(registry, engine_routing)


def adopt_off_kinds(routing: dict[str, Any] | None, provider: str) -> dict[str, Any] | None:
    """The routing map with every kind set to `AI_OFF` moved to `provider`, or
    None when no kind was off. The user who skipped the provider step and later
    sets one up gets AI on everywhere, without re-routing kind by kind."""
    current = dict(routing or {})
    off = [k for k, v in current.items() if isinstance(v, dict) and v.get("engine") == AI_OFF]
    if not off:
        return None
    for k in off:
        current[k] = {"engine": provider, "model": ""}
    return current
