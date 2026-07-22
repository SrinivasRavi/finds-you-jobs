"""Covers: A3 scheduler (architecture §5.5, NFR-LONG-01).

Due-enqueue, boot catch-up (past-due enqueues on tick), and the double-enqueue
guard (a schedule whose prior op is still queued/running is skipped). The runner
here is intentionally *not* started, so submitted ops stay `queued` — giving a
deterministic "prior op still pending" state for the guard.
"""

from __future__ import annotations

from datetime import timedelta

from sidecar.app.db import Database
from sidecar.app.db.base import now_utc
from sidecar.app.registry import OperationContext, OperationOutcome, OperationRegistry
from sidecar.app.runner import OperationRunner
from sidecar.app.scheduler import Scheduler


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
            location="Remote", description="d" * 60, source_adapter="greenhouse",
        )
        repos.preferences.update(thresholds={"auto_score_on_scan": False})
    assert len(plan_score_new(db)) == 1
