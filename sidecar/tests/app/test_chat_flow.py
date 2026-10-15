"""Covers: the chat agent end to end (audit-2026-10-14.md section 8.11, step 5).

The real app, runner and routes; a scripted engine stands in for the model."""

from __future__ import annotations

import json
import time
from typing import Any

from sidecar.modules._shared.claude_engine import EngineUsage

from ._support import AUTH


class Scripted:
    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies = [json.dumps(r) for r in replies]

    def complete(self, system: str, user: str) -> tuple[str, EngineUsage]:
        reply = self.replies.pop(0) if self.replies else '{"say": "done"}'
        return reply, EngineUsage(internal_calls=1, tokens_in=10, tokens_out=5, usd=0.01)


def _route(app, *replies: dict[str, Any]) -> None:  # noqa: ANN001
    app.state.engines.register("scripted", Scripted(*replies))
    app.state.engines.route("chat", engine="scripted")


def _settled(client, chat_id: str) -> dict:  # noqa: ANN001
    for _ in range(100):
        chat = client.get(f"/api/chats/{chat_id}", headers=AUTH).json()
        if not chat["busy"]:
            return chat
        time.sleep(0.05)
    raise AssertionError("the turn never finished")


def test_a_question_runs_a_tool_and_answers(app_client) -> None:  # noqa: ANN001
    app, client = app_client
    _route(app, {"call": {"tool": "list_applications", "args": {}}}, {"say": "You have 0."})
    sent = client.post("/api/chats", headers=AUTH, json={"text": "How many applications?"})
    assert sent.status_code == 202
    chat = _settled(client, sent.json()["chat_id"])
    assert [m["role"] for m in chat["messages"]] == ["user", "tool", "agent"]
    assert chat["messages"][1]["payload"]["result"]["status"] == 200
    assert chat["messages"][2]["payload"]["text"] == "You have 0."
    assert chat["title"] == "How many applications?"
    with app.state.db.repos() as repos:
        op = repos.operations.get(sent.json()["operation_id"])
        assert op.state == "succeeded" and op.requested_by == "user"
        assert op.usage["internal_calls"] == 2


def test_a_send_with_autopilot_off_becomes_a_card_the_user_answers(app_client) -> None:  # noqa: ANN001
    app, client = app_client
    _route(app, {"call": {"tool": "reach_out", "args": {"body": {"contact_ids": ["c"]}}}},
           {"say": "Please confirm the card."})
    chat_id = client.post("/api/chats", headers=AUTH, json={"text": "invite c"}).json()["chat_id"]
    chat = _settled(client, chat_id)
    card = next(m for m in chat["messages"] if m["role"] == "confirm")
    assert card["payload"]["state"] == "pending"
    assert "autopilot is off" in card["payload"]["reason"]
    with app.state.db.repos() as repos:  # nothing was queued to send
        assert not [o for o in repos.operations.list_recent(50) if o.kind == "send"]

    url = f"/api/chats/{chat_id}/cards/{card['id']}"
    assert client.post(f"{url}/dismiss", headers=AUTH).json()["payload"]["state"] == "dismissed"
    assert client.post(f"{url}/confirm", headers=AUTH).status_code == 409


def test_a_confirmed_card_runs_its_call_as_the_user(app_client) -> None:  # noqa: ANN001
    app, client = app_client
    with app.state.db.repos() as repos:
        chat_id = repos.chats.create("t").id
        card = repos.chats.add_message(chat_id, "confirm", {
            "tool": "list_applications", "args": {}, "reason": "r", "state": "pending"})
        card_id = card.id
    done = client.post(f"/api/chats/{chat_id}/cards/{card_id}/confirm", headers=AUTH).json()
    assert done["payload"]["state"] == "done"
    assert done["payload"]["result"]["status"] == 200
