"""apply runs — the Applier slice (`docs/internal/applier.md` §9.1).

One durable row per Applier attempt: immutable evidence, retry-linked

Revision ID: a33c46cd3118
Revises: b3f8d21a6c47
Create Date: 2026-07-17 07:43:54.363993

"""
# ruff: noqa: E501 — Alembic DDL; long constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a33c46cd3118'
down_revision: str | None = 'b3f8d21a6c47'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('apply_runs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('application_id', sa.String(), nullable=False),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('retry_of_run_id', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('phase', sa.String(), nullable=False),
    sa.Column('source_url', sa.String(), nullable=False),
    sa.Column('final_url', sa.String(), nullable=False),
    sa.Column('resume_artifact_id', sa.String(), nullable=True),
    sa.Column('cover_artifact_id', sa.String(), nullable=True),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('blockers', sa.JSON(), nullable=False),
    sa.Column('fields', sa.JSON(), nullable=False),
    sa.Column('screenshots', sa.JSON(), nullable=False),
    sa.Column('usage', sa.JSON(), nullable=False),
    sa.Column('steps', sa.Integer(), nullable=False),
    sa.Column('submit_evidence', sa.String(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=False),
    sa.Column('deadline_at', sa.DateTime(), nullable=True),
    sa.Column('ended_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('apply_runs', schema=None) as batch_op:
        batch_op.create_index('ix_applyrun_application', ['application_id', 'started_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('apply_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_applyrun_application')

    op.drop_table('apply_runs')
