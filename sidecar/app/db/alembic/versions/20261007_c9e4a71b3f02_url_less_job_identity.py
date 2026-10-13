"""A job can have no posting URL, so identity moves to its content.

url-less-jobs.md section 5.3 (D8, Shape B): `canonical_url` nullable, unique
`content_hash`, indexed `fingerprint`, and tombstones keyed either way. SQLite
NULLs are distinct in a unique index, so each population keeps its own key.

Revision ID: c9e4a71b3f02
Revises: b4d8e2a7c531
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e4a71b3f02"
down_revision = "b4d8e2a7c531"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=True
        )
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("fingerprint", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint("uq_jobs_content_hash", ["content_hash"])
        batch_op.create_index("ix_jobs_fingerprint", ["fingerprint"])

    with op.batch_alter_table("tombstones", schema=None) as batch_op:
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=True
        )
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_tombstones_content_hash", ["content_hash"]
        )


def downgrade() -> None:
    # Rows that only a URL-less job could have written have no URL to fall back
    # on, so they go rather than take a fabricated one into the old shape.
    op.execute(sa.text("DELETE FROM jobs WHERE canonical_url IS NULL"))
    op.execute(sa.text("DELETE FROM tombstones WHERE canonical_url IS NULL"))

    with op.batch_alter_table("tombstones", schema=None) as batch_op:
        batch_op.drop_constraint("uq_tombstones_content_hash", type_="unique")
        batch_op.drop_column("content_hash")
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=False
        )

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_fingerprint")
        batch_op.drop_constraint("uq_jobs_content_hash", type_="unique")
        batch_op.drop_column("fingerprint")
        batch_op.drop_column("content_hash")
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=False
        )
