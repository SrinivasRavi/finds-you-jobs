"""Per-job scoring attempt memory, so a failed score can be retried safely.

Excluding failed jobs meant an expired key failed every job for good.
`score_attempts` counts only failures that reached the provider (the planner
stops at 3; existing rows start at 0); `score_last_error` is the reason.

Revision ID: a3d7e1f95c24
Revises: f8b3c6d9e4a2
Create Date: 2026-08-27
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a3d7e1f95c24'
down_revision: str | None = 'f8b3c6d9e4a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("score_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("jobs", sa.Column("score_last_error", sa.Text(), nullable=True))
    # The planner's eligibility read: active jobs still worth an attempt.
    op.create_index(
        "ix_jobs_feedstate_attempts", "jobs", ["feed_state", "score_attempts"]
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_feedstate_attempts", table_name="jobs")
    op.drop_column("jobs", "score_last_error")
    op.drop_column("jobs", "score_attempts")
