"""Engagement splits into 2 columns: who owes the next message (S-N5).

`pending_our_response` (their message is last) and `pending_their_response`
(they've replied before, ours is last). Rows re-home by the stored
`last_thread_message.direction`; no stamp waits on them. No new column.

Revision ID: f3a9c1d8e527
Revises: e7b1c95d2a48
Create Date: 2026-09-20
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "f3a9c1d8e527"
down_revision = "e7b1c95d2a48"
branch_labels = None
depends_on = None


def _rows(conn: sa.Connection, status: str) -> list[tuple[str, str]]:
    result = conn.execute(
        sa.text(
            "SELECT id, COALESCE(profile_payload, '') FROM contacts "
            "WHERE connection_status = :status"
        ),
        {"status": status},
    )
    return [(str(r[0]), str(r[1])) for r in result]


def _direction(payload_json: str) -> str:
    """The stored direction of the last thread message, or "" when unknown."""
    if not payload_json:
        return ""
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    last = payload.get("last_thread_message")
    if not isinstance(last, dict):
        return ""
    direction = last.get("direction")
    return direction if isinstance(direction, str) else ""


def _set_status(conn: sa.Connection, contact_id: str, status: str) -> None:
    conn.execute(
        sa.text("UPDATE contacts SET connection_status = :status WHERE id = :id"),
        {"status": status, "id": contact_id},
    )


def _stamp_first_reply(conn: sa.Connection, contact_id: str, payload_json: str) -> None:
    """Write the sticky `first_replied_at` for a row we know has replied.

    Its value is the best evidence the row carries: the last thread message's
    timestamp, else the acceptance, else the send. Sticky by construction —
    once set, `accepted` is unreachable for this contact."""
    try:
        payload = json.loads(payload_json) if payload_json else {}
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("first_replied_at"):
        return
    last = payload.get("last_thread_message")
    stamp = last.get("at") if isinstance(last, dict) else None
    if not stamp:
        row = conn.execute(
            sa.text("SELECT accepted_at, sent_at FROM contacts WHERE id = :id"),
            {"id": contact_id},
        ).first()
        stamp = (row[0] or row[1]) if row is not None else None
    if not stamp:
        return
    payload["first_replied_at"] = str(stamp)
    conn.execute(
        sa.text("UPDATE contacts SET profile_payload = :payload WHERE id = :id"),
        {"payload": json.dumps(payload), "id": contact_id},
    )


def upgrade() -> None:
    conn = op.get_bind()
    for contact_id, payload_json in _rows(conn, "engagement"):
        owed_by_us = _direction(payload_json) == "them"
        _set_status(
            conn,
            contact_id,
            "pending_our_response" if owed_by_us else "pending_their_response",
        )
        _stamp_first_reply(conn, contact_id, payload_json)


def downgrade() -> None:
    conn = op.get_bind()
    for status in ("pending_our_response", "pending_their_response"):
        for contact_id, _payload in _rows(conn, status):
            _set_status(conn, contact_id, "engagement")
