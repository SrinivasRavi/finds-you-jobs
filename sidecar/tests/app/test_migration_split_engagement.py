"""Covers: S-N5's data migration (`f3a9c1d8e527`) re-homing Engagement rows.

The split is by whose message is last, which the row already stores in
`profile_payload.last_thread_message.direction`. Driven against a real SQLite
file through the real Alembic chain, upgrade and downgrade both.
"""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from alembic import command

from sidecar.app.db.migrate import make_alembic_config, upgrade_to_head

_SEED = [
    (
        "them-last",
        {"last_thread_message": {"direction": "them", "at": "2026-09-01T00:00:00+00:00"}},
    ),
    (
        "me-last",
        {"last_thread_message": {"direction": "me", "at": "2026-09-02T00:00:00+00:00"}},
    ),
    ("no-stamp", {}),
]


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'db.sqlite'}"


def _seed_engagement_rows(url: str) -> None:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        for i, (cid, payload) in enumerate(_SEED):
            conn.execute(
                sa.text(
                    "INSERT INTO contacts (id, linkedin_url, name, current_role,"
                    " current_company, headline, is_first_degree, audience_tag, warmth,"
                    " connection_status, profile_payload, added_at, last_touched_at,"
                    " sent_at, accepted_at) VALUES (:id, :url, :name, '', 'Acme', '', 1,"
                    " 'peer', 'warm', 'engagement', :payload, '2026-08-01', '2026-09-02',"
                    " '2026-08-01', '2026-08-06')"
                ),
                {"id": cid, "url": f"https://x/{i}", "name": cid, "payload": json.dumps(payload)},
            )


def _statuses(url: str) -> dict[str, str]:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(sa.text("SELECT id, connection_status FROM contacts")).all()
    return {str(r[0]): str(r[1]) for r in rows}


def _payload(url: str, contact_id: str) -> dict:
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        raw = conn.execute(
            sa.text("SELECT profile_payload FROM contacts WHERE id = :id"),
            {"id": contact_id},
        ).scalar_one()
    return json.loads(raw or "{}")


def test_engagement_rows_rehome_by_who_wrote_last(tmp_path: Path) -> None:
    url = _url(tmp_path)
    command.upgrade(make_alembic_config(url), "e7b1c95d2a48")
    _seed_engagement_rows(url)
    command.upgrade(make_alembic_config(url), "f3a9c1d8e527")

    statuses = _statuses(url)
    assert statuses["them-last"] == "pending_our_response"
    assert statuses["me-last"] == "pending_their_response"
    # No stamp at all: the row reached Engagement through a reply we saw, so it
    # is treated as answered and waits on them, the status that can ghost.
    assert statuses["no-stamp"] == "pending_their_response"


def test_every_rehomed_row_gets_the_sticky_first_reply(tmp_path: Path) -> None:
    """`first_replied_at` is what makes `accepted` unreachable afterwards."""
    url = _url(tmp_path)
    command.upgrade(make_alembic_config(url), "e7b1c95d2a48")
    _seed_engagement_rows(url)
    command.upgrade(make_alembic_config(url), "f3a9c1d8e527")

    assert _payload(url, "them-last")["first_replied_at"] == "2026-09-01T00:00:00+00:00"
    assert _payload(url, "me-last")["first_replied_at"] == "2026-09-02T00:00:00+00:00"
    # No message stamp: fall back to the acceptance, then the send.
    assert _payload(url, "no-stamp")["first_replied_at"].startswith("2026-08-06")


def test_downgrade_puts_every_row_back(tmp_path: Path) -> None:
    url = _url(tmp_path)
    command.upgrade(make_alembic_config(url), "e7b1c95d2a48")
    _seed_engagement_rows(url)
    command.upgrade(make_alembic_config(url), "f3a9c1d8e527")
    command.downgrade(make_alembic_config(url), "e7b1c95d2a48")

    assert set(_statuses(url).values()) == {"engagement"}


def test_an_install_with_no_engagement_rows_is_untouched(tmp_path: Path) -> None:
    """The maintainer's own database has 0 of them; migrating must be a no-op
    for every other status."""
    url = _url(tmp_path)
    upgrade_to_head(url)
    assert _statuses(url) == {}
