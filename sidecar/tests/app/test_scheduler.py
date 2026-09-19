"""Covers: A3 scheduler (architecture section 5.5, NFR-LONG-01).

Due-enqueue, boot catch-up (past-due enqueues on tick), and the double-enqueue
guard (a schedule whose prior op is still queued/running is skipped). The runner
here is intentionally *not* started, so submitted ops stay `queued` — giving a
deterministic "prior op still pending" state for the guard.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.registry import OperationContext, OperationOutcome, OperationRegistry
from sidecar.app.runner import OperationRunner
from sidecar.app.scheduler import Scheduler

# Job fixtures must clear `MIN_JD_CHARS` (200) or the planner skips them as
# too thin to score — the real floor, not a test artefact. Short one-liners were
# never representative: the shortest genuine description on the maintainer's
# install is 464 chars, and 248 rows sit at exactly 0.
REALISTIC_JD = (
    "Backend engineer role. You will build and operate Python services, design Post"
    "greSQL schemas, run workloads on AWS, and review other engineers' work. Requirem"
    "ents: strong Python, solid SQL, and production ownership experience."
)


def _noop(ctx: OperationContext) -> OperationOutcome:
    return OperationOutcome()


def _make(db: Database) -> tuple[OperationRunner, Scheduler]:
    # Runner not started → submit() leaves the op queued (no dispatch).
    runner = OperationRunner(db, registry=OperationRegistry({"scan": _noop}))
    scheduler = Scheduler(db, runner)
    return runner, scheduler


def test_due_schedule_enqueues_and_advances(migrated_db: Database) -> None:
    db = migrated_db
    _runner, scheduler = _make(db)
    now = now_utc()
    with db.repos() as repos:
        sched = repos.schedules.create("scan", 60, next_due_at=now - timedelta(minutes=1))
        sched_id = sched.id

    enqueued = scheduler.tick_once(now=now)
    assert len(enqueued) == 1

    with db.repos() as repos:
        sched = repos.schedules.get(sched_id)
        op = repos.operations.get(enqueued[0])
        assert sched is not None and op is not None
        assert sched.last_enqueued_operation_id == enqueued[0]
        assert sched.next_due_at > now  # advanced by interval
        assert op.kind == "scan" and op.state == "queued"


def test_boot_catch_up_enqueues_past_due(migrated_db: Database) -> None:
    db = migrated_db
    _runner, scheduler = _make(db)
    now = now_utc()
    with db.repos() as repos:
        repos.schedules.create("scan", 1440, next_due_at=now - timedelta(days=2))
    # A single tick (what boot catch-up runs) picks up the past-due schedule.
    assert len(scheduler.tick_once(now=now)) == 1


def test_disabled_schedule_is_skipped(migrated_db: Database) -> None:
    db = migrated_db
    _runner, scheduler = _make(db)
    now = now_utc()
    with db.repos() as repos:
        repos.schedules.create(
            "scan", 60, next_due_at=now - timedelta(minutes=5), enabled=False
        )
    assert scheduler.tick_once(now=now) == []


def test_double_enqueue_guard_skips_pending(migrated_db: Database) -> None:
    db = migrated_db
    _runner, scheduler = _make(db)
    start = now_utc()
    with db.repos() as repos:
        sched = repos.schedules.create(
            "scan", 60, next_due_at=start - timedelta(minutes=1)
        )
        sched_id = sched.id

    first = scheduler.tick_once(now=start)
    assert len(first) == 1  # op enqueued, still queued (runner not started)

    # Later tick: schedule is due again but its prior op is still queued → skip.
    later = start + timedelta(minutes=61)
    second = scheduler.tick_once(now=later)
    assert second == []

    with db.repos() as repos:
        sched = repos.schedules.get(sched_id)
        assert sched is not None
        # Guard still advances next_due so it doesn't hot-loop every tick.
        assert sched.next_due_at > later
        assert sched.last_enqueued_operation_id == first[0]
        assert len(repos.operations.list_by_state("queued")) == 1  # no duplicate


def test_plan_score_new_ignores_retired_auto_score_opt_out(migrated_db: Database) -> None:
    """Scoring is always on (2026-07-22 — the auto_score_on_scan opt-out is
    retired): a stored False from an old profile is ignored and the unscored
    job is still planned. The cost lever is thresholds.scoring_mode now."""
    from sidecar.app.scheduler.planner import plan_score_new

    db = migrated_db
    with db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer.")
        repos.jobs.create(
            canonical_url="https://ex.co/j/opt-out", title="BE", company="Acme",
            location="Remote", description=REALISTIC_JD, source_adapter="greenhouse",
        )
        repos.preferences.update(thresholds={"auto_score_on_scan": False})
    assert len(plan_score_new(db)) == 1


def test_plan_score_new_reaches_past_the_newest_1000_active_jobs(
    migrated_db: Database,
) -> None:
    """Regression: the planner used to fetch `jobs.list(feed_state="active",
    limit=1000)` and only THEN filter that page down to the unscored, so on an
    install with more than 1,000 active jobs the oldest could never be planned
    at any tick — they sat unscored with nothing to show for it. Measured
    before the fix: 1,200 active jobs, 200 genuinely unscored, 0 planned.

    The exclusion happens in SQL now, so a scored job leaves the result set on
    its own and every job is eventually reached.
    """
    from sqlalchemy import insert

    from sidecar.app.db.models import Job
    from sidecar.app.scheduler.planner import plan_score_new

    db = migrated_db
    base = datetime(2026, 1, 1, tzinfo=UTC)
    scored_count, unscored_count = 1_000, 5

    with db.repos() as repos:
        repos.profile.upsert("# Master\n\nBackend engineer.")
        # Bulk-inserted: the point is crossing the old 1,000 boundary, and one
        # ORM round trip per row would make this the slowest test in the file.
        # Index 0 is the OLDEST, so the unscored ones sort last by ingested_at.
        rows = [
            {
                "id": f"job-{i:05d}", "canonical_url": f"https://ex.co/j/{i}",
                "title": f"Backend Engineer {i}", "company": "Acme",
                "location": "Remote", "description": REALISTIC_JD,
                "source_adapter": "greenhouse", "feed_state": "active",
                "ingested_at": base + timedelta(seconds=i),
            }
            for i in range(unscored_count + scored_count)
        ]
        # Every job EXCEPT the oldest `unscored_count` already has an AI score.
        for i, row in enumerate(rows):
            if i >= unscored_count:
                row["llm_score"] = 80
        repos.session.execute(insert(Job), rows)

    planned = plan_score_new(db)
    assert len(planned) == unscored_count
    assert {snapshot["job_id"] for _kind, snapshot in planned} == {
        f"job-{i:05d}" for i in range(unscored_count)
    }
