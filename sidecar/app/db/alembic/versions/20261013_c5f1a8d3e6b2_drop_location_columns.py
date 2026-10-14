"""Drop the 5 resolved-location columns on `jobs`.

Nothing reads them: places are worked out when the board loads, in about 10 µs a
job (place-picker.md section 10.4). The downgrade puts them back empty, and NULL
`location_remote` reads as "never resolved", so the old daily pass refills them.

Revision ID: c5f1a8d3e6b2
Revises: b3e9c4a1d7f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5f1a8d3e6b2"
down_revision = "b3e9c4a1d7f2"
branch_labels = None
depends_on = None

# (column, is_integer), as b4d8e2a7c531 added them.
_COLUMNS: tuple[tuple[str, bool], ...] = (
    ("location_country", False),
    ("location_admin1", False),
    ("location_city_id", True),
    ("location_remote", False),
    ("location_attendance", False),
)


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        for name, _is_integer in _COLUMNS:
            batch.drop_column(name)


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        for name, is_integer in _COLUMNS:
            batch.add_column(
                sa.Column(name, sa.Integer() if is_integer else sa.String(), nullable=True)
            )
