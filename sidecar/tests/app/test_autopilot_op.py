"""Covers: the autopilot op (audit-2026-10-14.md section 8.10, P9).

Each tick reads the database and enqueues ops as the "app" caller: applies for
good open jobs with no card, and the referral chain (discover, draft, send) for
good cards. Nothing is enqueued for an action the user keeps on "confirm each".
A recording runner writes real op rows and runs nothing: 0 browser, 0 LLM."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.registry import autopilot_op
from sidecar.app.registry.operations import OperationContext

from ._support import seed_application_profile
from .conftest import migrated_db  # noqa: F401 — fixture


class RecordingRunner:
    """`runner.submit` with real op rows and no execution."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self.calls: list[tuple[str, dict[str, Any], str]] = []

    def submit(
        self, kind: str, snapshot: dict[str, Any], *, requested_by: str = "app"
    ) -> str:
        self.calls.append((kind, snapshot, requested_by))
        with self._db.repos() as repos:
            return repos.operations.create(kind, snapshot, requested_by=requested_by).id

    def kinds(self) -> list[str]:
        return [kind for kind, _snap, _by in self.calls]


@pytest.fixture
def runner(migrated_db: Database) -> Iterator[RecordingRunner]:  # noqa: F811
    rec = RecordingRunner(migrated_db)
    original = autopilot_op.RUNNER
    autopilot_op.RUNNER = rec
    try:
        yield rec
    finally:
        autopilot_op.RUNNER = original


def _ready(db: Database, **prefs: Any) -> None:
    with db.repos() as repos:
        repos.profile.upsert("# Ada Lovelace\n\nBackend engineer.")
        repos.preferences.update(**prefs)
    seed_application_profile(db)


def _job(db: Database, n: int, score: int | None, **over: Any) -> str:
    fields: dict[str, Any] = {
        "canonical_url": f"https://boards.example.com/acme/{n}",
        "title": f"Engineer {n}", "company": "Acme", "location": "Remote",
        "description": "Build services.", "source_adapter": "greenhouse",
        "score": score,
    }
    fields.update(over)
    with db.repos() as repos:
        return repos.jobs.create(**fields).id


def _tick(db: Database) -> dict[str, Any]:
    out = autopilot_op.autopilot_entrypoint(
        OperationContext(kind="autopilot", input_snapshot={}, db=db)
    )
    return out.result_ref or {}


# --- off means off -----------------------------------------------------------


def test_with_every_switch_off_a_tick_enqueues_nothing(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db)
    _job(migrated_db, 1, 95)
    _tick(migrated_db)
    assert runner.calls == []


# --- apply ---------------------------------------------------------------------


def test_apply_autopilot_applies_to_good_open_jobs_with_no_card(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_apply=True, autopilot_min_score=80)
    good = _job(migrated_db, 1, 91)
    _job(migrated_db, 2, 79)  # under the bar
    _job(migrated_db, 3, None)  # not scored yet
    _job(migrated_db, 4, 95, closed_at=now_utc())  # no longer listed
    _job(migrated_db, 5, 95, canonical_url=None, content_hash="h5")  # no link
    _job(migrated_db, 6, 95, feed_state="removed")  # in the trash
    carded = _job(migrated_db, 7, 95)
    with migrated_db.repos() as repos:
        repos.applications.create(carded, column="saved")

    report = _tick(migrated_db)

    applies = [(snap, by) for kind, snap, by in runner.calls if kind == "apply"]
    assert len(applies) == 1 and applies[0][1] == "app"
    with migrated_db.repos() as repos:
        card = next(a for a in repos.applications.list() if a.job_id == good)
        assert card.intent == "apply"
        run = repos.apply_runs.get(applies[0][0]["run_id"])
        assert run is not None and run.status == "queued"
        assert run.application_id == card.id == applies[0][0]["application_id"]
    # The packet goes first, as the Save button queues it; apply waits for it.
    assert runner.kinds().index("tailor") < runner.kinds().index("apply")
    assert all(by == "app" for _k, _s, by in runner.calls)
    assert report["applied"] == [good]


def test_apply_autopilot_stops_at_the_daily_cap(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_apply=True, autopilot_applies_per_day=2)
    for n in range(4):
        _job(migrated_db, n, 90 + n)
    with migrated_db.repos() as repos:  # 1 background apply already today
        repos.operations.create("apply", {"application_id": "x"}, requested_by="app")
    _tick(migrated_db)
    assert runner.kinds().count("apply") == 1
    _tick(migrated_db)
    assert runner.kinds().count("apply") == 1  # the cap holds on the next tick


def test_a_click_apply_does_not_spend_the_autopilot_cap(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_apply=True, autopilot_applies_per_day=1)
    _job(migrated_db, 1, 95)
    with migrated_db.repos() as repos:
        repos.operations.create("apply", {"application_id": "x"}, requested_by="user")
    _tick(migrated_db)
    assert runner.kinds().count("apply") == 1


def test_yesterdays_applies_do_not_count(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_apply=True, autopilot_applies_per_day=1)
    _job(migrated_db, 1, 95)
    with migrated_db.repos() as repos:
        old = repos.operations.create("apply", {"application_id": "x"}, requested_by="app")
        old.created_at = now_utc() - timedelta(hours=25)
    _tick(migrated_db)
    assert runner.kinds().count("apply") == 1


def test_apply_autopilot_skips_a_duplicate_of_a_carded_job(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_apply=True)
    first = _job(migrated_db, 1, 95)
    second = _job(migrated_db, 2, 94)
    third = _job(migrated_db, 3, 93)
    with migrated_db.repos() as repos:
        repos.applications.create(first, column="saved")
        repos.duplicates.record(second, [first])
        repos.duplicates.record(third, [second])  # pairs with an uncarded job
    report = _tick(migrated_db)
    # `second` copies a carded job. `third` copies `second`, which this same tick
    # leaves alone, so `third` is the 1 new application.
    assert report["applied"] == [third]


def test_apply_autopilot_waits_for_the_applicant_details(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    with migrated_db.repos() as repos:
        repos.profile.upsert("# Ada\n\nEngineer.")
        repos.preferences.update(autopilot_apply=True)
    _job(migrated_db, 1, 95)
    report = _tick(migrated_db)
    assert runner.calls == []
    assert "applicant details" in report["apply_skipped"]


# --- outreach -----------------------------------------------------------------


def _card(db: Database, n: int, score: int, column: str = "saved") -> tuple[str, str]:
    job_id = _job(db, n, score)
    with db.repos() as repos:
        return job_id, repos.applications.create(job_id, column=column).id


def _contact(
    db: Database, job_id: str, slug: str, *, first: bool, audience: str = "peer",
    status: str = "candidate",
) -> str:
    with db.repos() as repos:
        c = repos.contacts.create(
            f"https://www.linkedin.com/in/{slug}", name=slug.title(),
            is_first_degree=first, audience_tag=audience, connection_status=status,
            current_company="Acme",
        )
        repos.referral_candidates.upsert(c.id, job_id, audience_tag=audience)
        return c.id


def test_outreach_needs_referral_outreach_switched_on(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_invite=True)  # voyager_risk_marker_on stays off
    _card(migrated_db, 1, 95)
    report = _tick(migrated_db)
    assert runner.calls == []
    assert "Referral Outreach" in report["outreach_skipped"]


def test_outreach_discovers_once_for_a_good_card_with_no_candidates(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(migrated_db, autopilot_invite=True, voyager_risk_marker_on=True)
    job_id, _app = _card(migrated_db, 1, 95)
    _card(migrated_db, 2, 60)  # under the bar
    _tick(migrated_db)
    discovers = [snap for kind, snap, _by in runner.calls if kind == "discover"]
    assert [d["job_id"] for d in discovers] == [job_id]
    assert discovers[0]["company"] == "Acme"
    _tick(migrated_db)  # the earlier discover exists, whatever its state
    assert runner.kinds().count("discover") == 1


def test_outreach_drafts_for_the_top_k_whose_channel_is_on(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(
        migrated_db, autopilot_invite=True, voyager_risk_marker_on=True,
        autopilot_contacts_per_job=2,
    )
    job_id, _app = _card(migrated_db, 1, 95)
    dm_only = _contact(migrated_db, job_id, "raj", first=True)  # DM is off
    hm = _contact(migrated_db, job_id, "sarah", first=False, audience="hm")
    peer = _contact(migrated_db, job_id, "lee", first=False, audience="peer")
    _contact(migrated_db, job_id, "rec", first=False, audience="recruiter")
    _contact(migrated_db, job_id, "old", first=False, status="sent")  # reached already
    _tick(migrated_db)
    drafted = {snap["contact_id"] for kind, snap, _by in runner.calls if kind == "draft"}
    assert drafted == {hm, peer}
    assert dm_only not in drafted
    with migrated_db.repos() as repos:
        picked = {rc.contact_id for rc in repos.referral_candidates.list_for_job(job_id)
                  if rc.selected}
        assert picked == {hm, peer}
    _tick(migrated_db)  # the drafts are in flight: no second draft
    assert runner.kinds().count("draft") == 2


def test_outreach_sends_each_finished_draft_once(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(
        migrated_db, autopilot_invite=True, voyager_risk_marker_on=True,
        autopilot_contacts_per_job=1,
    )
    job_id, app_id = _card(migrated_db, 1, 95)
    cid = _contact(migrated_db, job_id, "sarah", first=False, audience="hm")
    _tick(migrated_db)
    with migrated_db.repos() as repos:
        op = next(o for o in repos.operations.list_for_job("draft", {"queued"}, job_id))
        repos.operations.mark_running(op.id)
        repos.operations.mark_succeeded(op.id, result_ref={"message": "Hi Sarah!"})
    _tick(migrated_db)
    sends = [snap for kind, snap, by in runner.calls if kind == "send" and by == "app"]
    assert len(sends) == 1
    assert sends[0]["contact_id"] == cid and sends[0]["message"] == "Hi Sarah!"
    assert sends[0]["application_id"] == app_id and sends[0]["batch_id"]
    _tick(migrated_db)
    assert runner.kinds().count("send") == 1


def test_a_failed_draft_is_not_sent_or_redrafted(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(
        migrated_db, autopilot_invite=True, voyager_risk_marker_on=True,
        autopilot_contacts_per_job=1,
    )
    job_id, _app = _card(migrated_db, 1, 95)
    _contact(migrated_db, job_id, "sarah", first=False, audience="hm")
    _tick(migrated_db)
    with migrated_db.repos() as repos:
        op = next(o for o in repos.operations.list_for_job("draft", {"queued"}, job_id))
        repos.operations.mark_running(op.id)
        repos.operations.mark_failed(op.id, error="no engine")
    _tick(migrated_db)
    assert runner.kinds().count("send") == 0
    assert runner.kinds().count("draft") == 1



def test_a_cap_stopped_send_is_retried_a_day_later_and_a_sent_one_never(
    migrated_db: Database, runner: RecordingRunner  # noqa: F811
) -> None:
    _ready(
        migrated_db, autopilot_invite=True, voyager_risk_marker_on=True,
        autopilot_contacts_per_job=2,
    )
    job_id, _app = _card(migrated_db, 1, 95)
    capped = _contact(migrated_db, job_id, "sarah", first=False, audience="hm")
    done = _contact(migrated_db, job_id, "lee", first=False, audience="peer")
    _tick(migrated_db)
    with migrated_db.repos() as repos:
        for op in repos.operations.list_for_job("draft", {"queued"}, job_id):
            repos.operations.mark_running(op.id)
            repos.operations.mark_succeeded(op.id, result_ref={"message": f"Hi {op.contact_id}"})
    _tick(migrated_db)
    assert runner.kinds().count("send") == 2
    with migrated_db.repos() as repos:
        for op in repos.operations.list_for_job("send", {"queued"}, job_id):
            repos.operations.mark_running(op.id)
            repos.operations.mark_succeeded(op.id)
            assert op.contact_id is not None
            outcome = "failed" if op.contact_id == capped else "sent"
            log = repos.outreach_logs.create(
                op.contact_id, job_id=job_id, channel="connection_note", body_sent="Hi",
                outcome=outcome, outcome_detail="cap_or_backoff", operation_id=op.id,
            )
            log.created_at = now_utc() - timedelta(hours=2)
    _tick(migrated_db)
    assert runner.kinds().count("send") == 2  # too soon for the capped one
    with migrated_db.repos() as repos:
        for log in repos.outreach_logs.list_for_job(job_id):
            log.created_at = now_utc() - timedelta(hours=25)
    _tick(migrated_db)
    resent = [s["contact_id"] for k, s, _b in runner.calls if k == "send"][2:]
    assert resent == [capped]
    assert done not in resent


# --- wiring --------------------------------------------------------------------


def test_boot_seeds_the_tick_and_hands_it_the_runner(app_client) -> None:
    app, _client = app_client
    assert autopilot_op.RUNNER is app.state.runner
    with app.state.db.repos() as repos:
        sched = next(s for s in repos.schedules.list_all() if s.kind == "autopilot")
        assert sched.enabled and sched.interval_minutes == 30
