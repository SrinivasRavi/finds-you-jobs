"""core operations — the orchestration baseline (database-design.md §2/§4 slice).

Creates the core-storage commit's tables (`docs/internal/roadmap.md` §7.2 #3):
`operations` (the runner's durable queue + cost ledger) and `user_preferences`
(whose ui_state carries the pruned-spend lifetime aggregate). The remaining §7
tables land with their feature commits as follow-up revisions.

Revision ID: 5c01e8a4f2b7
Revises:
Create Date: 2026-07-16

"""
# ruff: noqa: E501 — Alembic DDL; long create_index/constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5c01e8a4f2b7'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('operations',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('state', sa.String(), nullable=False),
    sa.Column('input_snapshot', sa.JSON(), nullable=False),
    sa.Column('result_ref', sa.JSON(), nullable=True),
    sa.Column('usage', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('engine', sa.String(), nullable=True),
    sa.Column('model', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('finished_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('operations', schema=None) as batch_op:
        batch_op.create_index('ix_operations_finished', ['finished_at'], unique=False)
        batch_op.create_index('ix_operations_kind_created', ['kind', 'created_at'], unique=False)
        batch_op.create_index('ix_operations_state_created', ['state', 'created_at'], unique=False)

    op.create_table('user_preferences',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('role_aliases', sa.JSON(), nullable=False),
    sa.Column('locations', sa.JSON(), nullable=False),
    sa.Column('freshness_days', sa.Integer(), nullable=False),
    sa.Column('hard_excludes', sa.JSON(), nullable=False),
    sa.Column('hard_requires', sa.JSON(), nullable=False),
    sa.Column('soft_preferences', sa.JSON(), nullable=False),
    sa.Column('thresholds', sa.JSON(), nullable=False),
    sa.Column('portals_config', sa.JSON(), nullable=False),
    sa.Column('voyager_risk_marker_on', sa.Boolean(), nullable=False),
    sa.Column('engine_routing', sa.JSON(), nullable=False),
    sa.Column('ui_state', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('user_preferences')
    with op.batch_alter_table('operations', schema=None) as batch_op:
        batch_op.drop_index('ix_operations_state_created')
        batch_op.drop_index('ix_operations_kind_created')
        batch_op.drop_index('ix_operations_finished')
    op.drop_table('operations')
