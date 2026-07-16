"""tracker applications — the pipeline slice (database-design.md §4 slice).

Creates the tracker tables for the saved/optional-referral/ready-to-apply
commit (`docs/internal/roadmap.md` §7.2 #8): applications (with the §5.1
exclusive `intent` column folded into the initial DDL; the prior repository's
retired `apply_state`/`form_prep` columns are deliberately absent), artifacts
(with `approved_at` folded in), and application_events.

Revision ID: e5a7c2d9b1f6
Revises: c7e91d3f5a24
Create Date: 2026-07-16

"""
# ruff: noqa: E501 — Alembic DDL; long create_index/constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5a7c2d9b1f6'
down_revision: str | None = 'c7e91d3f5a24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('applications',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('column', sa.String(), nullable=False),
    sa.Column('priority', sa.String(), nullable=False),
    sa.Column('intent', sa.String(), nullable=False),
    sa.Column('notes_markdown', sa.Text(), nullable=False),
    sa.Column('applied_via', sa.String(), nullable=True),
    sa.Column('preview_screenshot_path', sa.String(), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.Column('saved_at', sa.DateTime(), nullable=False),
    sa.Column('last_touched_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('artifacts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('application_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('markdown', sa.Text(), nullable=False),
    sa.Column('notes', sa.JSON(), nullable=False),
    sa.Column('profile_version', sa.Integer(), nullable=False),
    sa.Column('guidance_used', sa.Text(), nullable=True),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('superseded_by', sa.String(), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], ),
    sa.ForeignKeyConstraint(['superseded_by'], ['artifacts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('application_events',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('application_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('detail', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('application_events', schema=None) as batch_op:
        batch_op.create_index('ix_appevent_application', ['application_id', 'created_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('application_events', schema=None) as batch_op:
        batch_op.drop_index('ix_appevent_application')
    op.drop_table('application_events')
    op.drop_table('artifacts')
    op.drop_table('applications')
