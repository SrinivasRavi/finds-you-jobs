"""Move SmartRecruiters links to the shape the live site serves.

The adapter wrote `/<company>/postings/<id>`, which is a 404 on the site, so Open
posting and Apply landed on an error page (audit-2026-10-14.md section 8.4). Jobs
and tombstones move to `/<company>/<id>`. A job whose new link another job already
holds is the same posting twice: it keeps its old link, and the pair is recorded
in `job_duplicates`, so the duplicate notice asks the user which to keep.

Revision ID: d2b6f9a4c8e1
Revises: c5f1a8d3e6b2
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "d2b6f9a4c8e1"
down_revision = "c5f1a8d3e6b2"
branch_labels = None
depends_on = None

_OLD = re.compile(r"^https://jobs\.smartrecruiters\.com/([^/]+)/postings/(\d+)$")
_NEW = re.compile(r"^https://jobs\.smartrecruiters\.com/([^/]+)/(\d+)$")
_LIKE = "https://jobs.smartrecruiters.com/%"


def _rewrite(table: str, pattern: re.Pattern[str], fmt: str) -> list[tuple[str, str]]:
    """Rewrite matching links; return (id, wanted link) for each one already taken."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(f"SELECT id, canonical_url FROM {table} WHERE canonical_url LIKE :p"),  # noqa: S608
        {"p": _LIKE},
    ).all()
    taken = {url for _id, url in rows}
    blocked: list[tuple[str, str]] = []
    for row_id, url in rows:
        m = pattern.match(url or "")
        if not m:
            continue
        wanted = fmt.format(m[1], m[2])
        if wanted in taken:
            blocked.append((row_id, wanted))
            continue
        conn.execute(
            sa.text(f"UPDATE {table} SET canonical_url = :u WHERE id = :i"),  # noqa: S608
            {"u": wanted, "i": row_id},
        )
        taken.discard(url)
        taken.add(wanted)
    return blocked


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(UTC).replace(tzinfo=None)
    blocked = _rewrite("jobs", _OLD, "https://jobs.smartrecruiters.com/{}/{}")
    for job_id, wanted in blocked:
        other = conn.execute(
            sa.text("SELECT id FROM jobs WHERE canonical_url = :u"), {"u": wanted}
        ).scalar()
        if other is None:
            continue
        newer, older = conn.execute(
            sa.text(
                "SELECT id FROM jobs WHERE id IN (:a, :b) ORDER BY ingested_at DESC, id DESC"
            ),
            {"a": job_id, "b": other},
        ).scalars().all()
        conn.execute(
            sa.text(
                "INSERT OR IGNORE INTO job_duplicates (id, job_id, other_job_id, detected_at)"
                " VALUES (:id, :j, :o, :now)"
            ),
            {"id": str(uuid.uuid4()), "j": newer, "o": older, "now": now},
        )
    _rewrite("tombstones", _OLD, "https://jobs.smartrecruiters.com/{}/{}")


def downgrade() -> None:
    _rewrite("jobs", _NEW, "https://jobs.smartrecruiters.com/{}/postings/{}")
    _rewrite("tombstones", _NEW, "https://jobs.smartrecruiters.com/{}/postings/{}")
