"""1 table for duplicate pairs, who added each job, and the dead tombstone column.

`job_duplicates` replaces the JSON flags and the dismissals table (section 9.6).
`jobs.origin` is `scan` or `manual`: only a scanned job is tombstoned (8.2).
`tombstones.content_hash` has been unused since fd611f5.

Revision ID: e3a8c51f7d20
Revises: d1c5f83a9b27
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "e3a8c51f7d20"
down_revision = "d1c5f83a9b27"
branch_labels = None
depends_on = None

_PAIR_PREFIXES = ("probable-duplicate:", "duplicate-batch:", "probable-cross-listing:")


def upgrade() -> None:
    bind = op.get_bind()

    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("origin", sa.String(), nullable=False, server_default="scan")
        )
    bind.execute(
        sa.text(
            "UPDATE jobs SET origin = 'manual' WHERE source_adapter IN ('paste-url', 'paste-text') "
            "OR id IN (SELECT job_id FROM applications WHERE origin = 'manual')"
        )
    )

    op.create_table(
        "job_duplicates",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "other_job_id",
            sa.String(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("job_id", "other_job_id", name="uq_job_duplicates_pair"),
    )
    op.create_index("ix_job_duplicates_other", "job_duplicates", ["other_job_id"])

    now = datetime.now(UTC).replace(tzinfo=None)
    by_url: dict[str, str] = {
        str(url): str(job_id)
        for url, job_id in bind.execute(
            sa.text("SELECT canonical_url, id FROM jobs WHERE canonical_url IS NOT NULL")
        ).all()
    }
    # Old flags include some written before the compound gate (S-C69), which
    # called a Senior and a Principal the same job; only pairs today's gate
    # still matches are carried over.
    from sidecar.modules.scraper.fingerprint import is_probable_cross_listing, simhash64

    jobs = {
        str(r[0]): (simhash64(str(r[1] or "")), str(r[2] or ""), str(r[3] or ""))
        for r in bind.execute(sa.text("SELECT id, description, company, title FROM jobs"))
    }

    def still_matches(a: str, b: str) -> bool:
        (fp_a, co_a, ti_a), (fp_b, co_b, ti_b) = jobs[a], jobs[b]
        return bool(fp_a and fp_b) and is_probable_cross_listing(
            fp_a, fp_b, company_a=co_a, company_b=co_b, title_a=ti_a, title_b=ti_b
        )

    pairs: set[tuple[str, str]] = set()
    for job_id, raw in bind.execute(sa.text("SELECT id, trust_flags FROM jobs")).all():
        flags = json.loads(raw) if isinstance(raw, str) else (raw or [])
        kept = [f for f in flags if not str(f).startswith(_PAIR_PREFIXES)]
        for raw_flag in flags:
            flag = str(raw_flag)
            other: str | None = None
            if flag.startswith("probable-duplicate:"):
                other = flag[len("probable-duplicate:") :]
            elif flag.startswith("probable-cross-listing:"):
                other = by_url.get(flag[len("probable-cross-listing:") :])
            if (
                other
                and other in jobs
                and other != job_id
                and (other, job_id) not in pairs
                and still_matches(job_id, other)
            ):
                pairs.add((job_id, other))
        if len(kept) != len(flags):
            bind.execute(
                sa.text("UPDATE jobs SET trust_flags = :f WHERE id = :id"),
                {"f": json.dumps(kept), "id": job_id},
            )

    dismissed = bind.execute(
        sa.text("SELECT low_job_id, high_job_id, created_at FROM job_duplicate_dismissals")
    ).all()
    dismissed_at: dict[frozenset[str], object] = {
        frozenset((low, high)): at for low, high, at in dismissed
    }
    for low, high, _ in dismissed:
        if (low, high) not in pairs and (high, low) not in pairs:
            pairs.add((high, low))
    for job_id, other in pairs:
        bind.execute(
            sa.text(
                "INSERT INTO job_duplicates (id, job_id, other_job_id, detected_at, dismissed_at) "
                "VALUES (:id, :job, :other, :now, :dismissed)"
            ),
            {
                "id": str(uuid.uuid4()),
                "job": job_id,
                "other": other,
                "now": now,
                "dismissed": dismissed_at.get(frozenset((job_id, other))),
            },
        )

    op.drop_index("ix_job_duplicate_dismissals_high", table_name="job_duplicate_dismissals")
    op.drop_table("job_duplicate_dismissals")

    bind.execute(sa.text("DELETE FROM tombstones WHERE canonical_url IS NULL"))
    with op.batch_alter_table("tombstones") as batch:
        batch.drop_constraint("uq_tombstones_content_hash", type_="unique")
        batch.drop_column("content_hash")
        batch.alter_column("canonical_url", existing_type=sa.String(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("tombstones") as batch:
        batch.alter_column("canonical_url", existing_type=sa.String(), nullable=True)
        batch.add_column(sa.Column("content_hash", sa.String(), nullable=True))
        batch.create_unique_constraint("uq_tombstones_content_hash", ["content_hash"])

    op.create_table(
        "job_duplicate_dismissals",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "low_job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "high_job_id", sa.String(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("low_job_id", "high_job_id", name="uq_job_duplicate_pair"),
    )
    op.create_index(
        "ix_job_duplicate_dismissals_high", "job_duplicate_dismissals", ["high_job_id"]
    )
    rows = bind.execute(
        sa.text("SELECT job_id, other_job_id, dismissed_at FROM job_duplicates")
    ).all()
    flags_by_job: dict[str, list[str]] = {}
    for job_id, other, at in rows:
        if at is not None:
            low, high = sorted((job_id, other))
            bind.execute(
                sa.text(
                    "INSERT INTO job_duplicate_dismissals "
                    "(id, low_job_id, high_job_id, created_at) VALUES (:id, :low, :high, :at)"
                ),
                {"id": str(uuid.uuid4()), "low": low, "high": high, "at": at},
            )
        flags_by_job.setdefault(job_id, []).append(other)
    for job_id, others in flags_by_job.items():
        raw = bind.execute(
            sa.text("SELECT trust_flags FROM jobs WHERE id = :id"), {"id": job_id}
        ).scalar()
        flags = json.loads(raw) if isinstance(raw, str) else (raw or [])
        flags += [f"probable-duplicate:{others[0]}", f"duplicate-batch:{len(others)}"]
        bind.execute(
            sa.text("UPDATE jobs SET trust_flags = :f WHERE id = :id"),
            {"f": json.dumps(flags), "id": job_id},
        )
    op.drop_index("ix_job_duplicates_other", table_name="job_duplicates")
    op.drop_table("job_duplicates")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("origin")
