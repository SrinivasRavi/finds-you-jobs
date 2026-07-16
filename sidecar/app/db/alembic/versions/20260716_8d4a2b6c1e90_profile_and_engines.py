"""profile and engines — the A4 slice (database-design.md §3/§4/§6 slice).

Creates the profile + settings tables for the profile/engine-routing commit
(`docs/internal/roadmap.md` §7.2 #4): master_profiles (with the FR-APP-01
`application_profile` record folded into the initial DDL), profile_entities
(+ experience_skills / project_skills joins), and engine_settings. The
artifacts table lands with the commit that introduces the applications schema
its FK requires.

Revision ID: 8d4a2b6c1e90
Revises: 5c01e8a4f2b7
Create Date: 2026-07-16

"""
# ruff: noqa: E501 — Alembic DDL; long create_index/constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8d4a2b6c1e90'
down_revision: str | None = '5c01e8a4f2b7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('master_profiles',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('resume_markdown', sa.Text(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('application_profile', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('profile_entities',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('profile_id', sa.String(), nullable=False),
    sa.Column('entity_type', sa.String(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('user_curated', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['master_profiles.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('experience_skills',
    sa.Column('experience_id', sa.String(), nullable=False),
    sa.Column('skill_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['experience_id'], ['profile_entities.id'], ),
    sa.ForeignKeyConstraint(['skill_id'], ['profile_entities.id'], ),
    sa.PrimaryKeyConstraint('experience_id', 'skill_id')
    )
    op.create_table('project_skills',
    sa.Column('project_id', sa.String(), nullable=False),
    sa.Column('skill_id', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['profile_entities.id'], ),
    sa.ForeignKeyConstraint(['skill_id'], ['profile_entities.id'], ),
    sa.PrimaryKeyConstraint('project_id', 'skill_id')
    )
    op.create_table('engine_settings',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('engine', sa.String(), nullable=False),
    sa.Column('key_ref', sa.String(), nullable=True),
    sa.Column('key_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('base_url', sa.String(), nullable=True),
    sa.Column('default_model', sa.String(), nullable=True),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('engine_settings')
    op.drop_table('project_skills')
    op.drop_table('experience_skills')
    op.drop_table('profile_entities')
    op.drop_table('master_profiles')
