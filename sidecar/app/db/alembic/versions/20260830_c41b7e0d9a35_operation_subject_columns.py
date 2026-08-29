"""The job, contact and batch an operation is about become real columns.

`operations` is one table serving 15 kinds of work, so its payload is JSON. That
part is fine. What wasn't: the job id lived only inside that payload, so "what is
happening with job X" loaded every score row and unpacked it in Python — 1.06 s
at 100k rows, on the event loop, and the shell kills a sidecar that misses a 2 s
health poll. Four of the 8 defects in the 2026-08 ledger trace to it.

Indexed columns, deliberately no foreign key. The ledger is history, and history
outlives its subjects: an FK would either block hard-deleting a job or blank the
record of what we spent on it, and an FK pointing at this table is what made
ledger retention fail silently for a month (S-C13).

The backfill reads the same snapshot key the queries used to. `job_id: ""` from
the retired `watch_company` kind stays NULL: "" is not a missing value.

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
