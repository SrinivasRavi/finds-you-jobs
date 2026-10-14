"""BYOK provider surface: verify and CRUD (FR-SET-06 / US-SET-07 / US-OB-04).

Backs Settings › AI providers and onboarding's Verify. POST /api/engines/verify
runs a real check (a free models-list call, or a minimal completion for a CLI)
and returns the provider's verbatim failure. GET lists configs, masked. POST
saves one, sealing a sent key with the app Fernet key; omitting `key` keeps the
old one, and the registry rebuilds at once. DELETE removes one. Keys never
appear in a response, a log line or an error."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..registry.apply_op import has_extracted_profile
from ..registry.engine_config import (
    PROVIDERS,
    adopt_off_kinds,
    configure_engines,
    verify_provider,
)
from ..registry.engines_http import HttpTransport
from ..security import get_app_key, mask_key, seal_secret
from . import dto
from .deps import data_dir as _data_dir
from .deps import db as _db
from .deps import engines as _engines
from .deps import runner as _runner

router = APIRouter()


def _transport(request: Request) -> HttpTransport | None:
    """The verify HTTP transport. None ⇒ the engines' default `UrllibTransport`.
    Tests set `app.state.http_transport` to a fake so no real network call runs."""
    return getattr(request.app.state, "http_transport", None)


def _adopt_off_kinds(request: Request, provider: str) -> None:
    """A provider just set up takes over the kinds onboarding's Skip turned off,
    then reads the application profile that Skip left unread (audit A5)."""
    with _db(request).repos() as repos:
        prefs = repos.preferences.get_or_create()
        routing = adopt_off_kinds(prefs.engine_routing, provider)
        if routing is None:
            return
        repos.preferences.update(engine_routing=routing)
        needs_extract = repos.profile.get_current() is not None and not has_extracted_profile(
            repos
        )
    _reconfigure(request)
    if needs_extract:
        _runner(request).submit("extract", {})


def _reconfigure(request: Request) -> None:
    """Rebuild the engine registry from the persisted rows + routing so a saved
    or deleted provider takes effect immediately (no restart, no silent stale
    engine)."""
    engines = _engines(request)
    if engines is None:
        return
    with _db(request).repos() as repos:
        prefs = repos.preferences.get_or_create()
        routing = prefs.engine_routing
        rows = repos.engine_settings.list()
    configure_engines(engines, routing, engine_rows=rows, data_dir=_data_dir(request))


# -- verify ----------------------------------------------------------------


# Substrings that mark a `claude` failure as "not logged in" (vs a generic
# error). Matched case-insensitively against the CLI's verbatim stderr so
# onboarding can offer the log-in path instead of a dead-end error.
_AUTH_ERROR_MARKERS = (
    "login",
    "log in",
    "logged in",
    "sign in",
    "signed in",
    "authenticat",
    "unauthor",
    "credential",
    "oauth",
    "setup-token",
    "invalid api key",
    "subscription",
)


def _looks_like_auth_error(detail: str) -> bool:
    low = detail.lower()
    return any(marker in low for marker in _AUTH_ERROR_MARKERS)


def _cli_not_found(provider: str, detail: str) -> dto.EngineVerifyResult:
    """The shared resolve-fail tail: the CLI binary is not on PATH, so
    onboarding shows the install path instead of a dead-end error."""
    return dto.EngineVerifyResult(
        ok=False, status="not_found", detail=detail, provider=provider
    )


def _verify_by_completion(
    engine_cls: Callable[..., Any], *, timeout_s: int, provider: str, ok_detail: str
) -> dto.EngineVerifyResult:
    """The shared fallback: prove a subscription CLI with 1 minimal real completion.
    An `EngineError` comes back verbatim, classed `not_logged_in` when it reads like
    auth, else `error`. Blocking; run it off the event loop."""
    from sidecar.modules._shared.claude_engine import EngineError

    try:
        text, _usage = engine_cls(timeout_s=timeout_s).complete(
            "Reply with the single word OK.", "OK"
        )
    except EngineError as e:
        detail = str(e)
        status = "not_logged_in" if _looks_like_auth_error(detail) else "error"
        return dto.EngineVerifyResult(
            ok=False, status=status, detail=detail, provider=provider
        )
    ok = bool(text.strip())
    return dto.EngineVerifyResult(
        ok=ok, status="ok" if ok else "error", detail=ok_detail, provider=provider
    )


def _verify_claude_cli() -> dto.EngineVerifyResult:
    """Verify the Claude CLI, cheapest first, telling "not installed" from "not
    logged in": resolve the binary (login-shell PATH), then `claude auth status`
    (free, names the account), else fall back to a minimal completion. Blocking (up
    to 60 s); run it off the event loop."""
    from sidecar.modules._shared.claude_engine import (
        ClaudeCliEngine,
        claude_auth_status,
        resolve_claude,
    )

    # refresh=True so a "Retry" after the user installs the CLI re-probes PATH.
    exe = resolve_claude(refresh=True)
    if exe is None:
        return _cli_not_found(
            "claude-cli", "Claude CLI not found. Install Claude Code, then Verify."
        )
    auth = claude_auth_status(exe)
    if auth is not None:
        if auth.get("logged_in"):
            email = auth.get("email")
            plan = auth.get("plan")
            who = str(email) if email else "your Claude account"
            detail = f"Logged in as {who}" + (f" · {str(plan).capitalize()} plan" if plan else "")
            return dto.EngineVerifyResult(
                ok=True, status="ok", detail=detail, provider="claude-cli"
            )
        return dto.EngineVerifyResult(
            ok=False,
            status="not_logged_in",
            detail="Claude CLI is installed but not logged in.",
            provider="claude-cli",
        )
    # No auth-status answer (older CLI) — prove it with a minimal completion.
    return _verify_by_completion(
        ClaudeCliEngine,
        timeout_s=60,
        provider="claude-cli",
        ok_detail="claude CLI reachable",
    )


def _verify_codex_cli() -> dto.EngineVerifyResult:
    """Verify the ChatGPT-subscription Codex CLI, cheapest-first — mirrors
    `_verify_claude_cli`: resolve binary → instant `codex login status` probe →
    minimal real completion only when the probe can't answer."""
    from sidecar.modules._shared import cli_engines as ce

    exe = ce.resolve_cli("codex", refresh=True)
    if exe is None:
        return _cli_not_found(
            "codex-cli", "Codex CLI not found. Install OpenAI Codex, then Verify."
        )
    probe = ce.codex_login_status(exe)
    if probe is not None:
        if probe.status == "ok":
            return dto.EngineVerifyResult(
                ok=True, status="ok", detail=probe.detail, provider="codex-cli"
            )
        return dto.EngineVerifyResult(
            ok=False, status="not_logged_in", detail=probe.detail, provider="codex-cli"
        )
    return _verify_by_completion(
        ce.CodexCliEngine,
        timeout_s=60,
        provider="codex-cli",
        ok_detail="Codex CLI reachable",
    )


def _verify_antigravity_cli() -> dto.EngineVerifyResult:
    """Verify the Antigravity CLI (experimental) with a minimal REAL completion
    through the exact non-TTY subprocess path the engine uses — deliberately no
    cheap auth probe: `agy`'s known non-interactive stdout bug must be caught
    here at setup, not later mid-pipeline (design decision 2026-07-17)."""
    from sidecar.modules._shared import cli_engines as ce

    exe = ce.resolve_cli("agy", refresh=True)
    if exe is None:
        return _cli_not_found(
            "antigravity-cli",
            "Antigravity CLI (agy) not found. Install Antigravity, then Verify.",
        )
    return _verify_by_completion(
        ce.AntigravityCliEngine,
        timeout_s=90,
        provider="antigravity-cli",
        ok_detail="Antigravity CLI reachable (non-interactive mode verified)",
    )


# Subscription-CLI verify dispatch (mirrors engine_config.CLI_PROVIDERS).
_CLI_VERIFIERS = {
    "claude-cli": _verify_claude_cli,
    "codex-cli": _verify_codex_cli,
    "antigravity-cli": _verify_antigravity_cli,
}


@router.post("/api/engines/verify")
async def verify_engine(
    request: Request, payload: dto.EngineVerifyRequest
) -> dto.EngineVerifyResult:
    # Both probes block (subprocess / synchronous HTTP); run them in a worker
    # thread so a slow verify never stalls uvicorn's event loop (the SSE
    # heartbeat and every concurrent request) — the "Load failed" root cause.
    cli_verifier = _CLI_VERIFIERS.get(payload.provider)
    if cli_verifier is not None:
        verified = await run_in_threadpool(cli_verifier)
        if verified.ok:
            await run_in_threadpool(_adopt_off_kinds, request, payload.provider)
            await run_in_threadpool(_reconfigure, request)
        return verified
    result = await run_in_threadpool(
        verify_provider,
        payload.provider,
        api_key=payload.key,
        base_url=payload.base_url,
        transport=_transport(request),
    )
    return dto.EngineVerifyResult(
        ok=result.ok,
        status="ok" if result.ok else "error",
        detail=result.detail,
        provider=payload.provider,
    )


# -- CRUD ------------------------------------------------------------------


@router.get("/api/engines")
def list_engines(request: Request) -> list[dto.EngineSettingDTO]:
    with _db(request).repos() as repos:
        return [dto.engine_setting_dto(e) for e in repos.engine_settings.list()]


@router.post("/api/engines", status_code=201)
def save_engine(
    request: Request, payload: dto.EngineSettingUpsert
) -> dto.EngineSettingDTO:
    if payload.provider not in PROVIDERS:
        raise HTTPException(status_code=422, detail=f"unknown provider {payload.provider!r}")

    fields: dict[str, object] = {
        "base_url": payload.base_url,
        "default_model": payload.default_model,
        "enabled": payload.enabled,
    }
    # Seal the key only when one is sent; an empty/omitted key leaves the stored
    # secret untouched (edit base_url/model without re-pasting the key).
    if payload.key:
        key = get_app_key(_data_dir(request))
        fields["key_encrypted"] = seal_secret(payload.key, key)
        fields["key_ref"] = mask_key(payload.key)

    with _db(request).repos() as repos:
        existing = repos.engine_settings.get_by_engine(payload.provider)
        # No model sent (onboarding saves just the key): keep the stored one,
        # else the provider's safe default — a routed engine with no model at
        # all is unusable (the request would name no model).
        if not fields["default_model"]:
            prior = existing.default_model if existing is not None else None
            fields["default_model"] = prior or PROVIDERS[payload.provider].default_model
        if existing is None:
            row = repos.engine_settings.create(payload.provider, **fields)
        else:
            row = repos.engine_settings.update(existing.id, **fields)
        result = dto.engine_setting_dto(row)
    _adopt_off_kinds(request, payload.provider)
    _reconfigure(request)
    return result


@router.delete("/api/engines/{provider}", status_code=204)
def delete_engine(request: Request, provider: str) -> None:
    with _db(request).repos() as repos:
        removed = repos.engine_settings.delete_by_engine(provider)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no engine config for {provider!r}")
    _reconfigure(request)
