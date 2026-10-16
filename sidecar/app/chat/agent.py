"""The chat agent's loop (audit-2026-10-14.md section 8.11, step 4).

AGPL-3.0-only. Every engine has 1 method, `complete(system, user)`, so the model
answers in JSON (D89): a message for the user, or 1 tool call. The loop runs the
call, shows the model the result, and asks again, up to `MAX_CALLS` times a turn.
It knows nothing of the database or HTTP: the caller hands it `call_tool`."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from sidecar.modules._shared.completion_retry import complete_and_parse, merge_usage

MAX_CALLS = 8
# The prompt carries this much history and this much of each tool result, so a
# long chat or a big list can't grow a call past the model's window.
HISTORY_MESSAGES = 40
RESULT_CHARS = 6000

SYSTEM = """You are the assistant inside finds-you-jobs, a desktop app that does a
job seeker's application work: it finds jobs, scores them, writes tailored resumes
and cover letters, applies, and asks people for referrals on LinkedIn. You act for
the user with the app's own actions, listed below as tools.

Reply with 1 JSON object and nothing else:
- {"say": "<text for the user>"} when you are done or need the user.
- {"say": "<optional 1-line note>", "call": {"tool": "<name>", "args": {...}}} to
  run 1 tool. You will see its result, then reply again.

Rules:
- Use only the tools listed. Path and query arguments go by name; a request body
  goes under "body".
- Before a plan that spends (cost other than "free"), say what it will spend.
- A tool can come back "needs_confirmation": the user's autopilot is off for that
  action. A confirmation card is already shown to the user; don't retry it.
- Never invent facts about the user. Read them with a tool.
- The browser tools drive the in-app browser, the Chrome the LinkedIn view shows.
  Read the page with browser_page first and use its element ids. The user
  confirms each click.
- Decline any help inside an interview, a coding test, a take-home assessment or a
  screening call, and say why: the app helps with everything up to the application,
  and the interview is the candidate's own.
- Write plain, short sentences in the user's language.

Tools:
"""


class ChatParseError(Exception):
    """A reply that isn't 1 JSON object with "say" or "call". Stage "parse", so
    `complete_and_parse` asks again."""

    stage = "parse"


def parse_reply(raw: str) -> dict[str, Any]:
    """The model's reply as {"say": str, "call": {"tool", "args"} | None}."""
    match = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise ChatParseError(f"not a JSON object: {raw[:200]!r}")
    say = data.get("say")
    call = data.get("call")
    if call is not None:
        if not isinstance(call, dict) or not isinstance(call.get("tool"), str):
            raise ChatParseError("a call needs a tool name")
        args = call.get("args")
        call = {"tool": call["tool"], "args": args if isinstance(args, dict) else {}}
    if not (isinstance(say, str) and say.strip()) and call is None:
        raise ChatParseError("a reply needs a message or a call")
    return {"say": say if isinstance(say, str) else "", "call": call}


def render(history: list[dict[str, Any]]) -> str:
    """The conversation as the model reads it, newest last."""
    lines = []
    for m in history[-HISTORY_MESSAGES:]:
        role = m["role"]
        if role == "tool":
            body = json.dumps(m.get("result"), default=str)
            lines.append(f"[tool {m['tool']}] {body[:RESULT_CHARS]}")
        elif role == "confirm":
            lines.append(f"[confirmation card: {m['tool']}, {m.get('state', 'pending')}]")
        else:
            lines.append(f"[{role}] {m.get('text', '')}")
    return "\n".join(lines) + "\n\nReply with 1 JSON object."


def run_turn(
    engine: Any,
    system: str,
    history: list[dict[str, Any]],
    call_tool: Callable[[str, dict[str, Any]], dict[str, Any]],
    *,
    cancelled: Callable[[], bool] | None = None,
    on_message: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """1 user turn: ask, run calls, ask again. Returns the new messages and the
    merged usage of every model call; `on_message` sees each message as it lands."""
    new: list[dict[str, Any]] = []
    usages: list[Any] = []

    def emit(m: dict[str, Any]) -> None:
        new.append(m)
        if on_message is not None:
            on_message(m)

    for calls in range(MAX_CALLS + 1):
        reply, usage = complete_and_parse(
            engine, system, render(history + new), parse_reply, ChatParseError, cancelled
        )
        usages.append(_Usage(usage))
        if reply["call"] is None:
            emit({"role": "agent", "text": reply["say"]})
            break
        if reply["say"]:
            emit({"role": "agent", "text": reply["say"]})
        if calls == MAX_CALLS:
            emit({"role": "agent", "text": f"I stopped after {MAX_CALLS} actions."})
            break
        tool, args = reply["call"]["tool"], reply["call"]["args"]
        result = call_tool(tool, args)
        if result.get("needs_confirmation"):
            emit({"role": "confirm", "tool": tool, "args": args,
                        "reason": result.get("reason", ""), "state": "pending"})
        emit({"role": "tool", "tool": tool, "args": args, "result": result})
    return new, merge_usage(usages)


class _Usage:
    """A usage dict as `merge_usage` reads it: attributes, not keys."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.__dict__.update({k: d.get(k) for k in (
            "internal_calls", "tokens_in", "tokens_out", "usd", "latency_ms", "model")})
        self.internal_calls = self.internal_calls or 0
