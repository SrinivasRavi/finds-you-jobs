"""manual applications + content-addressed documents (FR-TR manual-add).

Adds the "Add a job application" slice: an `origin` marker on applications so the
Tracker can filter fyj-discovered vs. manually-logged cards, plus a
content-addressed `documents` store (dedup by sha256) and an
`application_documents` link so a manual card can carry the resume/cover letter
the user actually submitted.

Revision ID: f1a2b3c4d5e6
Revises: d7b2a91c4e58
Create Date: 2026-07-23

"""
# ruff: noqa: E501 — Alembic DDL; long create_index/constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: str | None = 'd7b2a91c4e58'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing cards are all fyj-discovered — server_default backfills them.
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('origin', sa.String(), nullable=False, server_default='discovered')
        )

    op.create_table('documents',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('sha256', sa.String(), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('mime_type', sa.String(), nullable=False),
    sa.Column('original_filename', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sha256', name='uq_documents_sha256')
    )

    op.create_table('application_documents',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('application_id', sa.String(), nullable=False),
    sa.Column('document_id', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'kind', name='uq_appdoc_kind')
    )
    with op.batch_alter_table('application_documents', schema=None) as batch_op:
        batch_op.create_index('ix_appdoc_application', ['application_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('application_documents', schema=None) as batch_op:
        batch_op.drop_index('ix_appdoc_application')
    op.drop_table('application_documents')
    op.drop_table('documents')
    with op.batch_alter_table('applications', schema=None) as batch_op:
        batch_op.drop_column('origin')
