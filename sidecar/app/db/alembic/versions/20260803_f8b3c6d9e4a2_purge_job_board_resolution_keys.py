"""Purge company resolutions cached under multi-employer job-board domains.

`registry/company_anchor.py` used to treat any non-ATS host as the employer's
own website, so a job scraped from a board like naukri.com minted the shared
cache key `domain:naukri.com` — the first employer confirmed under it was then
silently reused for EVERY job from that board (live 2026-08-02:
`domain:naukri.com` → Coupang, reapplied to a Virtusa job; a re-confirm would
have flipped the poison the other way). The code now refuses to derive an
employer domain from these hosts; this purge deletes the poisoned rows so
affected jobs re-resolve under their per-employer `<adapter>:<slug>` /
`name:…` keys. Data-only; downgrade is a no-op (nothing to restore).

Revision ID: f8b3c6d9e4a2
Revises: e7a1d4c9b2f8
Create Date: 2026-08-03

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f8b3c6d9e4a2'
down_revision: str | None = 'e7a1d4c9b2f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Inlined copy of `_JOB_BOARD_DOMAINS` from
# `sidecar/app/registry/company_anchor.py` as of this revision (migrations do
# not import app code — the module keeps evolving; this pin must not).
_JOB_BOARD_DOMAINS = (
    "naukri.com", "linkedin.com", "seek.com", "seek.com.au",
    "arbeitnow.com", "themuse.com",
    "remoteok.com", "remoteok.io", "remotive.com", "remotive.io",
    "greenhouse.io", "lever.co", "ashbyhq.com", "workable.com",
    "smartrecruiters.com", "myworkdayjobs.com", "bamboohr.com", "breezy.hr",
    "personio.de", "personio.com", "recruitee.com", "teamtailor.com",
    "indeed.com", "foundit.in", "monsterindia.com", "monster.com",
    "glassdoor.com", "glassdoor.co.in", "wellfound.com", "instahyre.com",
    "cutshort.io", "hirist.tech", "hirist.com", "timesjobs.com", "shine.com",
    "simplyhired.com", "ziprecruiter.com", "dice.com", "weworkremotely.com",
    "jobspresso.co", "workingnomads.com", "himalayas.app",
)


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM company_resolutions WHERE resolution_key IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": [f"domain:{d}" for d in _JOB_BOARD_DOMAINS]},
    )


def downgrade() -> None:
    pass  # data purge — nothing to restore
