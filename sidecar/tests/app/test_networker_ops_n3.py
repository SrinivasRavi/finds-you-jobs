"""Covers: Track N3 networking app-integration.

  US-REF-01 — discover potential referrers → persisted candidate contacts
  US-REF-02 — auto-tag discovered contacts by audience
  US-REF-03 — grounded referral draft (FakeEngine — no live LLM)
  US-REF-04 — send via voyager (fake driver — no live LinkedIn) + audit log
  US-NW-09  — reach-out flips contact onto kanban + moves Saved → Seeking Referral
  US-NW-11  — never-accepted-after-cutoff query
  FR-REF-*  — persistence into Contact / ContactJobAssoc / OutreachLog

ZERO live LinkedIn traffic: every discover/send goes through FakeVoyagerDriver
(the `DRIVER_FACTORY` seam), every draft through FakeEngine. The wire stays cold.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.registry import OperationContext, ResolvedEngine
from sidecar.app.registry import networker_ops as ops
from sidecar.modules.networker.types import NetworkerError

from ..modules.networker.fakes import BoomEngine, FakeEngine, FakeVoyagerDriver
from .conftest import migrated_db  # noqa: F401 — fixture


def _nn[T](value: T | None) -> T:
    """Narrow an Optional to its value (test-side assertion helper)."""
    assert value is not None
    return value


@dataclass
class Wired:
    """A seeded DB + the ids of its fixture job / Saved application."""

    db: Database
    job_id: str
    app_id: str

DISCOVER_ROWS = {
    "op": "discover",
    "ok": True,
    "contacts": [
        {"public_identifier": "sarah-tan", "full_name": "Sarah Tan",
         "current_title": "Engineering Manager", "current_company": "Northline",
         "url": "https://www.linkedin.com/in/sarah-tan", "connection_degree": 2},
        {"public_identifier": "raj-io", "full_name": "Raj Io",
         "current_title": "Senior Software Engineer", "current_company": "Northline",
         "url": "https://www.linkedin.com/in/raj-io", "connection_degree": 1},
        {"public_identifier": "lee-tech", "full_name": "Lee Tech",
         "current_title": "Technical Recruiter", "current_company": "Northline",
         "url": "https://www.linkedin.com/in/lee-tech", "connection_degree": 3},
    ],
}


@pytest.fixture
def wired(migrated_db: Database) -> Iterator[Wired]:  # noqa: F811
    """DB seeded with a job + a Saved application; the driver seam is restored
    to the production default on teardown so a fake never leaks across tests."""
    original_factory = ops.DRIVER_FACTORY
    with migrated_db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer. Python, Go, Postgres.")
        job = repos.jobs.create(
            canonical_url="https://ex.co/j/north", title="Backend Engineer",
            company="Northline", location="Remote",
            description=(
                "We are hiring a Backend Engineer to build distributed services "
                "in Python and Go. You will own APIs, work with Postgres and Kafka, "
                "and collaborate with product teams shipping at scale."
            ),
            source_adapter="greenhouse",
        )
        app = repos.applications.create(job.id, column="saved")
        wired = Wired(db=migrated_db, job_id=job.id, app_id=app.id)
    try:
        yield wired
    finally:
        ops.DRIVER_FACTORY = original_factory


def _ctx(db: Database, kind: str, snap: dict, *, engine=None, events=None) -> OperationContext:
    publish = (lambda e: events.append(e)) if events is not None else None
    # A real Operation row so FK-linked writes (OutreachLog.operation_id) hold,
    # exactly as the runner creates one before dispatching an entrypoint.
    with db.repos() as repos:
        op = repos.operations.create(kind, snap)
        op_id = op.id
    return OperationContext(
        kind=kind, input_snapshot=snap, engine=engine, db=db,
        operation_id=op_id, publish=publish,
    )


def _seed(w: Wired, *, company: str = "Northline", with_job: bool = True) -> None:
    """Run discover for the fixture company (fake driver already injected)."""
    snap = {"company": company}
    if with_job:
        snap["job_id"] = w.job_id
    ops.discover_entrypoint(_ctx(w.db, "discover", snap))


# --- discover --------------------------------------------------------------


def test_discover_persists_candidates_tagged_and_off_kanban(wired: Wired) -> None:
    drv = FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv  # inject fake (no live LinkedIn)
    events: list[dict] = []
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline", "job_id": wired.job_id}, events=events)
    )
    assert out.result_ref is not None
    assert out.result_ref["count"] == 3
    assert drv.closed  # driver torn down (NFR-MEM-02)

    with wired.db.repos() as repos:
        contacts = repos.contacts.list()  # excludes archived
        # discovered candidates are OFF the kanban (status 'candidate')
        assert all(c.connection_status == "candidate" for c in contacts)
        by_url = {c.linkedin_url: c for c in contacts}
        # US-REF-02 auto-tag: EM → hm, SWE → peer, Recruiter → recruiter
        assert by_url["https://www.linkedin.com/in/sarah-tan"].audience_tag == "hm"
        assert by_url["https://www.linkedin.com/in/raj-io"].audience_tag == "peer"
        assert by_url["https://www.linkedin.com/in/lee-tech"].audience_tag == "recruiter"
        # US-REF-10 warmth: 1st-degree Raj is warm, others cold
        assert by_url["https://www.linkedin.com/in/raj-io"].warmth == "warm"
        assert by_url["https://www.linkedin.com/in/sarah-tan"].warmth == "cold"
        # per-job assoc created (US-REF-05)
        assocs = repos.contact_job_assocs.list_for_job(wired.job_id)
        assert len(assocs) == 3
    # SSE live-update events for the popup (candidate + discovered)
    phases = [e["payload"]["phase"] for e in events]
    assert phases.count("candidate") == 3
    assert "discovered" in phases


def test_discover_is_idempotent_no_duplicate_rows(wired: Wired) -> None:
    drv = FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    snap = {"company": "Northline", "job_id": wired.job_id}
    ops.discover_entrypoint(_ctx(wired.db, "discover", snap))
    ops.discover_entrypoint(_ctx(wired.db, "discover", snap))  # re-run
    with wired.db.repos() as repos:
        assert len(repos.contacts.list()) == 3  # no dup (US-REF-04 once-per-person)


# --- company resolution (FR-NW-02: L0→L2 currentCompany scoping) -----------

# The fixture job URL is https://ex.co/j/north → employer domain "ex.co".
_DOMAIN_HIT = {
    "op": "resolve-company", "ok": True, "companies": [
        {"urn": "urn:li:fsd_company:111", "company_id": "111", "name": "Northline Decoy",
         "vanity": "northline-decoy", "domain_match": False},
        {"urn": "urn:li:fsd_company:222", "company_id": "222", "name": "Northline",
         "vanity": "northline", "website": "https://ex.co/", "domain_match": True},
    ],
}
_AMBIGUOUS = {
    "op": "resolve-company", "ok": True, "companies": [
        {"urn": "urn:li:fsd_company:111", "company_id": "111", "name": "Northline",
         "vanity": "northline", "domain_match": False},
        {"urn": "urn:li:fsd_company:333", "company_id": "333", "name": "Northline Ventilation",
         "vanity": "northline-hvac", "domain_match": False},
    ],
}
_SINGLE = {
    "op": "resolve-company", "ok": True, "companies": [
        {"urn": "urn:li:fsd_company:999", "company_id": "999", "name": "Northline Systems",
         "vanity": "northline-systems", "domain_match": False},
    ],
}


_URL_HIT = {
    "op": "resolve-company", "ok": True, "companies": [
        {"urn": "urn:li:fsd_company:999", "company_id": "999", "name": "Northline Systems",
         "vanity": "northline-systems", "domain_match": False},
    ],
}
_EMPTY = {"op": "resolve-company", "ok": True, "companies": []}


def _driver_calls(drv: FakeVoyagerDriver, name: str) -> list[tuple]:
    return [c for c in drv.calls if c[0] == name]


def test_resolve_auto_picks_on_domain_match_and_caches(wired: Wired) -> None:
    drv = FakeVoyagerDriver(resolve_result=_DOMAIN_HIT, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline", "job_id": wired.job_id})
    )
    assert out.result_ref is not None
    assert out.result_ref.get("needs_company_confirm") is None
    assert out.result_ref["company_urn"] == "urn:li:fsd_company:222"  # domain-matched entity
    # resolve ran with the employer domain (call tuple: name, url, domain, limit, dry_run).
    rc = _driver_calls(drv, "resolve_company")[0]
    assert rc == ("resolve_company", "Northline", None, "ex.co", 5, False)
    assert _driver_calls(drv, "discover")[0] == (
        "discover", "Northline", 10, "urn:li:fsd_company:222", 1, False,
    )
    with wired.db.repos() as repos:
        row = repos.company_resolutions.get("domain:ex.co")
        assert row is not None and row.company_urn == "urn:li:fsd_company:222"
        assert row.source == "domain"


def test_resolve_ambiguous_returns_needs_confirm_without_discovering(wired: Wired) -> None:
    drv = FakeVoyagerDriver(resolve_result=_AMBIGUOUS, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    events: list[dict] = []
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline", "job_id": wired.job_id}, events=events)
    )
    assert out.result_ref is not None and out.result_ref["needs_company_confirm"] is True
    assert len(out.result_ref["candidates"]) == 2
    assert _driver_calls(drv, "discover") == []  # nothing discovered while awaiting the pick
    with wired.db.repos() as repos:
        assert repos.contacts.list() == []
        assert repos.company_resolutions.get("domain:ex.co") is None  # not cached until confirmed
    assert any(e["payload"]["phase"] == "needs_company_confirm" for e in events)


def test_resolve_single_hit_confirms_not_auto(wired: Wired) -> None:
    # A lone typeahead hit is NOT trusted to auto-pick (it could be a namesake).
    # Only a domain-website match auto-picks; everything else asks the user.
    drv = FakeVoyagerDriver(resolve_result=_SINGLE, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline", "job_id": wired.job_id})
    )
    assert _nn(out.result_ref)["needs_company_confirm"] is True
    assert _driver_calls(drv, "discover") == []


def test_resolve_zero_hits_confirms_never_keyword_searches(wired: Wired) -> None:
    # THE "zip" REGRESSION guard: typeahead finds nothing → ask the user; NEVER
    # silently keyword-search the name (which returned employees of unrelated
    # namesake companies — RR ZIP LIMITED, zipzapzoop, …). No discovery at all.
    drv = FakeVoyagerDriver(resolve_result=_EMPTY, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "zip", "job_id": wired.job_id})
    )
    assert _nn(out.result_ref)["needs_company_confirm"] is True
    assert _nn(out.result_ref)["candidates"] == []
    assert _driver_calls(drv, "discover") == []
    with wired.db.repos() as repos:
        assert repos.contacts.list() == []


def test_paste_company_url_resolves_authoritatively_and_discovers(wired: Wired) -> None:
    drv = FakeVoyagerDriver(resolve_result=_URL_HIT, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(_ctx(wired.db, "discover", {
        "company": "Northline", "job_id": wired.job_id,
        "company_url": "https://www.linkedin.com/company/northline-systems/",
    }))
    assert _nn(out.result_ref)["company_urn"] == "urn:li:fsd_company:999"
    rc = _driver_calls(drv, "resolve_company")[0]  # (name, url, domain, limit, dry_run)
    assert rc[2] == "https://www.linkedin.com/company/northline-systems/"
    assert _driver_calls(drv, "discover")[0][3] == "urn:li:fsd_company:999"
    with wired.db.repos() as repos:
        assert _nn(repos.company_resolutions.get("domain:ex.co")).source == "user"


def test_paste_company_url_unresolved_reconfirms_without_discovering(wired: Wired) -> None:
    drv = FakeVoyagerDriver(resolve_result=_EMPTY, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(_ctx(wired.db, "discover", {
        "company": "Northline", "job_id": wired.job_id,
        "company_url": "https://www.linkedin.com/company/does-not-exist/",
    }))
    assert _nn(out.result_ref)["needs_company_confirm"] is True
    assert _nn(out.result_ref)["url_failed"] is True
    assert _driver_calls(drv, "discover") == []


def test_user_confirmed_urn_is_cached_and_used_no_resolve(wired: Wired) -> None:
    drv = FakeVoyagerDriver(resolve_result=_AMBIGUOUS, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(_ctx(wired.db, "discover", {
        "company": "Northline", "job_id": wired.job_id,
        "company_urn": "urn:li:fsd_company:333", "company_name": "Northline Ventilation",
        "company_vanity": "northline-hvac",
    }))
    assert _nn(out.result_ref)["company_urn"] == "urn:li:fsd_company:333"
    assert _driver_calls(drv, "resolve_company") == []  # user picked → no typeahead
    assert _driver_calls(drv, "discover")[0][3] == "urn:li:fsd_company:333"
    with wired.db.repos() as repos:
        row = _nn(repos.company_resolutions.get("domain:ex.co"))
        assert row.source == "user" and row.company_urn == "urn:li:fsd_company:333"


def test_cache_hit_skips_resolution(wired: Wired) -> None:
    with wired.db.repos() as repos:
        repos.company_resolutions.upsert(
            "domain:ex.co", company_name="Northline", company_urn="urn:li:fsd_company:777",
            source="user",
        )
    drv = FakeVoyagerDriver(resolve_result=_AMBIGUOUS, discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    out = ops.discover_entrypoint(
        _ctx(wired.db, "discover", {"company": "Northline", "job_id": wired.job_id})
    )
    assert _driver_calls(drv, "resolve_company") == []  # cache hit → no typeahead call
    assert _nn(out.result_ref)["company_urn"] == "urn:li:fsd_company:777"


def test_mask_killed_unknown_employer_uses_resolved_entity_name(wired: Wired) -> None:
    # A discovered contact whose current employer couldn't be read → we label it
    # with the RESOLVED entity name (verified by discovery), never the raw search
    # string, and never a stale wrong value (the `or company` mask is gone).
    unknown = {"op": "discover", "ok": True, "contacts": [
        {"public_identifier": "no-co", "full_name": "Pat Doe", "current_title": "SWE",
         "current_company": "", "url": "https://www.linkedin.com/in/no-co", "connection_degree": 2},
    ]}
    drv = FakeVoyagerDriver(resolve_result=_URL_HIT, discover_result=unknown)
    ops.DRIVER_FACTORY = lambda tier: drv
    ops.discover_entrypoint(_ctx(wired.db, "discover", {
        "company": "Northline", "job_id": wired.job_id,
        "company_url": "https://www.linkedin.com/company/northline-systems/",
    }))
    with wired.db.repos() as repos:
        c = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/no-co"))
        assert c.current_company == "Northline Systems"  # resolved entity, not raw "Northline"


def test_candidates_listable_when_resolved_name_differs_from_raw_company(wired: Wired) -> None:
    # Regression (FR-NW-02): a discovered contact is stored under the LinkedIn
    # canonical name ("Northline Systems"), which differs from the raw ATS
    # job.company ("Northline"). The popup roster must still find it — exact-match
    # on job.company alone would hide the whole roster once the mask was removed.
    rows = {"op": "discover", "ok": True, "contacts": [
        {"public_identifier": "kim-lee", "full_name": "Kim Lee", "current_title": "SWE",
         "current_company": "Northline Systems",
         "url": "https://www.linkedin.com/in/kim-lee", "connection_degree": 2},
    ]}
    drv = FakeVoyagerDriver(resolve_result=_URL_HIT, discover_result=rows)
    ops.DRIVER_FACTORY = lambda tier: drv
    ops.discover_entrypoint(_ctx(wired.db, "discover", {
        "company": "Northline", "job_id": wired.job_id,
        "company_url": "https://www.linkedin.com/company/northline-systems/",
    }))
    with wired.db.repos() as repos:
        # The brittle exact-match on the raw company name misses it entirely…
        assert repos.contacts.list(company="Northline") == []
        # …but the roster query finds it by the resolved name (case-insensitive)…
        by_name = repos.contacts.list_for_referrals(
            company_names={"Northline", "northline systems"}, contact_ids=set()
        )
        assert [c.linkedin_url for c in by_name] == ["https://www.linkedin.com/in/kim-lee"]
        # …and by the job association, regardless of how the employer is spelled.
        assoc_ids = {a.contact_id for a in repos.contact_job_assocs.list_for_job(wired.job_id)}
        by_assoc = repos.contacts.list_for_referrals(company_names=set(), contact_ids=assoc_ids)
        assert [c.linkedin_url for c in by_assoc] == ["https://www.linkedin.com/in/kim-lee"]


# --- draft -----------------------------------------------------------------


def test_draft_returns_grounded_message(wired: Wired) -> None:
    drv = FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    ops.DRIVER_FACTORY = lambda tier: drv
    _seed(wired)
    with wired.db.repos() as repos:
        cid = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/raj-io")).id
    engine = ResolvedEngine(engine=FakeEngine(), name="fake", model="fake")
    dsnap = {"contact_id": cid, "job_id": wired.job_id}
    out = ops.draft_entrypoint(_ctx(wired.db, "draft", dsnap, engine=engine))
    assert out.result_ref is not None
    assert "connect" in out.result_ref["message"].lower()
    assert out.result_ref["warmth"] == "warm"  # raj is 1st-degree
    assert out.result_ref["channel"] == "dm"
    assert out.usage is not None and out.usage["model"] == "fake"


def test_draft_propagates_engine_error_verbatim(wired: Wired) -> None:
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired)
    with wired.db.repos() as repos:
        cid = repos.contacts.list()[0].id
    engine = ResolvedEngine(engine=BoomEngine(), name="boom", model="x")
    with pytest.raises(NetworkerError, match="rate limit"):
        dsnap = {"contact_id": cid, "job_id": wired.job_id}
        ops.draft_entrypoint(_ctx(wired.db, "draft", dsnap, engine=engine))


# --- send ------------------------------------------------------------------


def test_send_persists_log_flips_kanban_and_moves_card(wired: Wired) -> None:
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired)
    with wired.db.repos() as repos:
        sarah = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan"))
        cid = sarah.id
    # a successful cold send
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "sent": True, "status": "sent"},
    )
    out = ops.send_entrypoint(_ctx(wired.db, "send", {
        "contact_id": cid, "job_id": wired.job_id, "application_id": wired.app_id,
        "message": "Hi Sarah, would love to connect.",
    }))
    assert out.result_ref is not None and out.result_ref["sent"] is True
    with wired.db.repos() as repos:
        c = _nn(repos.contacts.get(cid))
        assert c.connection_status == "sent"  # cold → Sent column
        assert c.sent_at is not None
        logs = repos.outreach_logs.list_for_contact(cid)
        assert len(logs) == 1 and logs[0].outcome == "sent"
        assert logs[0].channel == "connection_note"
        # US-NW-09 card move: Saved → Seeking Referral on first real send
        app = _nn(repos.applications.get(wired.app_id))
        assert app.column == "seeking_referral"


def test_send_first_degree_lands_accepted(wired: Wired) -> None:
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired, with_job=False)
    with wired.db.repos() as repos:
        raj = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/raj-io"))  # 1st deg, warm
        cid = raj.id
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(
        dm_result={"op": "send-dm", "ok": True, "sent": True, "status": "sent"},
    )
    ops.send_entrypoint(_ctx(wired.db, "send", {"contact_id": cid, "message": "Hi Raj!"}))
    with wired.db.repos() as repos:
        c = _nn(repos.contacts.get(cid))
        assert c.connection_status == "accepted"  # already connected → Accepted
        assert c.accepted_at is not None


def test_send_failure_records_verbatim_reason_no_flip(wired: Wired) -> None:
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired, with_job=False)
    with wired.db.repos() as repos:
        cid = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan")).id
    # voyager reports a not-sent (weekly cap) — never raises, returns sent=False + reason
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "sent": False,
                           "reason": "weekly invitation limit reached"},
    )
    out = ops.send_entrypoint(_ctx(wired.db, "send", {"contact_id": cid, "message": "Hi"}))
    assert out.result_ref is not None and out.result_ref["sent"] is False
    with wired.db.repos() as repos:
        c = _nn(repos.contacts.get(cid))
        assert c.connection_status == "candidate"  # NOT flipped onto kanban
        logs = repos.outreach_logs.list_for_contact(cid)
        assert logs[0].outcome == "failed"
        assert "weekly invitation limit" in logs[0].outcome_detail  # verbatim (NFR-SIDE-04)


def test_send_raised_error_still_writes_outreach_log(wired: Wired) -> None:
    # Regression for the live-dogfood "6 failed sends, outreach_logs empty" bug:
    # a HARD voyager failure (stale selector → SkipProfile) raises NetworkerError,
    # which used to skip the audit-row write entirely. It must now persist a
    # failed OutreachLog with the verbatim reason AND re-raise (op marked failed).
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired, with_job=False)
    with wired.db.repos() as repos:
        cid = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/lee-tech")).id  # 3rd deg
    verbatim = "[voyager] SkipProfile: send-connection skipped: no Connect affordance found"
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(
        raise_on="send_connection", error=NetworkerError("voyager", verbatim),
    )
    events: list[dict] = []
    with pytest.raises(NetworkerError):
        ops.send_entrypoint(
            _ctx(wired.db, "send", {"contact_id": cid, "message": "Hi Lee"}, events=events)
        )
    with wired.db.repos() as repos:
        c = _nn(repos.contacts.get(cid))
        assert c.connection_status == "candidate"  # never flipped onto the kanban
        logs = repos.outreach_logs.list_for_contact(cid)
        assert len(logs) == 1
        assert logs[0].outcome == "failed"
        assert "no Connect affordance found" in logs[0].outcome_detail  # verbatim
        assert logs[0].channel == "connection_note"  # 3rd-degree cold path
    # the popup still learns about the per-contact failure
    assert any(e["payload"]["phase"] == "send_failed" for e in events)


def test_send_dry_run_plans_without_flip(wired: Wired) -> None:
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(wired, with_job=False)
    with wired.db.repos() as repos:
        cid = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan")).id
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(
        connection_result={"op": "send-connection", "ok": True, "blocked_reason": "", "quota": {}},
    )
    ssnap = {"contact_id": cid, "message": "Hi", "dry_run": True}
    out = ops.send_entrypoint(_ctx(wired.db, "send", ssnap))
    assert out.result_ref is not None and out.result_ref["sent"] is False
    with wired.db.repos() as repos:
        c = _nn(repos.contacts.get(cid))
        assert c.connection_status == "candidate"  # dry-run never flips
        assert repos.outreach_logs.list_for_contact(cid)[0].outcome == "pending"


# --- batch-settle card move (FR-NW-03) -------------------------------------


def _seed_two(w: Wired) -> tuple[str, str]:
    """Discover the fixture pool, return two contact ids (a 2nd-degree + 3rd)."""
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(discover_result=DISCOVER_ROWS)
    _seed(w)
    with w.db.repos() as repos:
        a = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/sarah-tan")).id
        b = _nn(repos.contacts.get_by_url("https://www.linkedin.com/in/lee-tech")).id
    return a, b


def _enqueue_batch(w: Wired, contact_ids: list[str], batch_id: str) -> list[str]:
    """Pre-create one queued send op per contact (as `reach_out` does upfront), so
    a later member sees its siblings as real queued rows for settle detection."""
    op_ids: list[str] = []
    with w.db.repos() as repos:
        for cid in contact_ids:
            op = repos.operations.create("send", {
                "contact_id": cid, "job_id": w.job_id, "application_id": w.app_id,
                "batch_id": batch_id, "message": "Hi",
            })
            op_ids.append(op.id)
    return op_ids


def _run_send(w: Wired, op_id: str, contact_id: str, batch_id: str, *, sent: bool) -> None:
    """Dispatch one pre-enqueued send op through the entrypoint. `sent` picks a
    driver that lands or a cap-stop (not-sent, no raise)."""
    result = (
        {"op": "send-connection", "ok": True, "sent": True, "status": "sent"}
        if sent
        else {"op": "send-connection", "ok": True, "sent": False,
              "reason": "weekly invitation limit reached"}
    )
    ops.DRIVER_FACTORY = lambda tier: FakeVoyagerDriver(connection_result=result)
    with w.db.repos() as repos:
        repos.operations.mark_running(op_id)  # the runner marks it running first
    ctx = OperationContext(
        kind="send",
        input_snapshot={
            "contact_id": contact_id, "job_id": w.job_id, "application_id": w.app_id,
            "batch_id": batch_id, "message": "Hi",
        },
        engine=None, db=w.db, operation_id=op_id, publish=None,
    )
    ops.send_entrypoint(ctx)
    with w.db.repos() as repos:  # runner marks it terminal after the entrypoint
        repos.operations.mark_succeeded(op_id)


def test_batch_move_waits_for_settle_then_moves_once(wired: Wired) -> None:
    """FR-NW-03: a Saved card advances to Seeking Referral once, only after the
    whole batch settles — never on the first individual send."""
    a, b = _seed_two(wired)
    batch = "batch-1"
    op_a, op_b = _enqueue_batch(wired, [a, b], batch)
    _run_send(wired, op_a, a, batch, sent=True)  # first send lands…
    with wired.db.repos() as repos:
        # …but op_b is still queued → the card must NOT move yet.
        assert _nn(repos.applications.get(wired.app_id)).column == "saved"
    _run_send(wired, op_b, b, batch, sent=True)  # batch settles
    with wired.db.repos() as repos:
        assert _nn(repos.applications.get(wired.app_id)).column == "seeking_referral"


def test_batch_move_on_mid_batch_cap_stop_with_one_sent(wired: Wired) -> None:
    """A mid-batch cap stop (2nd send not sent) still moves the card once — iff
    ≥1 landed — when the batch settles (FR-NW-03 acceptance)."""
    a, b = _seed_two(wired)
    batch = "batch-cap"
    op_a, op_b = _enqueue_batch(wired, [a, b], batch)
    _run_send(wired, op_a, a, batch, sent=True)  # 1 sent
    with wired.db.repos() as repos:
        assert _nn(repos.applications.get(wired.app_id)).column == "saved"  # not yet settled
    _run_send(wired, op_b, b, batch, sent=False)  # cap stop → batch settles
    with wired.db.repos() as repos:
        assert _nn(repos.applications.get(wired.app_id)).column == "seeking_referral"


def test_batch_no_move_when_nothing_sent(wired: Wired) -> None:
    """A batch that lands zero sends never moves the card (FR-NW-03)."""
    a, b = _seed_two(wired)
    batch = "batch-zero"
    op_a, op_b = _enqueue_batch(wired, [a, b], batch)
    _run_send(wired, op_a, a, batch, sent=False)
    _run_send(wired, op_b, b, batch, sent=False)
    with wired.db.repos() as repos:
        assert _nn(repos.applications.get(wired.app_id)).column == "saved"


# --- repo: US-NW-11 auto-archive query -------------------------------------


def test_never_accepted_before_cutoff(wired: Wired) -> None:
    with wired.db.repos() as repos:
        old = repos.contacts.create(
            "https://www.linkedin.com/in/old", name="Old Sent",
            connection_status="sent", sent_at=now_utc() - timedelta(days=70),
        )
        repos.contacts.create(
            "https://www.linkedin.com/in/fresh", name="Fresh Sent",
            connection_status="sent", sent_at=now_utc() - timedelta(days=5),
        )
        old_id = old.id
    cutoff = now_utc() - timedelta(days=60)
    with wired.db.repos() as repos:
        stale = repos.contacts.list_never_accepted_before(cutoff)
        assert [c.id for c in stale] == [old_id]
