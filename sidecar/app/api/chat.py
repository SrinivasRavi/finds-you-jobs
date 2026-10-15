"""The chat agent's panel: send a message, read a chat, answer a confirmation card
(audit-2026-10-14.md section 8.11)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from ..db.models import OP_ACTIVE_STATES
from ..registry.autonomy import caller
from . import dto
from .deps import db as _db
from .deps import found as _found
from .deps import runner as _runner

router = APIRouter()
_TITLE_CHARS = 60


@router.get("/api/chats")
def list_chats(request: Request) -> list[dto.ChatSummaryDTO]:
    """The chats, most recent first."""
    with _db(request).repos() as repos:
        return [
            dto.ChatSummaryDTO(id=c.id, title=c.title, updated_at=c.updated_at)
            for c in repos.chats.list_recent()
        ]


@router.post("/api/chats", status_code=202)
def send_chat_message(request: Request, payload: dto.ChatSendRequest) -> dto.ChatSendAccepted:
    """Store the user's message and queue 1 agent turn. No `chat_id` starts a chat."""
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="the message is empty")
    db = _db(request)
    with db.repos() as repos:
        if payload.chat_id is None:
            chat_id = repos.chats.create(title=text[:_TITLE_CHARS]).id
        else:
            chat_id = _found(repos.chats.get(payload.chat_id), "chat", payload.chat_id).id
    operation_id = _runner(request).submit("chat", {"chat_id": chat_id}, requested_by=caller())
    with db.repos() as repos:
        repos.chats.add_message(chat_id, "user", {"text": text}, operation_id=operation_id)
    return dto.ChatSendAccepted(chat_id=chat_id, operation_id=operation_id)


@router.get("/api/chats/{chat_id}")
def get_chat(request: Request, chat_id: str) -> dto.ChatDTO:
    with _db(request).repos() as repos:
        chat = _found(repos.chats.get(chat_id), "chat", chat_id)
        messages = repos.chats.messages(chat_id)
        turns = [m.operation_id for m in messages if m.role == "user" and m.operation_id]
        last = repos.operations.get(turns[-1]) if turns else None
        return dto.ChatDTO(
            id=chat.id,
            title=chat.title,
            messages=[
                dto.ChatMessageDTO(id=m.id, role=m.role, payload=m.payload or {},
                                   created_at=m.created_at)
                for m in messages
            ],
            busy=last is not None and last.state in OP_ACTIVE_STATES,
        )


def _pending_card(request: Request, chat_id: str, message_id: str):  # type: ignore[no-untyped-def]
    if caller() != "user":  # a card is answered by a click, never by the agent
        raise HTTPException(status_code=403, detail="only the user answers a card")
    with _db(request).repos() as repos:
        msg = _found(repos.chats.get_message(message_id), "message", message_id)
        if msg.chat_id != chat_id or msg.role != "confirm":
            raise HTTPException(status_code=404, detail="no such confirmation card")
        if (msg.payload or {}).get("state") != "pending":
            raise HTTPException(status_code=409, detail="this card was already answered")
        return dict(msg.payload)


def _settle_card(request: Request, message_id: str, **change: object) -> dto.ChatMessageDTO:
    with _db(request).repos() as repos:
        msg = _found(repos.chats.get_message(message_id), "message", message_id)
        msg.payload = {**(msg.payload or {}), **change}
        return dto.ChatMessageDTO(id=msg.id, role=msg.role, payload=msg.payload,
                                  created_at=msg.created_at)


@router.post("/api/chats/{chat_id}/cards/{message_id}/confirm")
async def confirm_chat_card(request: Request, chat_id: str, message_id: str) -> dto.ChatMessageDTO:
    """The user's click on a card: run the card's call as the user, once."""
    from ..chat import dispatch

    card = _pending_card(request, chat_id, message_id)
    _settle_card(request, message_id, state="running")
    result = await asyncio.to_thread(dispatch.call, card["tool"], card.get("args") or {},
                                     who="user")
    state = "failed" if "error" in result else "done"
    return _settle_card(request, message_id, state=state, result=result)


@router.post("/api/chats/{chat_id}/cards/{message_id}/dismiss")
def dismiss_chat_card(request: Request, chat_id: str, message_id: str) -> dto.ChatMessageDTO:
    _pending_card(request, chat_id, message_id)
    return _settle_card(request, message_id, state="dismissed")
