"""The resolved location becomes columns, and a score's factors get a table.

5 location columns filled at ingest (scoring.md 6.6); `location_remote` is
3-state. `job_score_factors` holds 1 row per axis per method per job (7.2);
weights stay computed. `llm_score_method` backfills "llm".

Revision ID: b4d8e2a7c531
Revises: f3a9c1d8e527
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b4d8e2a7c531"
down_revision = "f3a9c1d8e527"
branch_labels = None
depends_on = None

# (column, is_integer). Every one is nullable: the location columns because a
# row the resolver has not seen must be distinguishable from a resolved one.
_JOB_COLUMNS: tuple[tuple[str, bool], ...] = (
    ("location_country", False),
    ("location_admin1", False),
    ("location_city_id", True),
    ("location_remote", False),
    ("location_attendance", False),
    ("llm_score_method", False),
)


def upgrade() -> None:
    for name, is_integer in _JOB_COLUMNS:
        kind = sa.Integer() if is_integer else sa.String()
        op.add_column("jobs", sa.Column(name, kind, nullable=True))

    op.execute(
        sa.text(
            "UPDATE jobs SET llm_score_method = 'llm' "
            "WHERE llm_score IS NOT NULL AND llm_score_method IS NULL"
        )
    )

    op.create_table(
        "job_score_factors",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("factor", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False, server_default="graded"),
        sa.Column("evidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("job_id", "method", "factor", name="uq_job_score_factor"),
    )


def downgrade() -> None:
    op.drop_table("job_score_factors")
    for name, _is_integer in reversed(_JOB_COLUMNS):
        op.drop_column("jobs", name)
