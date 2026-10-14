"""3 lifecycle dates become columns, and the dead weight goes.

`expired_at`, `trashed_at`, `feed_since` leave `source_meta` for indexed columns
(`feed_since` NOT NULL). `sequences` tables go (S-N11), with 2 indexes, 7 unread
columns and 2 stale `ui_state` keys; `linkedin_plan` drops only where it exists.

Revision ID: e7b1c95d2a48
Revises: d2f7a91b8c46
Create Date: 2026-09-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7b1c95d2a48"
down_revision = "d2f7a91b8c46"
branch_labels = None
depends_on = None

_DEAD_COLUMNS = (
    ("apply_runs", "cover_artifact_id"),
    ("company_resolutions", "resolved_at"),
    ("tombstones", "tombstoned_at"),
    ("master_profiles", "created_at"),
    ("referral_candidates", "status"),
    ("linkedin_sessions", "account_tier"),
)


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    # -- 1. the 3 lifecycle dates ------------------------------------------
    op.add_column("jobs", sa.Column("expired_at", sa.DateTime(), nullable=True))
    op.add_column("jobs", sa.Column("trashed_at", sa.DateTime(), nullable=True))
    op.add_column("jobs", sa.Column("feed_since", sa.DateTime(), nullable=True))

    # json_extract is exactly what this migration exists to stop doing at read
    # time; using it once to move the data out is what a backfill is.
    for column in ("expired_at", "trashed_at", "feed_since"):
        op.execute(
            f"""
            UPDATE jobs
            SET {column} = datetime(
                replace(json_extract(source_meta, '$.{column}'), 'T', ' ')
            )
            WHERE source_meta IS NOT NULL
              AND json_extract(source_meta, '$.{column}') IS NOT NULL
            """  # noqa: S608  column comes from the literal tuple above
        )
    op.execute("UPDATE jobs SET feed_since = ingested_at WHERE feed_since IS NULL")
    with op.batch_alter_table("jobs") as batch:
        batch.alter_column("feed_since", existing_type=sa.DateTime(), nullable=False)

    op.execute(
        """
        UPDATE jobs SET source_meta = json_remove(
            source_meta, '$.expired_at', '$.trashed_at', '$.feed_since'
        ) WHERE source_meta IS NOT NULL
        """
    )
    op.create_index("ix_jobs_feedstate_expired", "jobs", ["feed_state", "expired_at"])
    op.create_index("ix_jobs_feedstate_trashed", "jobs", ["feed_state", "trashed_at"])
    op.create_index("ix_jobs_feedstate_since", "jobs", ["feed_state", "feed_since"])

    # -- 2. the playbook tables --------------------------------------------
    with op.batch_alter_table("outreach_logs") as batch:
        batch.drop_column("sequence_id")
        batch.drop_column("step_id")
    op.drop_table("sequence_steps")
    op.drop_table("sequences")

    # -- 3. dead indexes and columns ---------------------------------------
    op.drop_index("ix_jobs_company", table_name="jobs")
    op.drop_index("ix_operations_contact_kind", table_name="operations")
    for table, column in _DEAD_COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
    if _has_column("linkedin_sessions", "linkedin_plan"):
        with op.batch_alter_table("linkedin_sessions") as batch:
            batch.drop_column("linkedin_plan")

    op.execute(
        """
        UPDATE user_preferences SET ui_state = json_remove(
            ui_state, '$.linkedin_search_enabled', '$.linkedin_search_ack_at'
        ) WHERE ui_state IS NOT NULL
        """
    )


def downgrade() -> None:
    op.create_index("ix_operations_contact_kind", "operations", ["contact_id", "kind"])
    op.create_index("ix_jobs_company", "jobs", ["company"])
    op.add_column(
        "apply_runs", sa.Column("cover_artifact_id", sa.String(), nullable=True)
    )
    # SQLite refuses ADD COLUMN with a non-constant default, so each of these 3
    # timestamps arrives nullable, gets stamped, and is tightened after.
    for table, column in (
        ("company_resolutions", "resolved_at"),
        ("tombstones", "tombstoned_at"),
        ("master_profiles", "created_at"),
    ):
        op.add_column(table, sa.Column(column, sa.DateTime(), nullable=True))
        op.execute(f"UPDATE {table} SET {column} = CURRENT_TIMESTAMP")  # noqa: S608
        with op.batch_alter_table(table) as batch:
            batch.alter_column(column, existing_type=sa.DateTime(), nullable=False)
    op.add_column(
        "referral_candidates",
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
    )
    op.add_column(
        "linkedin_sessions",
        sa.Column("account_tier", sa.String(), nullable=False, server_default="new"),
    )

    op.create_table(
        "sequences",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("audience", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sequence_steps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("sequence_id", sa.String(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("channel", sa.String(), nullable=False, server_default="linkedin_dm"),
        sa.Column("body_template", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "delay_days_from_previous", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("trigger", sa.String(), nullable=False, server_default="manual"),
        sa.ForeignKeyConstraint(["sequence_id"], ["sequences.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_seqstep_sequence", "sequence_steps", ["sequence_id", "order_index"])
    op.add_column("outreach_logs", sa.Column("sequence_id", sa.String(), nullable=True))
    op.add_column("outreach_logs", sa.Column("step_id", sa.String(), nullable=True))

    # The dates go back into the JSON exactly as they were read out of it.
    op.execute(
        """
        UPDATE jobs SET source_meta = json_patch(
            COALESCE(source_meta, '{}'),
            json_object('expired_at', replace(expired_at, ' ', 'T'))
        ) WHERE expired_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE jobs SET source_meta = json_patch(
            COALESCE(source_meta, '{}'),
            json_object('trashed_at', replace(trashed_at, ' ', 'T'))
        ) WHERE trashed_at IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE jobs SET source_meta = json_patch(
            COALESCE(source_meta, '{}'),
            json_object('feed_since', replace(feed_since, ' ', 'T'))
        ) WHERE feed_since IS NOT NULL AND feed_since != ingested_at
        """
    )
    op.drop_index("ix_jobs_feedstate_since", table_name="jobs")
    op.drop_index("ix_jobs_feedstate_trashed", table_name="jobs")
    op.drop_index("ix_jobs_feedstate_expired", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("feed_since")
        batch.drop_column("trashed_at")
        batch.drop_column("expired_at")
