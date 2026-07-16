"""networking tables — the Referral Outreach slice (database-design.md §5).

Creates the seven networking tables for the referral commit
(`docs/internal/roadmap.md` §7.2 #10-11): contacts, company_resolutions,
contact_job_assocs, sequences, sequence_steps, outreach_logs, linkedin_sessions.

Revision ID: b3f8d21a6c47
Revises: e5a7c2d9b1f6
Create Date: 2026-07-16

"""
# ruff: noqa: E501 — Alembic DDL; long constraint lines are boilerplate.
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b3f8d21a6c47'
down_revision: str | None = 'e5a7c2d9b1f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('company_resolutions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('resolution_key', sa.String(), nullable=False),
    sa.Column('company_name', sa.String(), nullable=False),
    sa.Column('company_urn', sa.String(), nullable=False),
    sa.Column('company_vanity', sa.String(), nullable=False),
    sa.Column('industry', sa.String(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('resolution_key')
    )
    op.create_table('contacts',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('linkedin_url', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('current_role', sa.String(), nullable=False),
    sa.Column('current_company', sa.String(), nullable=False),
    sa.Column('headline', sa.String(), nullable=False),
    sa.Column('connection_degree', sa.Integer(), nullable=True),
    sa.Column('is_first_degree', sa.Boolean(), nullable=False),
    sa.Column('audience_tag', sa.String(), nullable=False),
    sa.Column('warmth', sa.String(), nullable=False),
    sa.Column('connection_status', sa.String(), nullable=False),
    sa.Column('profile_payload', sa.JSON(), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.Column('last_touched_at', sa.DateTime(), nullable=False),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('accepted_at', sa.DateTime(), nullable=True),
    sa.Column('archived_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('linkedin_url')
    )
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.create_index('ix_contacts_company', ['current_company'], unique=False)
        batch_op.create_index('ix_contacts_status', ['connection_status'], unique=False)

    op.create_table('linkedin_sessions',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('cookies_encrypted', sa.LargeBinary(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('account_tier', sa.String(), nullable=False),
    sa.Column('connected_as', sa.String(), nullable=False),
    sa.Column('li_at_expires_at', sa.DateTime(), nullable=True),
    sa.Column('last_validated_at', sa.DateTime(), nullable=True),
    sa.Column('paused_until', sa.DateTime(), nullable=True),
    sa.Column('paused_reason', sa.String(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('sequences',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('audience', sa.String(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('contact_job_assocs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('contact_id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=False),
    sa.Column('audience_tag', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('selected', sa.Boolean(), nullable=False),
    sa.Column('added_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('contact_id', 'job_id', name='uq_contactjob')
    )
    with op.batch_alter_table('contact_job_assocs', schema=None) as batch_op:
        batch_op.create_index('ix_contactjob_job', ['job_id'], unique=False)

    op.create_table('sequence_steps',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('sequence_id', sa.String(), nullable=False),
    sa.Column('order_index', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(), nullable=False),
    sa.Column('channel', sa.String(), nullable=False),
    sa.Column('body_template', sa.Text(), nullable=False),
    sa.Column('delay_days_from_previous', sa.Integer(), nullable=False),
    sa.Column('trigger', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['sequence_id'], ['sequences.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('sequence_steps', schema=None) as batch_op:
        batch_op.create_index('ix_seqstep_sequence', ['sequence_id', 'order_index'], unique=False)

    op.create_table('outreach_logs',
    sa.Column('id', sa.String(), nullable=False),
    sa.Column('contact_id', sa.String(), nullable=False),
    sa.Column('job_id', sa.String(), nullable=True),
    sa.Column('sequence_id', sa.String(), nullable=True),
    sa.Column('step_id', sa.String(), nullable=True),
    sa.Column('channel', sa.String(), nullable=False),
    sa.Column('batch_id', sa.String(), nullable=True),
    sa.Column('body_sent', sa.Text(), nullable=False),
    sa.Column('outcome', sa.String(), nullable=False),
    sa.Column('outcome_detail', sa.Text(), nullable=False),
    sa.Column('operation_id', sa.String(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['contact_id'], ['contacts.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['operation_id'], ['operations.id'], ),
    sa.ForeignKeyConstraint(['sequence_id'], ['sequences.id'], ),
    sa.ForeignKeyConstraint(['step_id'], ['sequence_steps.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('outreach_logs', schema=None) as batch_op:
        batch_op.create_index('ix_outreach_contact', ['contact_id'], unique=False)
        batch_op.create_index('ix_outreach_job', ['job_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('outreach_logs', schema=None) as batch_op:
        batch_op.drop_index('ix_outreach_job')
        batch_op.drop_index('ix_outreach_contact')

    op.drop_table('outreach_logs')
    with op.batch_alter_table('sequence_steps', schema=None) as batch_op:
        batch_op.drop_index('ix_seqstep_sequence')

    op.drop_table('sequence_steps')
    with op.batch_alter_table('contact_job_assocs', schema=None) as batch_op:
        batch_op.drop_index('ix_contactjob_job')

    op.drop_table('contact_job_assocs')
    op.drop_table('sequences')
    op.drop_table('linkedin_sessions')
    with op.batch_alter_table('contacts', schema=None) as batch_op:
        batch_op.drop_index('ix_contacts_status')
        batch_op.drop_index('ix_contacts_company')

    op.drop_table('contacts')
    op.drop_table('company_resolutions')
