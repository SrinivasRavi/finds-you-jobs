"""Runs a chat agent's tool call through the app's own route (section 8.11, step 4).

AGPL-3.0-only. The call goes through the ASGI app on the serving loop, as caller
`agent`, so the route and the op it queues apply the same checks a click meets.
A send, a LinkedIn read or a Submit the user keeps on "confirm each" never
reaches the route: it comes back `needs_confirmation`, and the chat shows a card.
The card's click runs the same call as `user`."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from ..registry.autonomy import as_caller, refusal
from .tools import TOOLS, Route, routes

# Bound at boot (main.py): the app and the loop that serves it.
APP: Any = None
LOOP: asyncio.AbstractEventLoop | None = None
CALL_TIMEOUT_S = 120.0

# The autonomy actions a tool needs before it may run unattended. A tool needing
# 2 (reach_out sends invites or messages) asks for a click unless both are on.
_ACTIONS: dict[str, tuple[str, ...]] = {
    "start_apply": ("apply",),
    "submit_apply_run": ("apply",),
    "reach_out": ("invite", "dm"),
    "discover_referrals": ("linkedin_read",),
}


def bind(app: Any, loop: asyncio.AbstractEventLoop) -> None:
    global APP, LOOP
    APP, LOOP = app, loop


def _routes() -> dict[str, Route]:
    return routes(APP.openapi())


def needs_click(prefs: Any, tool: str) -> str | None:
    """Why the agent may not run `tool` unattended, or None if it may."""
    for action in _ACTIONS.get(tool, ()):
        reason = refusal(prefs, action, "agent")
        if reason is not None:
            return reason
    return None


def _request(tool: str, args: dict[str, Any]) -> tuple[str, str, dict, Any] | str:
    """(method, path, query, body), or the reason the call can't be built."""
    spec = TOOLS.get(tool)
    if spec is None:
        return f"unknown tool {tool!r}"
    route = _routes()[spec.endpoint]
    values = {**{k: v for k, v in args.items() if k != "body"}, **spec.fixed}
    path = route.path
    for name, where in route.params.items():
        if where == "path":
            if name not in values:
                return f"missing argument {name!r}"
            # Encoded, so a value like "../settings?x=" can't reach another route.
            path = path.replace("{" + name + "}", quote(str(values[name]), safe=""))
    query = {k: v for k, v in values.items() if route.params.get(k) == "query"}
    return route.method, path, query, args.get("body")


async def _send(method: str, path: str, query: dict, body: Any, who: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {APP.state.token}"}
    with as_caller(who):
        transport = httpx.ASGITransport(app=APP)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as c:
            r = await c.request(method, path, params=query, json=body, headers=headers,
                                timeout=CALL_TIMEOUT_S)
    try:
        data = r.json()
    except ValueError:
        data = r.text[:2000]
    if r.status_code >= 400:
        detail = data.get("detail") if isinstance(data, dict) else data
        return {"status": r.status_code, "error": detail}
    return {"status": r.status_code, "data": data}


def call(tool: str, args: dict[str, Any], *, who: str = "agent") -> dict[str, Any]:
    """Run 1 tool call from a worker thread and wait for its answer."""
    if APP is None or LOOP is None:
        return {"error": "the app isn't serving"}
    if who == "agent":
        with APP.state.db.repos() as repos:
            reason = needs_click(repos.preferences.get_or_create(), tool)
        if reason is not None:
            return {"needs_confirmation": True, "reason": reason}
    built = _request(tool, args)
    if isinstance(built, str):
        return {"error": built}
    future = asyncio.run_coroutine_threadsafe(_send(*built, who), LOOP)
    result = future.result(timeout=CALL_TIMEOUT_S + 5)
    if who == "agent" and result.get("status") == 403:
        return {"needs_confirmation": True, "reason": result.get("error", "")}
    return result
