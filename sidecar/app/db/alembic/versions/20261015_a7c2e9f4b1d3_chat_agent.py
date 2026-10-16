"""2 tables for the chat agent: `chats` and `chat_messages`.

New tables only, so an upgrade changes no existing row (audit-2026-10-14.md
section 8.11).

Revision ID: a7c2e9f4b1d3
Revises: f3b8d1e6a2c4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c2e9f4b1d3"
down_revision = "f3b8d1e6a2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chats",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "chat_id", sa.String(), sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("operation_id", sa.String(), sa.ForeignKey("operations.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_messages_chat_created", "chat_messages", ["chat_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_chat_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chats")
