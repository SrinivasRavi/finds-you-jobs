"""The `chat` op: 1 turn of the chat agent (audit-2026-10-14.md section 8.11).

AGPL-3.0-only. The user's message is already stored; this op asks the routed
model, runs its tool calls as caller `agent`, and stores each message as it
lands, so the panel shows the steps while they run."""

from __future__ import annotations

import json
from typing import Any

from .operations import OperationContext, OperationOutcome, _require_engine, llm_outcome


def history(messages: list[Any]) -> list[dict[str, Any]]:
    """Stored rows as the loop reads them: the role beside the payload."""
    return [{"role": m.role, **(m.payload or {})} for m in messages]


def chat_entrypoint(ctx: OperationContext) -> OperationOutcome:
    from ..chat import dispatch
    from ..chat.agent import SYSTEM, run_turn
    from ..chat.tools import catalog

    resolved = _require_engine(ctx)
    if ctx.db is None or dispatch.APP is None:
        raise RuntimeError("chat needs the database and the serving app")
    db, chat_id = ctx.db, ctx.input_snapshot["chat_id"]
    with db.repos() as repos:
        past = history(repos.chats.messages(chat_id))

    def store(m: dict[str, Any]) -> None:
        role = m["role"]
        with db.repos() as repos:
            repos.chats.add_message(
                chat_id, role, {k: v for k, v in m.items() if k != "role"},
                operation_id=ctx.operation_id,
            )

    system = SYSTEM + json.dumps(catalog(dispatch.APP.openapi()), separators=(",", ":"))
    new, usage = run_turn(
        resolved.engine, system, past, dispatch.call,
        cancelled=ctx.cancelled, on_message=store,
    )
    return llm_outcome({"chat_id": chat_id, "messages": len(new)}, usage=usage,
                       resolved=resolved)
