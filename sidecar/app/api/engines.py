"""BYOK provider surface — verify + CRUD (FR-SET-06 / US-SET-07 / US-OB-04).

A dedicated router (kept out of the contested `routes.py`). It backs both the
Settings → AI Providers panel and the Onboarding Verify gate:

- `POST /api/engines/verify` — a provider-appropriate real check. Anthropic /
  OpenAI / OpenRouter / Local use the free authenticated models-list call;
  `claude-cli` runs a minimal real completion. Returns `{ok, detail, provider}`
  with the provider's **verbatim** failure text on `ok=False`.
- `GET /api/engines` — the persisted engine configs, masked (`key_hint`, never
  the key).
- `POST /api/engines` — save/replace a provider config. A sent `key` is
  **sealed** with the app Fernet key (`get_app_key`) into
  `EngineSettings.key_encrypted`; `key_ref` carries only a masked hint. Omitting
  `key` leaves an existing sealed key intact. After the write the engine
  registry is rebuilt so routing to the provider works immediately.
- `DELETE /api/engines/{provider}` — remove a provider config + rebuild.

Keys never appear in a response (masked only), a log line, or an error message.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from ..db import Database
from ..registry import EngineRegistry
from ..registry.engine_config import PROVIDERS, configure_engines, verify_provider
from ..registry.engines_http import HttpTransport
from ..security import get_app_key, mask_key, seal_secret
from . import dto

router = APIRouter()


# -- app.state accessors ---------------------------------------------------


def _db(request: Request) -> Database:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="storage not initialized")
    return db


def _engines(request: Request) -> EngineRegistry | None:
    return getattr(request.app.state, "engines", None)


def _data_dir(request: Request) -> Path:
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir is None:
        raise HTTPException(status_code=503, detail="data dir not initialized")
    return Path(data_dir)


def _transport(request: Request) -> HttpTransport | None:
    """The verify HTTP transport. None ⇒ the engines' default `UrllibTransport`.
    Tests set `app.state.http_transport` to a fake so no real network call runs."""
    return getattr(request.app.state, "http_transport", None)


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


def _verify_claude_cli() -> dto.EngineVerifyResult:
    """Verify the Claude-subscription CLI, cheapest-first. Distinguishes 'not
    installed' from 'not logged in' so onboarding can guide the exact fix.

    1. Resolve the binary (login-shell PATH, so a GUI launch finds it).
    2. `claude auth status` — an instant, free auth probe that also names the
       account (email + plan), matching how the API providers verify (a free
       authenticated check, not a completion).
    3. Older CLI without `auth status` → fall back to a minimal real completion.

    Blocking (subprocesses, up to 60s on the fallback); callers must run it off
    the event loop.
    """
    from sidecar.modules._shared.claude_engine import (
        ClaudeCliEngine,
        EngineError,
        claude_auth_status,
        resolve_claude,
    )

    # refresh=True so a "Retry" after the user installs the CLI re-probes PATH.
    exe = resolve_claude(refresh=True)
    if exe is None:
        return dto.EngineVerifyResult(
            ok=False,
            status="not_found",
            detail="Claude CLI not found. Install Claude Code, then Verify.",
            provider="claude-cli",
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
    try:
        text, _usage = ClaudeCliEngine(timeout_s=60).complete(
            "Reply with the single word OK.", "OK"
        )
    except EngineError as e:
        detail = str(e)
        status = "not_logged_in" if _looks_like_auth_error(detail) else "error"
        return dto.EngineVerifyResult(
            ok=False, status=status, detail=detail, provider="claude-cli"
        )
    ok = bool(text.strip())
    return dto.EngineVerifyResult(
        ok=ok,
        status="ok" if ok else "error",
        detail="claude CLI reachable",
        provider="claude-cli",
    )


@router.post("/api/engines/verify")
async def verify_engine(
    request: Request, payload: dto.EngineVerifyRequest
) -> dto.EngineVerifyResult:
    # Both probes block (subprocess / synchronous HTTP); run them in a worker
    # thread so a slow verify never stalls uvicorn's event loop (the SSE
    # heartbeat and every concurrent request) — the "Load failed" root cause.
    if payload.provider == "claude-cli":
        return await run_in_threadpool(_verify_claude_cli)
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
async def list_engines(request: Request) -> list[dto.EngineSettingDTO]:
    with _db(request).repos() as repos:
        return [dto.engine_setting_dto(e) for e in repos.engine_settings.list()]


@router.post("/api/engines", status_code=201)
async def save_engine(
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
        result = dto.engine_setting_dto(row) if row is not None else None
    if result is None:  # pragma: no cover — update-after-existing always finds it
        raise HTTPException(status_code=500, detail="failed to persist engine")
    _reconfigure(request)
    return result


@router.delete("/api/engines/{provider}", status_code=204)
async def delete_engine(request: Request, provider: str) -> None:
    with _db(request).repos() as repos:
        removed = repos.engine_settings.delete_by_engine(provider)
    if not removed:
        raise HTTPException(status_code=404, detail=f"no engine config for {provider!r}")
    _reconfigure(request)
