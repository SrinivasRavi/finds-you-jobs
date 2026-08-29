"""F-H2 regression coverage: board + applications hot-path scaling.

Pins the three load-bearing properties of the 2026-07-25 fix:

- `/api/applications` issues a CONSTANT number of SQL statements regardless of
  card count (it was ~12-15 queries PER card, including a full score-op table
  scan each — ~2500+ statements at 200 cards).
- `/api/board` builds the JobDTO (three regexes over the full JD text each)
  only for the returned page — never for every eligible row — and a large feed
  answers fast.
- Both assemblies run off the event loop via `asyncio.to_thread`, so the shell's
  2 s /healthz window can never be crossed by feed size (async-first rule).

Plus repo-level parity: each new batch helper agrees with its per-row
equivalent, so the list endpoints can't drift from the single-card endpoints.

The 2026-08-24 sweep adds the 3 routes that pass missed, same properties:
`POST /api/jobs/trash/empty` (S-C6), `GET /api/discovery/analytics` (S-C7), and
`GET /api/contacts` (S-C8, which was also an unbounded N+1).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event

from sidecar.app.api import dto as dto_module
from sidecar.app.api import routes
from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.db.models import Job
from sidecar.app.main import create_app

TOKEN = "test-token-perf"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


def _db(app: FastAPI) -> Database:
    return app.state.db  # type: ignore[no-any-return]


class _QueryCounter:
    """Counts SQL statements the engine executes (cursor-level listener)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine
        self.count = 0

    def _on_execute(self, *args: Any, **kwargs: Any) -> None:
        self.count += 1

    def __enter__(self) -> _QueryCounter:
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc: Any) -> None:
        event.remove(self._engine, "before_cursor_execute", self._on_execute)


def _seed_cards(app: FastAPI, n: int, start: int = 0) -> None:
    """Tracker cards with every child kind the DTO assembly touches: an
    artifact (+ its op), a contact link, outreach logs, an apply run, and an
    in-flight send op — so a per-card query would show up in the count."""
    with _db(app).repos() as repos:
        for i in range(start, start + n):
            job = repos.jobs.create(
                canonical_url=f"https://jobs.example/{i}",
                title=f"Role {i}",
                company=f"Co {i}",
                location="Remote",
                description="A hybrid role using python.",
                source_adapter="lever",
            )
            application = repos.applications.create(job.id)
            op = repos.operations.create("packet", {"application_id": application.id})
            repos.artifacts.create(
                application.id, kind="tailored_resume", operation_id=op.id
            )
            contact = repos.contacts.create(
                f"https://linkedin.com/in/person-{i}",
                name=f"Person {i}",
                current_company=f"Co {i}",
            )
            repos.contact_job_assocs.upsert(contact.id, job.id)
            repos.outreach_logs.create(
                contact.id,
                job_id=job.id,
                channel="dm",
                outcome="sent",
                batch_id=f"batch-{i}",
            )
            repos.apply_runs.create(application.id)
            repos.operations.create("send", {"job_id": job.id})


def _seed_jobs(app: FastAPI, n: int) -> None:
    description = (
        "We are hiring a senior engineer to build data systems. " * 30
        + "This is a hybrid position; remote and on-site days mix."
    )
    with _db(app).repos() as repos:
        repos.session.add_all(
            [
                Job(
                    canonical_url=f"https://feed.example/{i}",
                    title=f"Engineer {i}",
                    company="Acme",
                    location="NYC",
                    description=description,
                    source_adapter="lever",
                    ingested_at=now_utc() - timedelta(seconds=i),
                )
                for i in range(n)
            ]
        )


# ─── /api/applications: constant query count ────────────────────────────────


def test_applications_query_count_constant_in_card_count(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    _seed_cards(app, 4)
    engine = _db(app).engine
    assert client.get("/api/applications", headers=AUTH).status_code == 200  # warm

    with _QueryCounter(engine) as small:
        resp = client.get("/api/applications", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 4

    _seed_cards(app, 12, start=4)
    with _QueryCounter(engine) as large:
        resp = client.get("/api/applications", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 16

    # 4x the cards, identical statement count: every child lookup is batched
    # and every card-invariant scan is hoisted (F-H2).
    assert large.count == small.count


def test_applications_list_matches_single_card_endpoint(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """The batched list assembly and the single-card endpoint must serialize a
    card identically — the parity net for the batch rewrite."""
    app, client = app_client
    _seed_cards(app, 3)
    listed = client.get("/api/applications", headers=AUTH).json()
    assert len(listed) == 3
    for card in listed:
        single = client.get(f"/api/applications/{card['id']}", headers=AUTH).json()
        assert single == card
        assert card["referralsCount"] == 1
        assert card["job"] is not None


# ─── /api/board: page-bounded DTO builds + off-loop assembly ────────────────


def test_board_builds_dtos_only_for_returned_page(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, client = app_client
    _seed_jobs(app, 300)

    calls = {"n": 0}
    real = dto_module.derive_work_style

    def counting(location: str, description: str) -> str:
        calls["n"] += 1
        return real(location, description)

    monkeypatch.setattr(dto_module, "derive_work_style", counting)
    resp = client.get("/api/board", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 300
    assert len(body["jobs"]) == 50
    # The regex derivation ran only for the 50 returned rows, not all 300.
    assert calls["n"] == 50
    # Parity: the deferred DTO build still derives the chip for the window.
    assert body["jobs"][0]["workStyle"] == "HYBRID"


def test_board_search_and_order_parity_with_deferred_dto_build(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Ordering (FR-JB-01) and shallow/deep search (FR-JB-13) are byte-identical
    after moving sort/search onto ORM rows: scored rows first (desc), unscored
    trail by recency; list_q never matches the JD body, text_q does."""
    app, client = app_client
    base = now_utc()
    with _db(app).repos() as repos:
        low = repos.jobs.create(
            canonical_url="u-low", title="Low", source_adapter="lever",
            description="mentions zanzibar in the body",
        )
        high = repos.jobs.create(
            canonical_url="u-high", title="High", source_adapter="lever"
        )
        old = repos.jobs.create(
            canonical_url="u-old", title="Old unscored", source_adapter="lever"
        )
        new = repos.jobs.create(
            canonical_url="u-new", title="New unscored", source_adapter="lever"
        )
        repos.jobs.update(old.id, ingested_at=base - timedelta(days=2))
        repos.jobs.update(new.id, ingested_at=base - timedelta(days=1))
        repos.job_scores.create(
            job_id=low.id, profile_version=1, score_0_100=10, breakdown_md="bd-low"
        )
        repos.job_scores.create(
            job_id=high.id, profile_version=1, score_0_100=90, breakdown_md="bd-high"
        )
        ids = {"low": low.id, "high": high.id, "old": old.id, "new": new.id}

    body = client.get("/api/board", headers=AUTH).json()
    order = [j["id"] for j in body["jobs"]]
    assert order == [ids["high"], ids["low"], ids["new"], ids["old"]]

    # Shallow search covers only what the row shows — never the JD body.
    shallow = client.get(
        "/api/board", headers=AUTH, params={"list_q": "zanzibar"}
    ).json()
    assert shallow["total"] == 0
    assert shallow["scanStatus"] != "empty"  # a search miss is not an empty scrape
    # Deep search reaches the JD body and the score texts.
    deep = client.get("/api/board", headers=AUTH, params={"text_q": "zanzibar"}).json()
    assert [j["id"] for j in deep["jobs"]] == [ids["low"]]
    deep_score = client.get(
        "/api/board", headers=AUTH, params={"text_q": "bd-high"}
    ).json()
    assert [j["id"] for j in deep_score["jobs"]] == [ids["high"]]


def test_board_large_feed_answers_fast(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """1 200 full-JD rows must answer well inside the shell's 2 s health window
    — and the assembly is off-loop anyway (see the to_thread test below)."""
    app, client = app_client
    _seed_jobs(app, 1200)
    client.get("/api/board", headers=AUTH)  # warm the pool + exclude cache
    started = time.monotonic()
    resp = client.get("/api/board", headers=AUTH, params={"text_q": "hybrid"})
    elapsed = time.monotonic() - started
    assert resp.status_code == 200
    assert resp.json()["total"] == 1200
    assert elapsed < 2.0


def test_board_and_applications_assemble_off_loop(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hot paths hand their whole assembly to `asyncio.to_thread`, so the
    event loop is never held for the feed-sized work (async-first rule).
    `/api/jobs` is bounded (200 rows) but follows the same pattern."""
    app, client = app_client
    _seed_jobs(app, 5)

    offloaded: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spying_to_thread(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        offloaded.append(getattr(fn, "__qualname__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spying_to_thread)
    assert client.get("/api/board", headers=AUTH).status_code == 200
    assert client.get("/api/applications", headers=AUTH).status_code == 200
    assert client.get("/api/jobs", headers=AUTH).status_code == 200
    assert client.get("/api/scan/progress", headers=AUTH).status_code == 200
    assert any(name.startswith("board.") for name in offloaded)
    assert any(name.startswith("list_applications.") for name in offloaded)
    assert any(name.startswith("list_jobs.") for name in offloaded)
    assert any(name.startswith("scan_progress.") for name in offloaded)


# ─── S-C6 / S-C7 / S-C8: the 3 routes the F-H2 pass missed ─────────────────


def _seed_trash(app: FastAPI, n: int) -> None:
    """`n` Trashed jobs, bulk-inserted."""
    with _db(app).repos() as repos:
        repos.session.add_all(
            [
                Job(
                    canonical_url=f"https://trash.example/{i}",
                    title=f"Binned {i}",
                    company="Acme",
                    source_adapter="lever",
                    feed_state="removed",
                )
                for i in range(n)
            ]
        )


def _seed_contacts(
    app: FastAPI, n: int, *, status: str = "accepted", archived: bool = False,
    start: int = 0, logs: bool = True,
) -> None:
    """`n` contacts, each with an outreach log so a per-row log query would show
    up in the statement count."""
    with _db(app).repos() as repos:
        for i in range(start, start + n):
            contact = repos.contacts.create(
                f"https://linkedin.com/in/roster-{i}",
                name=f"Roster {i}",
                current_company="Acme",
                connection_status=status,
                archived_at=now_utc() if archived else None,
            )
            if logs:
                repos.outreach_logs.create(
                    contact.id, channel="dm", outcome="sent",
                    body_sent=f"hello {i}",
                )


def test_trash_analytics_and_contacts_run_off_loop(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-C6 / S-C7 / S-C8: each of the 3 hands its whole assembly to
    `asyncio.to_thread`, so the shell's 2 s /healthz window can't be crossed by
    Trash depth, job count, or roster size (async-first rule)."""
    app, client = app_client
    _seed_jobs(app, 5)
    _seed_trash(app, 5)
    _seed_contacts(app, 3)

    offloaded: list[str] = []
    real_to_thread = asyncio.to_thread

    async def spying_to_thread(fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        offloaded.append(getattr(fn, "__qualname__", repr(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", spying_to_thread)
    assert client.get("/api/contacts", headers=AUTH).status_code == 200
    assert client.get("/api/discovery/analytics", headers=AUTH).status_code == 200
    assert client.post("/api/jobs/trash/empty", headers=AUTH).status_code == 200
    assert any(name.startswith("list_contacts.") for name in offloaded)
    assert any(name.startswith("discovery_analytics.") for name in offloaded)
    assert any(name.startswith("empty_trash.") for name in offloaded)


def test_empty_trash_keeps_its_semantics_at_depth(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """S-C6: moving the loop off the event loop changed nothing it does — every
    Trashed URL is tombstoned exactly once, every row is hard-deleted, and an
    already-tombstoned URL is not written twice."""
    app, client = app_client
    _seed_trash(app, 400)
    with _db(app).repos() as repos:
        repos.tombstones.create("https://trash.example/0", reason="user_delete")

    resp = client.post("/api/jobs/trash/empty", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["tombstoned"] == 400
    assert len(set(body["canonical_urls"])) == 400

    with _db(app).repos() as repos:
        assert repos.jobs.list(feed_state="removed", limit=10_000) == []
        assert all(repos.tombstones.exists(url) for url in body["canonical_urls"])
    # The pre-existing tombstone was reused, never duplicated.
    second = client.post("/api/jobs/trash/empty", headers=AUTH)
    assert second.json() == {"tombstoned": 0, "canonical_urls": []}


def test_contacts_query_count_constant_in_roster_size(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """S-C8: the roster used to run one outreach-log query PER contact. The
    statement count is now flat in roster size."""
    app, client = app_client
    _seed_contacts(app, 4)
    engine = _db(app).engine
    assert client.get("/api/contacts", headers=AUTH).status_code == 200  # warm

    with _QueryCounter(engine) as small:
        resp = client.get("/api/contacts", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 4

    _seed_contacts(app, 12, start=4)
    with _QueryCounter(engine) as large:
        resp = client.get("/api/contacts", headers=AUTH)
    assert resp.status_code == 200
    assert len(resp.json()) == 16

    assert large.count == small.count


def test_contacts_list_matches_single_contact_build(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Parity net for the batch rewrite: the batched roster DTO and the per-row
    `_contact_dto` (still used by create/update) serialize a contact
    identically, including the last-message attribution."""
    app, client = app_client
    _seed_contacts(app, 3)
    listed = client.get("/api/contacts", headers=AUTH).json()
    assert len(listed) == 3

    with _db(app).repos() as repos:
        for card in listed:
            contact = repos.contacts.get(card["id"])
            assert contact is not None
            single = routes._contact_dto(repos, contact)
            assert single.model_dump(mode="json") == card
            assert card["last_message"] is not None
            assert card["last_message_direction"] == "me"


def test_contacts_roster_is_bounded_and_filtered_in_sql(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """S-C8: the roster read has a LIMIT, and every filter is in the query.

    A LIMIT on top of a Python filter bounds the wrong population, which is why
    both filters moved into SQL: 1,200 `candidate` rows (discovery writes one
    per person found) would otherwise fill the cap before a single kanban card
    was reached, and the Deleted-Contacts view would come back empty while the
    archived rows sat past the cap."""
    app, client = app_client
    _seed_contacts(app, 1200, status="candidate", logs=False)
    _seed_contacts(app, 5, status="accepted", start=2000)
    _seed_contacts(app, 3, status="accepted", archived=True, start=3000)

    kanban = client.get("/api/contacts", headers=AUTH).json()
    assert len(kanban) == 5
    assert {c["connection_status"] for c in kanban} == {"accepted"}

    deleted = client.get("/api/contacts?archived=true", headers=AUTH).json()
    assert len(deleted) == 3
    assert all(c["archived_at"] is not None for c in deleted)

    # 1,200 candidates + 5 kanban rows all come back. The cap was 1,000 for one
    # release and nothing in the UI says "1,000 of 1,240", so a roster grown by
    # discovery (about 10 candidates per company watched) silently dropped
    # people — the same defect as the scoring window it shipped beside. It
    # matches the board's 10,000 now.
    everyone = client.get("/api/contacts?include_candidates=true", headers=AUTH).json()
    assert len(everyone) == 1205

    # The LIMIT is still applied AFTER the filters, which is the property that
    # kept the Deleted view honest.
    with _db(app).repos() as repos:
        assert len(repos.contacts.list(include_candidates=True, limit=10)) == 10
        assert len(repos.contacts.list(archived_only=True, limit=10)) == 3


# ─── Repo batch helpers agree with their per-row equivalents ────────────────


@pytest.fixture
def repo_db(tmp_path: Path) -> Iterator[Database]:
    from sidecar.app.db.migrate import upgrade_to_head

    url = f"sqlite:///{tmp_path / 'repo.sqlite'}"
    upgrade_to_head(url)
    db = Database(url)
    try:
        yield db
    finally:
        db.dispose()


def test_outreach_batch_helpers_parity(repo_db: Database) -> None:
    base = now_utc()
    with repo_db.repos() as repos:
        job1 = repos.jobs.create(canonical_url="j1", title="A", source_adapter="lever")
        job2 = repos.jobs.create(canonical_url="j2", title="B", source_adapter="lever")
        job3 = repos.jobs.create(canonical_url="j3", title="C", source_adapter="lever")
        contact = repos.contacts.create("https://linkedin.com/in/x", name="X")
        # job1: a settled batch, then a NEWER solo (batchless) send — the solo
        # send is its own latest batch.
        repos.outreach_logs.create(
            contact.id, job_id=job1.id, channel="dm", outcome="sent",
            batch_id="b1", created_at=base - timedelta(minutes=3),
        )
        repos.outreach_logs.create(
            contact.id, job_id=job1.id, channel="dm", outcome="failed",
            batch_id="b1", created_at=base - timedelta(minutes=2),
        )
        solo = repos.outreach_logs.create(
            contact.id, job_id=job1.id, channel="dm", outcome="sent",
            batch_id=None, created_at=base - timedelta(minutes=1),
        )
        # job2: one open batch of two.
        repos.outreach_logs.create(
            contact.id, job_id=job2.id, channel="dm", outcome="sent",
            batch_id="b2", created_at=base - timedelta(minutes=2),
        )
        repos.outreach_logs.create(
            contact.id, job_id=job2.id, channel="dm", outcome="pending",
            batch_id="b2", created_at=base - timedelta(minutes=1),
        )
        ids = {"j1": job1.id, "j2": job2.id, "j3": job3.id, "solo": solo.id}

    with repo_db.repos() as repos:
        job_ids = [ids["j1"], ids["j2"], ids["j3"]]
        counts = repos.outreach_logs.count_sent_for_jobs(job_ids)
        assert counts.get(ids["j1"], 0) == 2
        assert counts.get(ids["j2"], 0) == 1
        assert counts.get(ids["j3"], 0) == 0
        batches = repos.outreach_logs.latest_batches_for_jobs(job_ids)
        assert [log.id for log in batches[ids["j1"]]] == [ids["solo"]]
        assert [log.outcome for log in batches[ids["j2"]]] == ["sent", "pending"]
        assert ids["j3"] not in batches
        assert repos.outreach_logs.count_sent_for_jobs([]) == {}
        assert repos.outreach_logs.latest_batches_for_jobs([]) == {}


def test_apply_runs_latest_batch_parity(repo_db: Database) -> None:
    base = now_utc()
    with repo_db.repos() as repos:
        job = repos.jobs.create(canonical_url="j1", title="A", source_adapter="lever")
        card1 = repos.applications.create(job.id)
        card2 = repos.applications.create(job.id)
        repos.apply_runs.create(card1.id, started_at=base - timedelta(minutes=2))
        newest = repos.apply_runs.create(
            card1.id, started_at=base - timedelta(minutes=1)
        )
        ids = {"card1": card1.id, "card2": card2.id, "newest": newest.id}

    with repo_db.repos() as repos:
        latest = repos.apply_runs.latest_for_applications(
            [ids["card1"], ids["card2"]]
        )
        assert latest[ids["card1"]].id == ids["newest"]
        assert ids["card2"] not in latest
        # Agrees with the per-card read.
        single = repos.apply_runs.latest_for_application(ids["card1"])
        assert single is not None and single.id == ids["newest"]
        assert repos.apply_runs.latest_for_applications([]) == {}


def test_a_slow_request_is_named_in_the_log(
    app_client: tuple[FastAPI, TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    """S-C26: a sidecar that is merely slow used to leave no evidence at all —
    only a shell log saying it died. Every request is timed now, and one over
    half the health window says which route and how long."""
    import logging

    from sidecar.app import main as main_module

    _app, client = app_client
    with (
        caplog.at_level(logging.WARNING, logger="fyj.sidecar"),
        patch.object(main_module, "SLOW_REQUEST_SECONDS", 0.0),
    ):
        client.get("/api/board", headers=AUTH)

    slow = [r.getMessage() for r in caplog.records if "slow request" in r.getMessage()]
    assert len(slow) == 1
    assert "/api/board" in slow[0] and "GET" in slow[0]
