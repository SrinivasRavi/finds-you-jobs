"""Covers: the chat agent's loop (audit-2026-10-14.md section 8.11, step 4).

A scripted engine stands in for the model: 0 real calls."""

from __future__ import annotations

import json
from typing import Any

import pytest

from sidecar.app.chat.agent import MAX_CALLS, ChatParseError, parse_reply, run_turn
from sidecar.modules._shared.claude_engine import EngineUsage


class Scripted:
    def __init__(self, *replies: str) -> None:
        self.replies, self.prompts = list(replies), []

    def complete(self, system: str, user: str) -> tuple[str, EngineUsage]:
        self.prompts.append(user)
        reply = self.replies.pop(0) if self.replies else self.replies_last
        self.replies_last = reply
        return reply, EngineUsage(internal_calls=1, tokens_in=10, tokens_out=5, usd=0.01)


def _call(tool: str, **args: Any) -> str:
    return json.dumps({"say": "", "call": {"tool": tool, "args": args}})


def test_a_reply_is_a_message_or_a_call() -> None:
    assert parse_reply('{"say": "hi"}') == {"say": "hi", "call": None}
    got = parse_reply('Sure:\n```json\n{"call": {"tool": "get_job", "args": {"job_id": "j"}}}\n```')
    assert got["call"] == {"tool": "get_job", "args": {"job_id": "j"}}
    for bad in ("no json", "{}", '{"call": {"args": {}}}', '{"say": ""}'):
        with pytest.raises(ChatParseError):
            parse_reply(bad)


def test_a_call_runs_and_the_model_reads_its_result() -> None:
    engine = Scripted(_call("list_jobs"), '{"say": "You have 2 jobs."}')
    calls: list[tuple[str, dict]] = []

    def tool(name: str, args: dict) -> dict:
        calls.append((name, args))
        return {"status": 200, "data": [{"title": "SWE"}, {"title": "SRE"}]}

    new, usage = run_turn(engine, "sys", [{"role": "user", "text": "my jobs?"}], tool)
    assert calls == [("list_jobs", {})]
    assert "SRE" in engine.prompts[1]  # the 2nd ask carries the result
    assert [m["role"] for m in new] == ["tool", "agent"]
    assert new[-1]["text"] == "You have 2 jobs."
    assert usage["internal_calls"] == 2 and usage["usd"] == pytest.approx(0.02)


def test_a_refused_action_becomes_a_confirmation_card() -> None:
    engine = Scripted(_call("reach_out", body={"x": 1}), '{"say": "Confirm the card."}')
    refused = {"needs_confirmation": True, "reason": "autopilot is off for a LinkedIn invite"}
    history = [{"role": "user", "text": "invite them"}]
    new, _ = run_turn(engine, "sys", history, lambda n, a: refused)
    card = next(m for m in new if m["role"] == "confirm")
    assert card["tool"] == "reach_out" and card["args"] == {"body": {"x": 1}}
    assert card["state"] == "pending"


def test_a_turn_stops_after_the_call_cap() -> None:
    engine = Scripted(_call("list_jobs"))  # calls forever
    count = 0

    def tool(name: str, args: dict) -> dict:
        nonlocal count
        count += 1
        return {"status": 200, "data": []}

    new, _ = run_turn(engine, "sys", [{"role": "user", "text": "loop"}], tool)
    assert count == MAX_CALLS
    assert new[-1]["text"] == f"I stopped after {MAX_CALLS} actions."
