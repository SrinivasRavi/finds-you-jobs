"""Engine wiring from Settings (architecture §5.4/§9).

`claude-cli` (the shared `ClaudeCliEngine`) stays the **default** engine of
record — no routing default is flipped here. It is registered as a per-model
**factory** so a per-kind model override in `UserPreferences.engine_routing` is
honored.

On top of that, the BYOK direct-API engines (`anthropic`, `openrouter`,
`openai`, `local` — the last three are OpenAI-compatible) are registered from
the persisted `EngineSettings` rows: a row that carries a usable config (a
sealed key where one is required, a base URL where one is required) becomes a
routable engine under its provider id, so routing a kind to it in Settings just
works. A routing entry naming an engine we have *not* registered resolves to
`None`, and the operation wrapper then raises the typed
`EngineNotConfiguredError` — never a silent hang, never a silent fallback.

The one-way rule holds: this is `app/` code reusing the module's shared
`_shared/claude_engine.py` (via `engines_http`); the module never imports back.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
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
    verify_anthropic,
    verify_openai_compatible,
)

# The dev engine of record (architecture §9). Stays the routing default — the
# BYOK engines are opt-in via the Settings routing map, never auto-selected.
DEFAULT_ENGINE = "claude-cli"

# The subscription-CLI provider family (2026-07-17 expansion): always-registered
# builtin engines driven through the user's logged-in coding CLI — no key, no
# EngineSettings row, nothing to persist. NOT in PROVIDERS below on purpose:
# POST /api/engines must keep rejecting them as BYOK configs. Routing to one
# that isn't installed fails at call time with a clear EngineError, matching
# claude-cli's long-standing behavior.
CLI_PROVIDERS = (DEFAULT_ENGINE, "codex-cli", "antigravity-cli")

# The LLM-backed operation kinds that need a routed engine. `extract` is the
# application-profile extraction at master-save (FR-APP-01) — a small call; a
# cheap model is fine. (The prior repository also routed `prep`; Save-time
# form-prep is retired in this rebuild — `docs/internal/applier.md` §2 — so
# the kind is deliberately absent.)
# Every LLM-driven kind must be here or apply_routing never routes it and the
# op dies EngineNotConfiguredError in production. `draft` (Networker) was
# missing in the prior repository — a latent bug this rebuild fixes; `apply`
# is the Applier agent loop.
LLM_KINDS = ("score", "tailor", "cover", "extract", "draft", "apply")


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
    return verify_openai_compatible(resolved_base or "", api_key, transport=transport)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class FakeInstantEngine:
    """FYJ_FAKE_LLM's engine: instant canned completion, zero subprocesses.

    Exists because the Playwright suite was discovered (2026-07-18) making REAL
    `claude -p` calls on the dev machine's logged-in subscription — every
    spec's profile save enqueues an `extract` op, default-routed to claude-cli.
    That broke the suite's zero-model contract, spent real tokens per run, and
    the ~10s child subprocesses were the root of teardown hangs and shutdown-
    drain flakes. Same dev-seam philosophy as FYJ_APPLY_DEV: env-gated, never
    set by the shell, honest to the ledger (zero tokens recorded).
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or "fake-instant"

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Any]:
        from sidecar.modules._shared.claude_engine import EngineUsage

        return "{}", EngineUsage(
            internal_calls=1, tokens_in=0, tokens_out=0, usd=0.0, model=self.model
        )


def register_builtin_engines(registry: EngineRegistry) -> None:
    """Register the always-present subscription-CLI engines. `claude-cli` keeps
    its pinned default model; the other CLIs run their own configured default
    when the routing entry names no model (`model=None` omits the flag).

    FYJ_FAKE_LLM=1 (dev/e2e only) swaps every builtin CLI engine for
    `FakeInstantEngine` so no test run ever drives a real subscription CLI.
    BYOK rows are untouched — the e2e environment persists none."""
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
    """(Re)apply the per-kind routing map from settings onto the registry.

    Each LLM kind routes to its configured `(engine, model)` or, absent an
    entry, to `claude-cli` at the default model. Non-LLM kinds (`scan`) need no
    engine and are left unrouted.
    """
    routing = engine_routing or {}
    registry.clear_routing()
    for kind in LLM_KINDS:
        entry = routing.get(kind)
        entry = entry if isinstance(entry, dict) else {}
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
