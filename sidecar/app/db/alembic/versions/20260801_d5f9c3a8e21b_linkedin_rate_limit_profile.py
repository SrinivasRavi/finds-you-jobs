"""linkedin self-imposed rate-limit profile: membership_type, risk_pct, cap_overrides.

Maintainer directive 2026-08-01. The New/Seasoned account tier is retired as the
user-facing basis — that gradation now lives in a risk-appetite slider. The user
picks a LinkedIn **membership type** (the estimated-ceiling basis), a **risk_pct**
(10–100, scales the ceilings), and optional per-meter **cap_overrides** (absolute
pins). The outreach package computes the effective caps from these.

The free/premium plan the note-budget path keys on is derived from
`membership_type` at runtime (`pacing.plan_for_membership`) — no stored plan
column. `account_tier` is retained only because it pre-dates this branch;
nothing reads it.

Revision ID: d5f9c3a8e21b
Revises: c4e8b2d7f3a9
Create Date: 2026-08-01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd5f9c3a8e21b'
down_revision: str | None = 'c4e8b2d7f3a9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('linkedin_sessions', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('membership_type', sa.String(), nullable=False, server_default='free')
        )
        batch_op.add_column(
            sa.Column('risk_pct', sa.Integer(), nullable=False, server_default='60')
        )
        batch_op.add_column(
            sa.Column('cap_overrides', sa.JSON(), nullable=False, server_default='{}')
        )


def downgrade() -> None:
    with op.batch_alter_table('linkedin_sessions', schema=None) as batch_op:
        batch_op.drop_column('cap_overrides')
        batch_op.drop_column('risk_pct')
        batch_op.drop_column('membership_type')
