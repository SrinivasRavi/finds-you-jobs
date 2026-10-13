"""Re-resolve every job's location with the place-reading fix.

The old resolver read "London, ON" as England (place-picker.md section 2). This
only clears the "never resolved" marker; the daily pass re-resolves 500 a run,
so startup stays instant.

Revision ID: f7c2d4e9a1b6
Revises: e3a8c51f7d20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f7c2d4e9a1b6"
down_revision = "e3a8c51f7d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text("UPDATE jobs SET location_remote = NULL"))


def downgrade() -> None:
    # Nothing to restore: the next maintenance pass resolves every row again.
    pass
