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


def test_a_turn_that_fails_says_why(app_client) -> None:  # noqa: ANN001
    app, client = app_client
    _route(app, {"no": "say or call"}, {"no": 1}, {"no": 2})  # 3 unreadable replies
    chat_id = client.post("/api/chats", headers=AUTH, json={"text": "hi"}).json()["chat_id"]
    chat = _settled(client, chat_id)
    assert chat["error"] and "a reply needs a message or a call" in chat["error"]
    assert [m["role"] for m in chat["messages"]] == ["user"]


def test_the_message_is_stored_before_its_turn_is_queued(app_client, monkeypatch) -> None:  # noqa: ANN001
    """The runner may start a turn the instant it's queued; the turn must see the
    message it answers (an e2e caught a turn reading an empty chat)."""
    app, client = app_client
    _route(app, {"say": "ok"})
    seen: list[list[str]] = []
    submit = app.state.runner.submit

    def watching(kind, snapshot, **kw):  # noqa: ANN001, ANN003, ANN202
        with app.state.db.repos() as repos:
            seen.append([m.role for m in repos.chats.messages(snapshot["chat_id"])])
        return submit(kind, snapshot, **kw)

    monkeypatch.setattr(app.state.runner, "submit", watching)
    sent = client.post("/api/chats", headers=AUTH, json={"text": "hi"}).json()
    assert seen == [["user"]]
    chat = _settled(client, sent["chat_id"])
    assert chat["messages"][0]["payload"]["text"] == "hi"


def test_a_path_argument_cannot_reach_another_route(app_client) -> None:  # noqa: ANN001
    """A posting's text could tell the model to pass "../settings?x=" as an id."""
    app, client = app_client
    from sidecar.app.chat import dispatch

    before = client.get("/api/settings", headers=AUTH).json()["preferences"]["autopilot_apply"]
    result = dispatch.call("tombstone_job", {"job_id": "../settings?x=",
                                             "body": {"autopilot_apply": True}})
    assert result["status"] in (404, 405, 422), result
    after = client.get("/api/settings", headers=AUTH).json()["preferences"]["autopilot_apply"]
    assert before is after is False


def test_a_card_whose_call_raises_lands_failed(app_client, monkeypatch) -> None:  # noqa: ANN001
    app, client = app_client
    from sidecar.app.chat import dispatch

    def boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise TimeoutError("slow")

    monkeypatch.setattr(dispatch, "call", boom)
    with app.state.db.repos() as repos:
        chat_id = repos.chats.create("t").id
        card_id = repos.chats.add_message(chat_id, "confirm", {
            "tool": "list_applications", "args": {}, "reason": "r", "state": "pending"}).id
    done = client.post(f"/api/chats/{chat_id}/cards/{card_id}/confirm", headers=AUTH)
    assert done.status_code == 200 and done.json()["payload"]["state"] == "failed"
