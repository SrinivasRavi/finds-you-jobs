"""Per-job scoring attempt memory, so a failed score can be retried safely.

The planner used to exclude any job with a `failed` score operation at the
current profile version (`_ATTEMPTED` in `scheduler/planner.py`), which meant a
fan-out that died on an expired key or exhausted tokens marked every remaining
job failed within seconds and NONE of them were ever re-planned. Topping the key
up re-scored nothing.

Dropping `failed` from that exclusion needs somewhere to bound the retries, or a
job that keeps failing against a live provider would be re-planned on every tick
forever. These 2 columns are that bound: `score_attempts` counts only failures
where a call actually REACHED the provider (a circuit-open rejection never did,
so it costs nothing and the job returns to the pool unmarked), and the planner
stops at 3. `score_last_error` carries the verbatim reason for the card.

No status column: every other scoring state is already derivable and a stored
copy could drift from it. `scored` is a `job_scores` row at `scorer-llm`,
`in_progress` is a live score operation, `unscorable` is a description under
`MIN_JD_CHARS`, and `pending` is the remainder.

Additive and backfill-free: existing rows default to 0 attempts, which is
exactly right — they have never been counted, so they all get a fresh 3.

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
