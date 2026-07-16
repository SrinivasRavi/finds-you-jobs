"""jobs discovery — the scraper slice (database-design.md §2/§3 slice).

Creates the job-discovery tables for the source-registry commit
(`docs/internal/roadmap.md` §7.2 #7): jobs (canonical-URL dedup key),
job_scores (the per-profile-version score cache), tombstones (permanent
discards), and schedules (the recurring-operation rows the scheduler ticks).

Revision ID: c7e91d3f5a24
Revises: 8d4a2b6c1e90
Create Date: 2026-07-16

"""
# ruff: noqa: E501 — Alembic DDL; long create_index/constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c7e91d3f5a24'
down_revision: str | None = '8d4a2b6c1e90'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('jobs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('canonical_url', sa.String(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('company', sa.String(), nullable=False),
    sa.Column('location', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('posted_at', sa.String(), nullable=True),
    sa.Column('salary', sa.String(), nullable=True),
    sa.Column('source_adapter', sa.String(), nullable=False),
    sa.Column('trust_score', sa.Integer(), nullable=False),
    sa.Column('trust_flags', sa.JSON(), nullable=False),
    sa.Column('ingested_at', sa.DateTime(), nullable=False),
    sa.Column('feed_state', sa.String(), nullable=False),
    sa.Column('source_meta', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('canonical_url')
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index('ix_jobs_company', ['company'], unique=False)
        batch_op.create_index('ix_jobs_feedstate_ingested', ['feed_state', 'ingested_at'], unique=False)

    op.create_table('job_scores',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('profile_version', sa.Integer(), nullable=False),
    sa.Column('score_0_100', sa.Integer(), nullable=False),
    sa.Column('reasons', sa.JSON(), nullable=False),
    sa.Column('breakdown_md', sa.Text(), nullable=False),
    sa.Column('scorer_impl', sa.String(), nullable=False),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('scored_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('job_id', 'profile_version', 'scorer_impl', name='uq_jobscore_cachekey')
    )
    op.create_table('tombstones',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('canonical_url', sa.String(), nullable=False),
    sa.Column('tombstoned_at', sa.DateTime(), nullable=False),
    sa.Column('reason', sa.String(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('canonical_url')
    )
    op.create_table('schedules',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('interval_minutes', sa.Integer(), nullable=False),
    sa.Column('next_due_at', sa.DateTime(), nullable=False),
    sa.Column('last_enqueued_operation_id', sa.String(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['last_enqueued_operation_id'], ['operations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('schedules')
    op.drop_table('tombstones')
    op.drop_table('job_scores')
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.drop_index('ix_jobs_feedstate_ingested')
        batch_op.drop_index('ix_jobs_company')
    op.drop_table('jobs')
