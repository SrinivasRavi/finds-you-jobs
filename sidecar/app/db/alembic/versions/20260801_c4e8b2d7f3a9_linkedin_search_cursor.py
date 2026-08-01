"""linkedin_search_cursors — Fresh search / Next page pagination cursor.

The one-shot LinkedIn job search grows a Next-page button: a Fresh search
snapshots its queries here with each pair's next offset, Next page resumes the
snapshot within a 12 h freshness window (host policy — LinkedIn's own
pagination is stateless and never expires; the TTL is for result coherence).

Revision ID: c4e8b2d7f3a9
Revises: a9c4e7f2b8d1
Create Date: 2026-08-01

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e8b2d7f3a9'
down_revision: str | None = 'a9c4e7f2b8d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'linkedin_search_cursors',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('fresh_at', sa.DateTime(), nullable=True),
        sa.Column('queries', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('linkedin_search_cursors')
