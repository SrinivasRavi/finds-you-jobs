"""Covers: the chat agent as a caller of its own (audit-2026-10-14.md section 8.11).

The agent's actions run only where the user's autopilot switches allow, like any
background caller, and its AI work waits in the user's fast lane. A route learns
its caller from a context variable that only in-process code can set."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from sidecar.app.registry.autonomy import as_caller, caller, refusal
from sidecar.app.runner.policy import DEFAULT_POLICY

from ._support import AUTH


def _prefs(**on: bool) -> SimpleNamespace:
    base = {"autopilot_invite": False, "autopilot_dm": False, "autopilot_apply": False}
    return SimpleNamespace(**{**base, **on})


@pytest.mark.parametrize("action", ["invite", "dm", "apply", "linkedin_read"])
def test_the_agent_is_refused_while_the_action_is_confirm(action: str) -> None:
    reason = refusal(_prefs(), action, "agent")
    assert reason is not None and "autopilot is off" in reason


def test_the_agent_runs_what_autopilot_allows() -> None:
    on = _prefs(autopilot_invite=True, autopilot_dm=True, autopilot_apply=True)
    for action in ("invite", "dm", "apply", "linkedin_read"):
        assert refusal(on, action, "agent") is None
    assert refusal(on, "view", "agent") is not None


def test_the_agents_ai_work_waits_in_the_users_lane() -> None:
    assert DEFAULT_POLICY.group_for("score", "agent") == DEFAULT_POLICY.group_for("score", "user")


def test_the_caller_is_the_user_unless_in_process_code_says_otherwise() -> None:
    assert caller() == "user"
    with as_caller("agent"):
        assert caller() == "agent"
    assert caller() == "user"


def test_a_route_called_by_the_agent_records_the_agent(app_client) -> None:  # noqa: ANN001
    app, client = app_client

    async def call() -> str:
        with as_caller("agent"):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
                r = await c.post("/api/operations/cleanup_trash", headers=AUTH, json={})
        return r.json()["id"]

    agent_op = client.portal.call(call)
    user_op = client.post("/api/operations/cleanup_trash", headers=AUTH, json={}).json()["id"]
    with app.state.db.repos() as repos:
        assert repos.operations.get(agent_op).requested_by == "agent"
        assert repos.operations.get(user_op).requested_by == "user"


def test_the_agent_cannot_press_submit_while_submit_is_confirm(app_client) -> None:  # noqa: ANN001
    """Submit creates no op, so the route itself checks the caller."""
    app, client = app_client

    async def call() -> httpx.Response:
        with as_caller("agent"):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
                return await c.post("/api/apply-runs/no-such-run/submit", headers=AUTH)

    refused = client.portal.call(call)
    assert refused.status_code == 403 and "autopilot is off" in refused.json()["detail"]
    assert client.post("/api/apply-runs/no-such-run/submit", headers=AUTH).status_code == 404
