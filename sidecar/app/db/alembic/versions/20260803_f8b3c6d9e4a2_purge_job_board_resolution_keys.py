"""Purge company resolutions cached under multi-employer job-board domains.

`registry/company_anchor.py` used to treat any non-ATS host as the employer's
own website, so a job scraped from a board like naukri.com minted the shared
cache key `domain:naukri.com` — the first employer confirmed under it was then
silently reused for EVERY job from that board (live 2026-08-02:
`domain:naukri.com` → Coupang, reapplied to a Virtusa job; a re-confirm would
have flipped the poison the other way). The code now refuses to derive an
employer domain from these hosts; this purge deletes the poisoned rows so
affected jobs re-resolve under their per-employer `<adapter>:<slug>` /
`name:…` keys. Also purges keys minted from the naive last-two-label domain
parse under two-level public suffixes (`domain:co.in`, `domain:com.au`, …):
every employer on such a ccTLD site shared one key — the same poison class.
Data-only; downgrade is a no-op (nothing to restore).

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

# Inlined copy of `_TWO_LEVEL_PUBLIC_SUFFIXES` from
# `sidecar/app/registry/company_anchor.py` as of this revision (same
# no-app-imports rule as above). `registrable_domain` used to collapse
# `careers.tataelxsi.co.in` to `co.in`, so a shared `domain:co.in` row could
# exist for every `.co.in` employer — per-suffix poison, purged here.
_TWO_LEVEL_PUBLIC_SUFFIXES = (
    "co.in", "net.in", "org.in", "gov.in", "ac.in",
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "com.br", "com.mx", "com.sg", "com.my",
    "com.hk", "com.tw", "co.jp", "or.jp", "ne.jp", "co.kr",
    "co.za", "org.za", "com.ar", "com.tr", "com.cn", "com.vn",
    "com.ph", "com.id", "co.id", "co.th", "com.pk", "com.eg",
    "com.ng", "co.ke", "com.sa", "com.ae",
)


def upgrade() -> None:
    conn = op.get_bind()
    poisoned = [
        f"domain:{d}" for d in (*_JOB_BOARD_DOMAINS, *_TWO_LEVEL_PUBLIC_SUFFIXES)
    ]
    conn.execute(
        sa.text(
            "DELETE FROM company_resolutions WHERE resolution_key IN :keys"
        ).bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": poisoned},
    )


def downgrade() -> None:
    pass  # data purge — nothing to restore
