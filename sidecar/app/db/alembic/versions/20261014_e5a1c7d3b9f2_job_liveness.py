"""4 liveness columns on `jobs`, so a closed posting stops costing tailoring.

`source_board` and `last_seen_at`: the company board a complete scan last listed
the job on, and when. `closed_at`: when that board stopped listing it, or the daily
read found it closed. `liveness_checked_at`: the daily read of a job no board lists.
All 4 start NULL; the next scan and the next daily tick fill them
(audit-2026-10-14.md section 8.7).

Revision ID: e5a1c7d3b9f2
Revises: d2b6f9a4c8e1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5a1c7d3b9f2"
down_revision = "d2b6f9a4c8e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("source_board", sa.String(), nullable=True))
        batch.add_column(sa.Column("last_seen_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("liveness_checked_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_jobs_source_board", ["source_board"])
        batch.create_index("ix_jobs_liveness_checked", ["liveness_checked_at"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_liveness_checked")
        batch.drop_index("ix_jobs_source_board")
        batch.drop_column("liveness_checked_at")
        batch.drop_column("closed_at")
        batch.drop_column("last_seen_at")
        batch.drop_column("source_board")
