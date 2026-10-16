"""6 autonomy columns on `user_preferences`: 3 autopilot switches and 3 limits.

Every switch starts off, which is "confirm each", so an upgrade changes nothing
until the user turns 1 on (audit-2026-10-14.md section 8.10).

Revision ID: f3b8d1e6a2c4
Revises: e5a1c7d3b9f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f3b8d1e6a2c4"
down_revision = "e5a1c7d3b9f2"
branch_labels = None
depends_on = None

_SWITCHES = ("autopilot_invite", "autopilot_dm", "autopilot_apply")
_LIMITS = (
    ("autopilot_min_score", "80"),
    ("autopilot_contacts_per_job", "2"),
    ("autopilot_applies_per_day", "20"),
)


def upgrade() -> None:
    with op.batch_alter_table("user_preferences") as batch:
        for name in _SWITCHES:
            batch.add_column(
                sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
            )
        for name, default in _LIMITS:
            batch.add_column(
                sa.Column(name, sa.Integer(), nullable=False, server_default=default)
            )


def downgrade() -> None:
    with op.batch_alter_table("user_preferences") as batch:
        for name, _default in reversed(_LIMITS):
            batch.drop_column(name)
        for name in reversed(_SWITCHES):
            batch.drop_column(name)
