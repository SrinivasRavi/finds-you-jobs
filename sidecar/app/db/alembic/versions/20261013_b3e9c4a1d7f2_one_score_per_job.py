"""1 score per job, its factors on the row, and the resume edit it predates (audit A3).

The 2 rating column sets (`llm_*`, `keyword_*`) become 1 (`score_*`), copied from the
real score where one exists, else the keyword floor. The shown method's factor rows
move into `jobs.score_factors`; the other methods' rows are dropped, as
docs/internal/score-storage.md section 4 decides. `scored_profile_version` is unknown
for old rows (NULL: never marked stale). `master_profiles.major_version` starts at 0.

Revision ID: b3e9c4a1d7f2
Revises: a1c7e5d2b9f4
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "b3e9c4a1d7f2"
down_revision = "a1c7e5d2b9f4"
branch_labels = None
depends_on = None

_OLD = (
    ("llm_score", sa.Integer(), None),
    ("llm_reasons", sa.JSON(), "[]"),
    ("llm_breakdown_md", sa.Text(), ""),
    ("keyword_score", sa.Integer(), None),
    ("keyword_reasons", sa.JSON(), "[]"),
    ("keyword_breakdown_md", sa.Text(), ""),
    ("llm_score_method", sa.String(), None),
)


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("score", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("score_method", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("score_reasons", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(
            sa.Column("score_breakdown_md", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column("score_factors", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.add_column(sa.Column("scored_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("scored_profile_version", sa.Integer(), nullable=True))
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE jobs SET score = llm_score, score_method = COALESCE(llm_score_method, 'llm'), "
        "score_reasons = llm_reasons, score_breakdown_md = llm_breakdown_md "
        "WHERE llm_score IS NOT NULL"
    ))
    conn.execute(sa.text(
        "UPDATE jobs SET score = keyword_score, score_method = 'keyword', "
        "score_reasons = keyword_reasons, score_breakdown_md = keyword_breakdown_md "
        "WHERE llm_score IS NULL AND keyword_score IS NOT NULL"
    ))
    rows = conn.execute(sa.text(
        "SELECT f.job_id, f.factor, f.value, f.kind, f.evidence, f.created_at "
        "FROM job_score_factors f JOIN jobs j ON j.id = f.job_id "
        "WHERE f.method = j.score_method ORDER BY f.job_id, f.factor"
    )).fetchall()
    by_job: dict[str, tuple[list[dict], str]] = {}
    for job_id, factor, value, kind, evidence, created_at in rows:
        factors, newest = by_job.get(job_id, ([], ""))
        factors.append({"factor": factor, "value": value, "kind": kind, "evidence": evidence})
        by_job[job_id] = (factors, max(newest, str(created_at)))
    for job_id, (factors, newest) in by_job.items():
        conn.execute(
            sa.text("UPDATE jobs SET score_factors = :f, scored_at = :t WHERE id = :id"),
            {"f": json.dumps(factors), "t": newest, "id": job_id},
        )
    op.drop_table("job_score_factors")
    with op.batch_alter_table("jobs") as batch:
        for name, _type, _default in _OLD:
            batch.drop_column(name)
        batch.create_index("ix_jobs_feedstate_method", ["feed_state", "score_method"])
    with op.batch_alter_table("master_profiles") as batch:
        batch.add_column(
            sa.Column("major_version", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("master_profiles") as batch:
        batch.drop_column("major_version")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_feedstate_method")
        for name, type_, default in _OLD:
            batch.add_column(
                sa.Column(name, type_, nullable=default is None, server_default=default)
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
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE jobs SET llm_score = score, llm_score_method = score_method, "
        "llm_reasons = score_reasons, llm_breakdown_md = score_breakdown_md "
        "WHERE score IS NOT NULL AND score_method != 'keyword'"
    ))
    conn.execute(sa.text(
        "UPDATE jobs SET keyword_score = score, keyword_reasons = score_reasons, "
        "keyword_breakdown_md = score_breakdown_md WHERE score_method = 'keyword'"
    ))
    rows = conn.execute(sa.text(
        "SELECT id, score_method, score_factors, COALESCE(scored_at, CURRENT_TIMESTAMP) "
        "FROM jobs WHERE score_method IS NOT NULL"
    )).fetchall()
    for job_id, method, factors, at in rows:
        for f in json.loads(factors) if isinstance(factors, str) else factors or []:
            conn.execute(
                sa.text(
                    "INSERT INTO job_score_factors "
                    "(id, job_id, method, factor, value, kind, evidence, created_at) "
                    "VALUES (:id, :job, :m, :f, :v, :k, :e, :t)"
                ),
                {"id": str(uuid.uuid4()), "job": job_id, "m": method, "f": f["factor"],
                 "v": f["value"], "k": f.get("kind", "graded"), "e": f.get("evidence", ""),
                 "t": at},
            )
    with op.batch_alter_table("jobs") as batch:
        for name in (
            "scored_profile_version", "scored_at", "score_factors",
            "score_breakdown_md", "score_reasons", "score_method", "score",
        ):
            batch.drop_column(name)
