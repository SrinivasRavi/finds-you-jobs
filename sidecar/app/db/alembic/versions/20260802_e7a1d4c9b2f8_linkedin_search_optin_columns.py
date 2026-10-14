"""linkedin_search_enabled and linkedin_search_ack_at as typed columns.

The job search hard-403s on these, and in the `ui_state` blob a frontend rename
could flip a safety gate. Backfilled from the blob; enforced config gets a column.

Revision ID: e7a1d4c9b2f8
Revises: d5f9c3a8e21b
Create Date: 2026-08-02
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7a1d4c9b2f8'
down_revision: str | None = 'd5f9c3a8e21b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('user_preferences', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'linkedin_search_enabled', sa.Boolean(), nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column('linkedin_search_ack_at', sa.DateTime(), nullable=True)
        )

    # Backfill from the ui_state blob (the ack is a JS ISO string with a
    # trailing Z; the column stores naive UTC per the UTCDateTime convention).
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, ui_state FROM user_preferences")).fetchall()
    for row_id, raw in rows:
        try:
            ui = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            continue
        enabled = bool(ui.get("linkedin_search_enabled"))
        ack = ui.get("linkedin_search_ack_at")
        ack_dt = None
        if isinstance(ack, str) and ack:
            try:
                parsed = datetime.fromisoformat(ack.replace("Z", "+00:00"))
                ack_dt = parsed.astimezone(UTC).replace(tzinfo=None)
            except ValueError:
                ack_dt = None
        conn.execute(
            sa.text(
                "UPDATE user_preferences SET linkedin_search_enabled = :e, "
                "linkedin_search_ack_at = :a WHERE id = :i"
            ),
            {"e": enabled, "a": ack_dt, "i": row_id},
        )


def downgrade() -> None:
    with op.batch_alter_table('user_preferences', schema=None) as batch_op:
        batch_op.drop_column('linkedin_search_ack_at')
        batch_op.drop_column('linkedin_search_enabled')
