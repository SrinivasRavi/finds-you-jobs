"""Re-resolve every job's location with the place-reading fix.

The stored location columns were written by a resolver that read "London, ON"
as London, England (docs/internal/place-picker.md section 2). Clearing the
"never resolved" marker hands every row back to the daily maintenance pass,
which re-resolves 500 a run in its worker thread; this migration only flips the
marker, so startup stays instant.

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
