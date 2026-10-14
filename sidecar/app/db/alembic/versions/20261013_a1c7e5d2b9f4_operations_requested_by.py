"""Record who started each operation, so a user's request never queues behind the app's.

The runner orders queued rows by it and picks a queue lane from it
(docs/internal/queueing.md section 4, audit A1). Every existing row becomes `app`.

Revision ID: a1c7e5d2b9f4
Revises: f7c2d4e9a1b6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c7e5d2b9f4"
down_revision = "f7c2d4e9a1b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.add_column(
            sa.Column("requested_by", sa.String(), nullable=False, server_default="app")
        )
        batch.create_index(
            "ix_operations_state_requested_created",
            ["state", "requested_by", "created_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("operations") as batch:
        batch.drop_index("ix_operations_state_requested_created")
        batch.drop_column("requested_by")
