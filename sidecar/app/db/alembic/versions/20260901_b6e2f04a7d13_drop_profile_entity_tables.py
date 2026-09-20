"""Drop the 3 profile-entity tables — no writer, no reader, empty everywhere.

`profile_entities` and its `experience_skills` / `project_skills` join tables were
added for the FR-TL-01 fabrication guard, which was never built. Nothing outside
`models.py` ever constructed one and every install carries 0 rows, so the drop is
lossless. `downgrade` rebuilds the exact shape for when the guard lands.

Revision ID: b6e2f04a7d13
Revises: c41b7e0d9a35
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b6e2f04a7d13"
down_revision = "c41b7e0d9a35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("experience_skills")
    op.drop_table("project_skills")
    op.drop_table("profile_entities")


def downgrade() -> None:
    op.create_table(
        "profile_entities",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("profile_id", sa.String(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("user_curated", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["master_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "experience_skills",
        sa.Column("experience_id", sa.String(), nullable=False),
        sa.Column("skill_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["experience_id"], ["profile_entities.id"]),
        sa.ForeignKeyConstraint(["skill_id"], ["profile_entities.id"]),
        sa.PrimaryKeyConstraint("experience_id", "skill_id"),
    )
    op.create_table(
        "project_skills",
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("skill_id", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["profile_entities.id"]),
        sa.ForeignKeyConstraint(["skill_id"], ["profile_entities.id"]),
        sa.PrimaryKeyConstraint("project_id", "skill_id"),
    )
