"""A pair of jobs the user said are NOT the same posting.

The duplicate prompt (url-less-jobs.md 9.6) asks once and must never ask again
after "They're different". D15 left where that lives open, and the 2 candidates
were a `trust_flags` entry on one of the jobs, or this table.

**Why a table rather than the JSON.** A dismissal is a fact about a PAIR, and
neither half of a pair is its home:

- A `trust_flags` entry naming the other job's id has no foreign key, so
  deleting either job leaves a dangling id inside the surviving row's blob and
  `delete_job_cascade` can't reach it. That is a stale prompt waiting to fire on
  whatever row later takes that id's place.
- The pair is symmetric and a single row's list is not, so dismissing from job
  A's card and from job B's card are 2 write sites and 2 readers that drift.

The ordered pair (`low_job_id` < `high_job_id`) makes one row answer both
directions, so there is exactly one write site and one read.

**Why it costs the board nothing.** Near-duplicate detection happens at INGEST,
not at render: the new row gets a `probable-duplicate:<id>` trust flag, and
`trust_flags` is a column the board already loads. So a page with no flagged
pair never touches this table at all, and a page with one pays a single batched
indexed read.

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
