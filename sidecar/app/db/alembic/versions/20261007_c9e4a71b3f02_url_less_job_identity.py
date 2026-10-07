"""A job can have no posting URL, so identity moves to its content.

url-less-jobs.md section 5.3, decision D8, Shape B.

`canonical_url` does 3 unrelated jobs today: identity, content source and
action target. Dropping the URL requirement only breaks the first one, so this
gives identity its own column rather than minting a fake URL to put in the old
one. A fake value would satisfy the unique index and lie to every reader that
parses it as a URL, which is exactly the defect 7.1 fixed in the Networker.

4 changes:

- `jobs.canonical_url` becomes nullable. A row with no URL now says so,
  instead of carrying `fyj-local:<uuid>` or the prose a failed fetch wrote.
- `jobs.content_hash`, unique: SHA-256 of the normalized token stream of the
  description (`modules/scraper/identity.py`). The dedup key for a URL-less
  row, and kept across a promotion so a later re-paste still finds the row.
- `jobs.fingerprint`, indexed: the 64-bit SimHash `fingerprint.py` already
  computes per scan and throws away. It answers "nearly the same text?", which
  is the duplicate prompt, the promotion check and the repost signal.
- `tombstones.content_hash`, unique, with `canonical_url` nullable: so delete
  forever works on a job that never had a URL. `tombstones.exists` looks up
  whichever key the row has.

No backfill of `content_hash`: every existing row has a URL and is keyed on
it, and hashing 133 descriptions to fill a column nothing would read for them
buys nothing. `fingerprint` backfills on the next scan for the same reason.

Each population keeps its own unique key. SQLite treats NULLs as distinct in a
unique index, so any number of URL-less rows coexist under
`canonical_url IS NULL` and any number of URL rows under
`content_hash IS NULL`.

Revision ID: c9e4a71b3f02
Revises: b4d8e2a7c531
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9e4a71b3f02"
down_revision = "b4d8e2a7c531"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=True
        )
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("fingerprint", sa.Integer(), nullable=True))
        batch_op.create_unique_constraint("uq_jobs_content_hash", ["content_hash"])
        batch_op.create_index("ix_jobs_fingerprint", ["fingerprint"])

    with op.batch_alter_table("tombstones", schema=None) as batch_op:
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=True
        )
        batch_op.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch_op.create_unique_constraint(
            "uq_tombstones_content_hash", ["content_hash"]
        )


def downgrade() -> None:
    # Rows that only a URL-less job could have written have no URL to fall back
    # on, so they go rather than take a fabricated one into the old shape.
    op.execute(sa.text("DELETE FROM jobs WHERE canonical_url IS NULL"))
    op.execute(sa.text("DELETE FROM tombstones WHERE canonical_url IS NULL"))

    with op.batch_alter_table("tombstones", schema=None) as batch_op:
        batch_op.drop_constraint("uq_tombstones_content_hash", type_="unique")
        batch_op.drop_column("content_hash")
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=False
        )

    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_index("ix_jobs_fingerprint")
        batch_op.drop_constraint("uq_jobs_content_hash", type_="unique")
        batch_op.drop_column("fingerprint")
        batch_op.drop_column("content_hash")
        batch_op.alter_column(
            "canonical_url", existing_type=sa.String(), nullable=False
        )
