"""Covers: the BYOK provider router (FR-SET-06 / US-SET-07 / US-OB-04).

Drives `sidecar/app/api/engines.py` through the real app (TestClient → lifespan
→ real migration + engine registry):

- save seals the key (never plaintext on disk), list returns a masked hint only;
- verify makes a provider-appropriate call against a **fake** HTTP transport
  (no network), surfacing a 401's verbatim body;
- a saved + routed BYOK engine registers so `resolve(kind)` returns it;
- delete removes the config and 404s when absent;
- the claude-cli live verify is env-guarded (FYJ_LIVE_E2E).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry.engines_http import HttpResponse, OpenAICompatibleEngine
from sidecar.app.security import SESSION_KEY_ENV

TOKEN = "test-token-engines"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SECRET_KEY = "sk-ant-super-secret-value-abcd1234"  # noqa: S105 — fixture


@dataclass
class FakeTransport:
    responses: list[HttpResponse]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def request(self, method, url, *, headers, body=None, timeout_s=60):  # noqa: ANN001, ANN201
        self.calls.append((method, url))
        if not self.responses:
            return HttpResponse(status=200, body=b'{"data": []}')
        return self.responses.pop(0)


def _resp(status: int, payload: object) -> HttpResponse:
    return HttpResponse(status=status, body=json.dumps(payload).encode())


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, TestClient]]:
    # A deterministic Fernet key via the env override → seals BYOK keys without
    # ever touching the OS keychain (hermetic on any machine).
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    app.state._test_data_dir = tmp_path / "data"  # for the on-disk plaintext scan
    with TestClient(app) as client:
        yield app, client


# ---------------------------------------------------------------------------
# save → seal → list masked
# ---------------------------------------------------------------------------


def test_save_seals_key_and_list_is_masked(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    resp = client.post(
        "/api/engines",
        headers=AUTH,
        json={"provider": "anthropic", "key": SECRET_KEY, "default_model": "claude-opus-4-8"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["engine"] == "anthropic"
    assert body["has_key"] is True
    # The response carries a masked hint, never the key.
    assert body["key_hint"].endswith("1234")
    assert SECRET_KEY not in json.dumps(body)

    listing = client.get("/api/engines", headers=AUTH).json()
    assert any(e["engine"] == "anthropic" and e["has_key"] for e in listing)
    assert SECRET_KEY not in json.dumps(listing)

    # NFR-SEC-01: the plaintext key is nowhere on disk under the data dir.
    data_dir: Path = app.state._test_data_dir
    for path in data_dir.rglob("*"):
        if path.is_file():
            assert SECRET_KEY.encode() not in path.read_bytes(), f"plaintext key in {path}"

    # And the stored blob is sealed bytes that decrypt back (round-trip).
    from sidecar.app.security import get_app_key, open_secret

    with app.state.db.repos() as repos:
        row = repos.engine_settings.get_by_engine("anthropic")
        assert row.key_encrypted is not None
        assert SECRET_KEY.encode() not in row.key_encrypted
        assert open_secret(row.key_encrypted, get_app_key(data_dir)) == SECRET_KEY


def test_save_without_key_keeps_existing_sealed_key(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    client.post("/api/engines", headers=AUTH, json={"provider": "anthropic", "key": SECRET_KEY})
    # Edit the model without re-pasting the key.
    resp = client.post(
        "/api/engines",
        headers=AUTH,
        json={"provider": "anthropic", "default_model": "claude-sonnet-4-6"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["default_model"] == "claude-sonnet-4-6"
    assert body["has_key"] is True  # key preserved


def test_save_unknown_provider_422(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post("/api/engines", headers=AUTH, json={"provider": "bogus", "key": "x"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# verify (fake transport)
# ---------------------------------------------------------------------------


def test_verify_ok_against_fake_transport(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app.state.http_transport = FakeTransport([_resp(200, {"data": [{"id": "gpt-5"}]})])
    resp = client.post(
        "/api/engines/verify", headers=AUTH, json={"provider": "openai", "key": "sk-ok"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "openai"
    assert app.state.http_transport.calls[0] == ("GET", "https://api.openai.com/v1/models")


def test_verify_401_surfaces_verbatim(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    app.state.http_transport = FakeTransport(
        [_resp(401, {"error": {"message": "invalid x-api-key"}})]
    )
    resp = client.post(
        "/api/engines/verify", headers=AUTH, json={"provider": "anthropic", "key": "bad"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "invalid x-api-key" in body["detail"]


def test_verify_local_missing_base_url_fails_without_network(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    app.state.http_transport = FakeTransport([])  # would error if a call were made
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "local"})
    body = resp.json()
    assert body["ok"] is False
    assert "base URL" in body["detail"]


# ---------------------------------------------------------------------------
# claude-cli verify status (mocked engine — no real subprocess)
# ---------------------------------------------------------------------------


def test_looks_like_auth_error_classification() -> None:
    from sidecar.app.api.engines import _looks_like_auth_error

    assert _looks_like_auth_error("claude CLI exited 1: Invalid API key · Please run /login")
    assert _looks_like_auth_error("Not authenticated with your subscription")
    # A timeout / generic failure is NOT an auth problem.
    assert not _looks_like_auth_error("claude CLI timed out after 60s")
    assert not _looks_like_auth_error("connection reset by peer")


def test_verify_claude_cli_not_found(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Binary not on PATH → `not_found`, so onboarding shows the install path."""
    import sidecar.modules._shared.claude_engine as ce

    _app, client = app_client
    monkeypatch.setattr(ce, "resolve_claude", lambda refresh=False: None)
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "claude-cli"})
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "not_found"
    assert "Install" in body["detail"]


def test_verify_claude_cli_auth_status_logged_in(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The instant `claude auth status` probe answers: ok + names the account —
    and NO completion runs (it would blow up if it did)."""
    import sidecar.modules._shared.claude_engine as ce

    _app, client = app_client
    monkeypatch.setattr(ce, "resolve_claude", lambda refresh=False: "/usr/local/bin/claude")
    monkeypatch.setattr(
        ce,
        "claude_auth_status",
        lambda exe=None: {"logged_in": True, "email": "jane@example.com", "plan": "pro"},
    )

    def _never(self: object, system: str, user: str) -> tuple[str, object]:
        raise AssertionError("completion must not run when auth status answers")

    monkeypatch.setattr(ce.ClaudeCliEngine, "complete", _never)
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "claude-cli"})
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert "jane@example.com" in body["detail"]
    assert "Pro plan" in body["detail"]


def test_verify_claude_cli_auth_status_logged_out(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`claude auth status` reports logged out → `not_logged_in`, instantly."""
    import sidecar.modules._shared.claude_engine as ce

    _app, client = app_client
    monkeypatch.setattr(ce, "resolve_claude", lambda refresh=False: "/usr/local/bin/claude")
    monkeypatch.setattr(
        ce, "claude_auth_status", lambda exe=None: {"logged_in": False, "email": None, "plan": None}
    )
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "claude-cli"})
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "not_logged_in"


def test_verify_claude_cli_fallback_not_logged_in(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older CLI (no auth-status answer) + an auth error from the completion
    fallback → `not_logged_in` (offer log-in)."""
    import sidecar.modules._shared.claude_engine as ce

    _app, client = app_client
    monkeypatch.setattr(ce, "resolve_claude", lambda refresh=False: "/usr/local/bin/claude")
    monkeypatch.setattr(ce, "claude_auth_status", lambda exe=None: None)

    def _auth_boom(self: object, system: str, user: str) -> tuple[str, object]:
        raise ce.EngineError("claude CLI exited 1: Invalid API key · Please run /login")

    monkeypatch.setattr(ce.ClaudeCliEngine, "complete", _auth_boom)
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "claude-cli"})
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == "not_logged_in"
    assert "login" in body["detail"].lower()


def test_verify_claude_cli_fallback_ok(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Older CLI (no auth-status answer) + a real one-word completion → `ok`."""
    import sidecar.modules._shared.claude_engine as ce

    _app, client = app_client
    monkeypatch.setattr(ce, "resolve_claude", lambda refresh=False: "/usr/local/bin/claude")
    monkeypatch.setattr(ce, "claude_auth_status", lambda exe=None: None)
    monkeypatch.setattr(
        ce.ClaudeCliEngine,
        "complete",
        lambda self, system, user: ("OK", ce.EngineUsage(internal_calls=1)),
    )
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "claude-cli"})
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "ok"


def test_subscription_env_scrubs_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray ANTHROPIC_API_KEY would silently flip the CLI to pay-per-token
    API billing; the subscription engine's child env must drop it."""
    import sidecar.modules._shared.claude_engine as ce

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stray")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-stray")
    env = ce._subscription_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "PATH" in env  # the rest of the env passes through


# ---------------------------------------------------------------------------
# registration → routing
# ---------------------------------------------------------------------------


def test_save_without_model_fills_provider_default(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Onboarding saves just the key — the row must still carry a usable model
    (the provider's safe default), or routing to it would name no model at all."""
    _app, client = app_client
    resp = client.post(
        "/api/engines", headers=AUTH, json={"provider": "openrouter", "key": "sk-or-x"}
    )
    assert resp.status_code == 201
    assert resp.json()["default_model"] == "openrouter/auto"
    # An explicit model on a later edit still wins and persists.
    resp = client.post(
        "/api/engines",
        headers=AUTH,
        json={"provider": "openrouter", "default_model": "anthropic/claude-sonnet-5"},
    )
    assert resp.json()["default_model"] == "anthropic/claude-sonnet-5"
    # And a subsequent key-only save keeps the user's explicit model.
    resp = client.post(
        "/api/engines", headers=AUTH, json={"provider": "openrouter", "key": "sk-or-y"}
    )
    assert resp.json()["default_model"] == "anthropic/claude-sonnet-5"


def test_routing_without_model_uses_engine_default_model(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """The onboarding routing write sends {engine} with no model — the resolved
    engine must fall back to the row's default_model (2026-07-12 fix: BYOK
    onboarding left everything silently routed to claude-cli)."""
    app, client = app_client
    client.post("/api/engines", headers=AUTH, json={"provider": "openrouter", "key": "sk-or-1"})
    r = client.post(
        "/api/settings",
        headers=AUTH,
        json={"engine_routing": {"score": {"engine": "openrouter", "model": ""}}},
    )
    assert r.status_code == 200
    resolved = app.state.engines.resolve("score")
    assert resolved is not None
    assert resolved.name == "openrouter"
    assert isinstance(resolved.engine, OpenAICompatibleEngine)
    assert resolved.engine.model == "openrouter/auto"


def test_saved_and_routed_engine_resolves(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    # Save an OpenRouter engine.
    client.post(
        "/api/engines",
        headers=AUTH,
        json={"provider": "openrouter", "key": "sk-or-1", "default_model": "x/y"},
    )
    # Route `score` to it.
    r = client.post(
        "/api/settings",
        headers=AUTH,
        json={"engine_routing": {"score": {"engine": "openrouter", "model": "x/y"}}},
    )
    assert r.status_code == 200
    resolved = app.state.engines.resolve("score")
    assert resolved is not None
    assert resolved.name == "openrouter"
    assert isinstance(resolved.engine, OpenAICompatibleEngine)
    assert resolved.engine.api_key == "sk-or-1"
    # claude-cli (the default) is untouched — routing an unrelated kind still hits it.
    assert app.state.engines.resolve("tailor").name == "claude-cli"


def test_routing_to_unconfigured_engine_stays_unresolved(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    # Route to a provider we never saved → no engine → resolve None (the wrapper
    # then raises EngineNotConfiguredError; never a silent fallback).
    client.post(
        "/api/settings",
        headers=AUTH,
        json={"engine_routing": {"score": {"engine": "openai", "model": "gpt-5"}}},
    )
    assert app.state.engines.resolve("score") is None


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_removes_and_404s_when_absent(app_client: tuple[FastAPI, TestClient]) -> None:
    app, client = app_client
    client.post("/api/engines", headers=AUTH, json={"provider": "anthropic", "key": SECRET_KEY})
    assert client.delete("/api/engines/anthropic", headers=AUTH).status_code == 204
    remaining = client.get("/api/engines", headers=AUTH).json()
    assert not any(e["engine"] == "anthropic" for e in remaining)
    # Second delete → 404.
    assert client.delete("/api/engines/anthropic", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# claude-cli live verify — env-guarded (real subprocess, real subscription)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("FYJ_LIVE_E2E"),
    reason="live claude-cli verify — set FYJ_LIVE_E2E=1 to run (real subprocess)",
)
def test_verify_claude_cli_live(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post("/api/engines/verify", headers=AUTH, json={"provider": "claude-cli"})
    assert resp.status_code == 200
    assert resp.json()["provider"] == "claude-cli"
