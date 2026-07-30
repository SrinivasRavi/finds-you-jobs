"""linkedin_plan on linkedin_sessions (posture doc §4 fix 10).

The free-plan personalized-note allowance (~5/month) exists only on free
LinkedIn accounts, so the outreach package's notes budget must be conditioned
on the plan — before this column there was no field to condition on, and the
cap either broke Premium users or protected nobody.

Revision ID: a9c4e7f2b8d1
Revises: f1a2b3c4d5e6
Create Date: 2026-07-30

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9c4e7f2b8d1'
down_revision: str | None = 'f1a2b3c4d5e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 'free' is the conservative default: it gates note-bearing sends on the
    # notes budget; a Premium holder lifts it in Settings.
    with op.batch_alter_table('linkedin_sessions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('linkedin_plan', sa.String(), nullable=False, server_default='free')
        )


def downgrade() -> None:
    with op.batch_alter_table('linkedin_sessions', schema=None) as batch_op:
        batch_op.drop_column('linkedin_plan')
