"""The resolved location becomes columns, and a score's factors get a table.

Two changes, both from `docs/internal/scoring.md` sections 6.6 and 7.2.

1. `jobs` gains 5 location columns, filled at ingest by `modules/geo/resolve.py`.
   The location field is 1 free-text string that 3 consumers parse differently
   today, and the measured result is that discovery's matcher calls 114 of 115
   rows a location match, 41 of them wrongly. `location_remote` is deliberately
   3-state: NULL for a row the resolver has not seen (which is every existing
   row), "" for resolved and not remote, else the scope. No index: the queries
   that would read these aren't written yet (D30).

   The match verdict is NOT a column. It depends on the user's preferences, which
   change, so it is computed on read from these facts about the posting.

2. `job_score_factors` holds one row per axis per method per job, which is what
   the per-factor bars, user weights and honest method-versus-method comparison
   all wait on. The weight applied is not stored, because a weight is a
   preference and the total is computed from the factors on read.

   This is not a return of `job_scores`, which `d2f7a91b8c46` collapsed into
   columns 4 weeks ago. That table's 3-part key carried a `profile_version`
   dimension that distinguished nothing across 1,238 rows. A factor table is a
   different cardinality, one row per axis, every column of it queried.

   `jobs.llm_score_method` rides along, because 4 methods write that one score
   slot and the board has to say which one did. Existing scored rows backfill to
   "llm": the single LLM path is the only thing that could have written them.

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
