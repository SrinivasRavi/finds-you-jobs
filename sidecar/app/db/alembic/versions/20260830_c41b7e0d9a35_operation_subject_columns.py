"""The job, contact and batch an operation is about become real columns.

The job id lived only in the JSON payload, so "what's happening with job X" took
1.06 s at 100k rows on the loop (4 of 8 defects in the 2026-08 ledger).
Indexed, no FK: history outlives its subjects (S-C13). `job_id: ""` stays NULL.

Revision ID: c41b7e0d9a35
Revises: a3d7e1f95c24
Create Date: 2026-08-30
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c41b7e0d9a35'
down_revision: str | None = 'a3d7e1f95c24'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.add_column("operations", sa.Column("job_id", sa.String(), nullable=True))
    op.add_column("operations", sa.Column("contact_id", sa.String(), nullable=True))
    op.add_column("operations", sa.Column("batch_id", sa.String(), nullable=True))

    op.execute(
        "UPDATE operations SET job_id = json_extract(input_snapshot, '$.job_id') "
        "WHERE json_extract(input_snapshot, '$.job_id') NOT IN ('')"
    )
    op.execute(
        "UPDATE operations SET contact_id = "
        "json_extract(input_snapshot, '$.contact_id') "
        "WHERE json_extract(input_snapshot, '$.contact_id') NOT IN ('')"
    )
    op.execute(
        "UPDATE operations SET batch_id = json_extract(input_snapshot, '$.batch_id') "
        "WHERE json_extract(input_snapshot, '$.batch_id') NOT IN ('')"
    )

    op.create_index("ix_operations_job_kind", "operations", ["job_id", "kind"])
    op.create_index("ix_operations_contact_kind", "operations", ["contact_id", "kind"])
    op.create_index("ix_operations_batch", "operations", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_operations_batch", table_name="operations")
    op.drop_index("ix_operations_contact_kind", table_name="operations")
    op.drop_index("ix_operations_job_kind", table_name="operations")
    op.drop_column("operations", "batch_id")
    op.drop_column("operations", "contact_id")
    op.drop_column("operations", "job_id")
