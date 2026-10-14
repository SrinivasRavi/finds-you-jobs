"""linkedin_search_cursors: the Fresh search / Next page cursor.

A Fresh search snapshots its queries with each pair's next offset; Next page
resumes it within 12 h (result coherence; LinkedIn's paging never expires).

Revision ID: c4e8b2d7f3a9
Revises: f1a2b3c4d5e6
Create Date: 2026-08-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4e8b2d7f3a9'
down_revision: str | None = 'f1a2b3c4d5e6'
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
