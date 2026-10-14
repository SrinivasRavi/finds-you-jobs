"""A pair of jobs the user said are not the same posting (url-less-jobs.md 9.6).

A table, not `trust_flags` JSON: a pair is symmetric, and an id in a blob has no
FK, so deleting a job would leave a stale prompt. The ordered pair
(`low_job_id` < `high_job_id`) gives 1 write site and 1 read; detection runs at
ingest, so the board reads this only for a flagged pair.

Revision ID: d1c5f83a9b27
Revises: c9e4a71b3f02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1c5f83a9b27"
down_revision = "c9e4a71b3f02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_duplicate_dismissals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "low_job_id",
            sa.String(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "high_job_id",
            sa.String(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("low_job_id", "high_job_id", name="uq_job_duplicate_pair"),
    )
    # The read is "is this pair dismissed", always from the low side first, so
    # the unique constraint above is also the index that answers it. A second
    # index on `high_job_id` serves a cascade delete arriving from that side.
    op.create_index(
        "ix_job_duplicate_dismissals_high", "job_duplicate_dismissals", ["high_job_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_job_duplicate_dismissals_high", table_name="job_duplicate_dismissals")
    op.drop_table("job_duplicate_dismissals")
