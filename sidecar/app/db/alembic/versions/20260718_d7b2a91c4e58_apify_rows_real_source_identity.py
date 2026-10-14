"""Apify-scraped jobs carry their real board identity ("Naukri", never "Apify").

Data only: rows stored as `source_adapter = "apify"` are re-stamped with the
board derived from the URL's host (`adapters/apify.py ACTOR_SOURCE_IDS`);
unmatched hosts keep "apify".

Revision ID: d7b2a91c4e58
Revises: a33c46cd3118
Create Date: 2026-07-18 12:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd7b2a91c4e58'
down_revision: str | None = 'a33c46cd3118'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# host fragment → real source id, mirroring ACTOR_SOURCE_IDS' target boards.
_HOST_SOURCES: list[tuple[str, str]] = [
    ("naukri.com", "naukri"),
    ("linkedin.com", "linkedin"),
    ("seek.com.au", "seek"),
    ("indeed.com", "indeed"),
]


def upgrade() -> None:
    for host, source in _HOST_SOURCES:
        op.execute(
            "UPDATE jobs SET source_adapter = "  # noqa: S608 — constants above, no user input
            f"'{source}' WHERE source_adapter = 'apify' "
            f"AND canonical_url LIKE '%{host}/%'"
        )


def downgrade() -> None:
    # Collapse the real identities back to the adapter family — only for rows
    # on the actor boards (first-party linkedin rows are NOT touched: they were
    # never 'apify' and don't match, because this reverses by the same hosts
    # only for sources this migration could have produced).
    for host, source in _HOST_SOURCES:
        if source == "linkedin":
            # Ambiguous on downgrade (guest/logged-in rows share the host and
            # identity); leaving them as 'linkedin' is the honest choice.
            continue
        op.execute(
            f"UPDATE jobs SET source_adapter = 'apify' "  # noqa: S608
            f"WHERE source_adapter = '{source}' AND canonical_url LIKE '%{host}/%'"
        )
