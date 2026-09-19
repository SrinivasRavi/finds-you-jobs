"""Typed repositories per aggregate (architecture section 5, database-design section 9).

Routes and the runner go through `Repos`, never a raw session. Each sub-repo is
a thin, typed surface over one aggregate; the `Repos` container binds them to a
single session (one short transaction per unit of work — AM4).
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.orm import Session

from .base import now_utc
from .models import (
    APPLY_RUN_ACTIVE_STATUSES,
    CONTACT_SYNCABLE_STATUSES,
    OP_ACTIVE_STATES,
    Application,
    ApplicationEvent,
    ApplyRun,
    Artifact,
    CompanyResolution,
    Contact,
    Document,
    EngineSettings,
    Job,
    LinkedInSearchCursor,
    LinkedInSession,
    MasterProfile,
    Operation,
    OutreachLog,
    ReferralCandidate,
    Schedule,
    Tombstone,
    UserPreferences,
)

# ---------------------------------------------------------------------------
# Lifetime cost aggregate (US-LOG-01 #2 / FR-SET-07)
# ---------------------------------------------------------------------------
# Summing the live ledger alone would silently forget the spend of any op that
# got deleted, so the all-time totals surface = live-ledger sum + a persistent
# aggregate (UserPreferences.ui_state["cost_totals"]). Nothing deletes an
# operation today (S-C22), so the aggregate sits at zero and the live sum
# carries everything; a deletion policy folds the doomed rows in here first.

CostTotals = dict[str, Any]


def _empty_cost_totals() -> CostTotals:
    return {
        "usd": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "operations": 0,
        "failed": 0,
        "by_kind": {},
    }


def _accumulate(
    agg: CostTotals, *, kind: str, state: str, usage: dict[str, Any] | None
) -> None:
    """Fold one operation's usage into a running cost aggregate."""
    usage = usage or {}
    usd = float(usage.get("usd") or 0.0)
    agg["usd"] += usd
    agg["tokens_in"] += int(usage.get("tokens_in") or 0)
    agg["tokens_out"] += int(usage.get("tokens_out") or 0)
    agg["operations"] += 1
    if state == "failed":
        agg["failed"] += 1
    by_kind = agg["by_kind"]
    by_kind[kind] = float(by_kind.get(kind, 0.0)) + usd


def add_cost_totals(base: CostTotals, delta: CostTotals) -> CostTotals:
    """Sum two cost aggregates (by_kind merged key-wise). Pure — no I/O."""
    merged = {
        "usd": float(base.get("usd", 0.0)) + float(delta.get("usd", 0.0)),
        "tokens_in": int(base.get("tokens_in", 0)) + int(delta.get("tokens_in", 0)),
        "tokens_out": int(base.get("tokens_out", 0)) + int(delta.get("tokens_out", 0)),
        "operations": int(base.get("operations", 0)) + int(delta.get("operations", 0)),
        "failed": int(base.get("failed", 0)) + int(delta.get("failed", 0)),
        "by_kind": dict(base.get("by_kind") or {}),
    }
    for kind, usd in (delta.get("by_kind") or {}).items():
        merged["by_kind"][kind] = float(merged["by_kind"].get(kind, 0.0)) + float(usd)
    return merged


class OperationsRepo:
    """The runner's durable queue + the cost ledger."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(self, kind: str, input_snapshot: dict[str, Any]) -> Operation:
        """The one place the subject ids are lifted out of the snapshot, so no
        call site has to remember to pass them twice. Empty string becomes NULL:
        the retired `watch_company` kind wrote `job_id: ""`, and "" is not a
        missing value the way NULL is."""
        op = Operation(
            kind=kind,
            state="queued",
            job_id=input_snapshot.get("job_id") or None,
            contact_id=input_snapshot.get("contact_id") or None,
            batch_id=input_snapshot.get("batch_id") or None,
            input_snapshot=input_snapshot,
        )
        self._s.add(op)
        self._s.flush()
        return op

    def get(self, operation_id: str) -> Operation | None:
        return self._s.get(Operation, operation_id)

    def get_many(self, operation_ids: list[str]) -> dict[str, Operation]:
        """id → Operation for a batch — the artifact→op-state join done with one
        IN query instead of one `get` per artifact (F-H2)."""
        if not operation_ids:
            return {}
        stmt = select(Operation).where(Operation.id.in_(operation_ids))
        return {op.id: op for op in self._s.scalars(stmt)}

    def list_by_state(self, state: str) -> list[Operation]:
        stmt = (
            select(Operation)
            .where(Operation.state == state)
            .order_by(Operation.created_at, Operation.id)
        )
        return list(self._s.scalars(stmt))

    def list_queued_for_dispatch(
        self, *, priority_by_kind: dict[str, int], default_priority: int, limit: int
    ) -> list[Operation]:
        """Queued operations in dispatch order, most urgent first, capped.

        The runner used to read EVERY queued row and sort in Python, so
        enqueueing N operations cost n(n+1)/2 row loads. The cap is what fixes
        that, and the ordering has to move into SQL for the cap to be safe: with
        a date-ordered read, a `LIMIT` would hide an `apply` the user is watching
        behind hundreds of bulk `score` rows. Sorted here, the first `limit` rows
        are always the most urgent ones. `created_at, id` keeps it FIFO within a
        priority band, matching the stable Python sort this replaced."""
        priority = case(priority_by_kind, value=Operation.kind, else_=default_priority)
        stmt = (
            select(Operation)
            .where(Operation.state == "queued")
            .order_by(priority, Operation.created_at, Operation.id)
            .limit(limit)
        )
        return list(self._s.scalars(stmt))

    def list_recent(self, limit: int = 100) -> list[Operation]:
        stmt = select(Operation).order_by(Operation.created_at.desc()).limit(limit)
        return list(self._s.scalars(stmt))

    def live_cost_totals(self) -> CostTotals:
        """The cost aggregate over every operation still in the table (all states;
        in-flight rows carry no usage and contribute only to the op count). Added
        to the pruned aggregate to yield the all-time totals.

        Reads 3 columns rather than building an ORM object per row, which is the
        difference between 95 ms and 1.2 s at 100k operations. Still linear:
        summing every row is inherently linear, and at 24 operations a day it
        stays under a millisecond for years."""
        agg = _empty_cost_totals()
        stmt = select(Operation.kind, Operation.state, Operation.usage)
        for kind, state, usage in self._s.execute(stmt):
            _accumulate(agg, kind=kind, state=state, usage=usage)
        return agg

    def list_by_kind_states(self, kind: str, states: Collection[str]) -> list[Operation]:
        stmt = select(Operation).where(
            Operation.kind == kind, Operation.state.in_(states)
        )
        return list(self._s.scalars(stmt))

    def list_for_job(
        self, kind: str, states: Collection[str], job_id: str | None
    ) -> list[Operation]:
        """Ops of `kind` in `states` about one job, straight off the index.

        `None` means the job-less ones (a reach-out sent from the contact modal
        rather than from a role), which is what the Python match this replaced
        did with a `None` value."""
        subject = (
            Operation.job_id.is_(None) if job_id is None else Operation.job_id == job_id
        )
        stmt = select(Operation).where(
            Operation.kind == kind, Operation.state.in_(states), subject
        )
        return list(self._s.scalars(stmt))

    def list_for_batch(
        self, kind: str, states: Collection[str], batch_id: str
    ) -> list[Operation]:
        """Ops of `kind` in `states` in one send batch, straight off the index."""
        stmt = select(Operation).where(
            Operation.kind == kind,
            Operation.state.in_(states),
            Operation.batch_id == batch_id,
        )
        return list(self._s.scalars(stmt))

    def score_states_for_job(self, job_id: str) -> set[str]:
        """The states of one job's `score` ops. One indexed read, for the routes
        that only ever asked about one job."""
        stmt = select(Operation.state).where(
            Operation.kind == "score", Operation.job_id == job_id
        )
        return set(self._s.scalars(stmt))

    def score_states_by_job(self) -> dict[str, set[str]]:
        """job_id → the set of its `score` operation states, for the board's
        Score-failed derivation (FR-JB-07 / NFR-OFFLINE-02).

        Two indexed columns, no rows built: this used to load every `score` row
        through the ORM and unpack its JSON in Python (1.06 s at 100k rows)."""
        result: dict[str, set[str]] = {}
        stmt = select(Operation.job_id, Operation.state).where(
            Operation.kind == "score", Operation.job_id.is_not(None)
        )
        for job_id, state in self._s.execute(stmt):
            result.setdefault(job_id, set()).add(state)
        return result

    def latest_by_kind(self, kind: str) -> Operation | None:
        """The most-recently-created op of `kind`."""
        stmt = (
            select(Operation)
            .where(Operation.kind == kind)
            .order_by(Operation.created_at.desc())
            .limit(1)
        )
        return self._s.scalars(stmt).first()

    def latest_succeeded_by_kind(self, kind: str) -> Operation | None:
        stmt = (
            select(Operation)
            .where(Operation.kind == kind, Operation.state == "succeeded")
            .order_by(Operation.finished_at.desc())
            .limit(1)
        )
        return self._s.scalars(stmt).first()

    def recent_succeeded_by_kind(self, kind: str, *, limit: int) -> list[Operation]:
        """The newest `limit` succeeded ops of `kind`, newest first — the
        contact-sync stamp/outcome derivation scans these for the last sweep
        that actually probed (a budget-refused sweep still "succeeds")."""
        stmt = (
            select(Operation)
            .where(Operation.kind == kind, Operation.state == "succeeded")
            .order_by(Operation.finished_at.desc())
            .limit(limit)
        )
        return list(self._s.scalars(stmt))

    def any_in_flight(self, kind: str) -> bool:
        stmt = select(Operation.id).where(
            Operation.kind == kind, Operation.state.in_(OP_ACTIVE_STATES)
        )
        return self._s.scalars(stmt).first() is not None

    def mark_running(self, operation_id: str, *, started_at: datetime | None = None) -> None:
        op = self._require(operation_id)
        op.state = "running"
        op.started_at = started_at or now_utc()

    def mark_succeeded(
        self,
        operation_id: str,
        *,
        result_ref: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
        engine: str | None = None,
        model: str | None = None,
    ) -> None:
        op = self._require(operation_id)
        op.state = "succeeded"
        op.result_ref = result_ref
        op.usage = usage
        op.engine = engine
        op.model = model
        op.finished_at = now_utc()

    def mark_failed(
        self,
        operation_id: str,
        *,
        error: str,
        usage: dict[str, Any] | None = None,
        engine: str | None = None,
        model: str | None = None,
    ) -> None:
        op = self._require(operation_id)
        op.state = "failed"
        op.error = error  # verbatim — never swallowed (NFR-SIDE-04)
        if usage is not None:
            op.usage = usage
        if engine is not None:
            op.engine = engine
        if model is not None:
            op.model = model
        op.finished_at = now_utc()

    def mark_cancelled(self, operation_id: str) -> None:
        op = self._require(operation_id)
        op.state = "cancelled"
        op.finished_at = now_utc()

    def requeue(self, operation_id: str) -> None:
        op = self._require(operation_id)
        op.state = "queued"
        op.started_at = None
        op.finished_at = None

    def _require(self, operation_id: str) -> Operation:
        op = self._s.get(Operation, operation_id)
        if op is None:
            raise KeyError(f"operation {operation_id!r} not found")
        return op


class PreferencesRepo:
    """User preferences — single row in P1."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self) -> UserPreferences | None:
        return self._s.scalars(select(UserPreferences).limit(1)).first()

    def get_or_create(self) -> UserPreferences:
        prefs = self.get()
        if prefs is None:
            prefs = UserPreferences()
            self._s.add(prefs)
            self._s.flush()
        return prefs

    def update(self, **fields: Any) -> UserPreferences:
        prefs = self.get_or_create()
        for key, value in fields.items():
            setattr(prefs, key, value)
        return prefs

    def get_cost_totals(self) -> CostTotals:
        """The persisted lifetime aggregate of *pruned* ledger spend (empty when
        nothing has been pruned yet). Lives under `ui_state["cost_totals"]`."""
        prefs = self.get()
        stored = (prefs.ui_state or {}).get("cost_totals") if prefs is not None else None
        return add_cost_totals(_empty_cost_totals(), stored or {})

    def add_cost_totals(self, delta: CostTotals) -> None:
        """Fold a pruned-ops aggregate into the lifetime cost totals. No-op when
        the delta is empty. Reassigns `ui_state` so the JSON column is marked
        dirty (SQLAlchemy does not track in-place mutation of a JSON dict)."""
        if not delta.get("operations"):
            return
        prefs = self.get_or_create()
        ui = dict(prefs.ui_state or {})
        ui["cost_totals"] = add_cost_totals(ui.get("cost_totals") or {}, delta)
        prefs.ui_state = ui


class SchedulesRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self,
        kind: str,
        interval_minutes: int,
        *,
        next_due_at: datetime | None = None,
        enabled: bool = True,
    ) -> Schedule:
        sched = Schedule(
            kind=kind,
            interval_minutes=interval_minutes,
            next_due_at=next_due_at or now_utc(),
            enabled=enabled,
        )
        self._s.add(sched)
        self._s.flush()
        return sched

    def get(self, schedule_id: str) -> Schedule | None:
        return self._s.get(Schedule, schedule_id)

    def list_all(self) -> list[Schedule]:
        return list(self._s.scalars(select(Schedule).order_by(Schedule.next_due_at)))

    def list_due(self, now: datetime) -> list[Schedule]:
        stmt = (
            select(Schedule)
            .where(Schedule.enabled.is_(True), Schedule.next_due_at <= now)
            .order_by(Schedule.next_due_at)
        )
        return list(self._s.scalars(stmt))

    def mark_enqueued(
        self, schedule_id: str, *, operation_id: str | None, next_due_at: datetime
    ) -> None:
        sched = self._s.get(Schedule, schedule_id)
        if sched is None:
            raise KeyError(f"schedule {schedule_id!r} not found")
        sched.last_enqueued_operation_id = operation_id
        sched.next_due_at = next_due_at

    def update(self, schedule_id: str, **fields: Any) -> Schedule:
        sched = self._s.get(Schedule, schedule_id)
        if sched is None:
            raise KeyError(f"schedule {schedule_id!r} not found")
        for key, value in fields.items():
            setattr(sched, key, value)
        return sched


class JobsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, job_id: str) -> Job | None:
        return self._s.get(Job, job_id)

    def get_many(self, job_ids: list[str]) -> dict[str, Job]:
        """id → Job for a batch — one IN query instead of one `get` per tracker
        card (F-H2)."""
        if not job_ids:
            return {}
        stmt = select(Job).where(Job.id.in_(job_ids))
        return {job.id: job for job in self._s.scalars(stmt)}

    def get_by_canonical_url(self, canonical_url: str) -> Job | None:
        stmt = select(Job).where(Job.canonical_url == canonical_url)
        return self._s.scalars(stmt).first()

    def list(
        self, *, feed_state: str | None = "active", limit: int = 200
    ) -> list[Job]:
        stmt = select(Job)
        if feed_state is not None:
            stmt = stmt.where(Job.feed_state == feed_state)
        stmt = stmt.order_by(Job.ingested_at.desc(), Job.id).limit(limit)
        return list(self._s.scalars(stmt))

    def list_active_without_llm_score(
        self, *, limit: int | None = None, max_attempts: int | None = None
    ) -> list[Job]:
        """Active jobs that can still earn an AI score, newest first.

        The AI planner used to page the newest 1,000 active jobs and only THEN
        filter that page down to the unscored, so on an install with more than
        1,000 active jobs the oldest could never be planned at any tick — they
        sat unscored with nothing to show for it. Doing the exclusion in SQL
        makes a scored job leave the result set on its own, so every job is
        eventually reached, and the read is O(unscored) instead of O(all
        active). `limit=None` means no cap, which is the planner's default.

        A keyword floor does NOT count: `scorer_impl` is matched so the LLM can
        still upgrade a job the on-device floor already scored.

        A job whose description is under `MIN_JD_CHARS` is excluded outright. It
        can never earn an LLM score (`score_entrypoint` refuses it and writes a
        0 without calling the engine — see that constant for why), so leaving it
        in would re-plan the same job on every tick forever, each pass writing
        another operation row. On the maintainer's install that's 248 jobs, so
        it would be 248 pointless operations per tick. The predicate is on the
        description rather than on the presence of a refusal row, which means a
        later scan that fills the description in makes the job eligible again on
        its own, with nothing to reset (S-A6).

        `max_attempts` drops jobs that have already failed that many times
        against a live provider (`Job.llm_score_attempts`). A provider outage
        never increments that counter, so an expired key costs no job an attempt
        and the whole backlog returns on the next tick."""
        from sidecar.modules.scorer.deterministic import MIN_JD_CHARS

        stmt = select(Job).where(
            Job.feed_state == "active",
            Job.llm_score.is_(None),
            func.length(func.coalesce(Job.description, "")) >= MIN_JD_CHARS,
        )
        if max_attempts is not None:
            stmt = stmt.where(Job.llm_score_attempts < max_attempts)
        stmt = stmt.order_by(Job.ingested_at.desc(), Job.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._s.scalars(stmt))

    def record_score_failure(self, job_id: str, error: str) -> None:
        """One scoring attempt that REACHED the provider and failed. Only these
        count: a circuit-open rejection never reached anything, so the job keeps
        its budget and comes straight back to the pool."""
        self._s.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                llm_score_attempts=Job.llm_score_attempts + 1,
                llm_score_last_error=error[:2000],
            )
        )

    def count_score_exhausted(self, max_attempts: int) -> int:
        """Active jobs that have spent their whole AI-scoring budget and still
        carry no LLM score — the ones no future tick will ever pick up. This is
        what the ledger's Retry affordance counts, so the button appears only
        when pressing it would actually do something. A job at 1 or 2 attempts
        is NOT counted: it comes back on the next tick on its own."""
        stmt = select(func.count()).select_from(Job).where(
            Job.feed_state == "active",
            Job.llm_score.is_(None),
            Job.llm_score_attempts >= max_attempts,
        )
        return int(self._s.scalar(stmt) or 0)

    def reset_score_attempts(self) -> int:
        """Give every exhausted job its budget back — what Retry does. Returns
        the number of jobs reset. Jobs with no usable description are untouched:
        they never spent an attempt (the planner skips them outright), so there
        is nothing to give back and retrying can't help until a scan fills the
        description in."""
        result = cast(
            "CursorResult[Any]",
            self._s.execute(
                update(Job)
                .where(Job.llm_score_attempts > 0)
                .values(llm_score_attempts=0, llm_score_last_error=None)
            ),
        )
        return int(result.rowcount or 0)

    def list_by_states(self, states: list[str], *, limit: int = 10_000) -> list[Job]:
        """All jobs in any of `states` (the board serves active + expired —
        FR-SYS-03: Expired rows stay on the board, greyed). No silent 200-row cap
        — the board endpoint paginates the full result server-side."""
        stmt = (
            select(Job)
            .where(Job.feed_state.in_(states))
            .order_by(Job.ingested_at.desc(), Job.id)
            .limit(limit)
        )
        return list(self._s.scalars(stmt))

    def create(self, **fields: Any) -> Job:
        job = Job(**fields)
        self._s.add(job)
        self._s.flush()
        return job

    def upsert_by_canonical_url(self, canonical_url: str, **fields: Any) -> Job:
        existing = self.get_by_canonical_url(canonical_url)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            return existing
        return self.create(canonical_url=canonical_url, **fields)

    def update(self, job_id: str, **fields: Any) -> Job:
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        for key, value in fields.items():
            setattr(job, key, value)
        return job

    def set_trash_state(
        self, job_id: str, *, trashed: bool, now: datetime | None = None
    ) -> Job:
        """Move a job into/out of Trash (US-JB-11 / FR-JB-12).

        Trashing stamps `trashed_at` so the 7-day TTL tick (FR-SYS-03/FR-SYS-04)
        can age it out; restoring clears the stamp and returns the row to the
        active feed — its score/history are untouched."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        if trashed:
            job.feed_state = "removed"
            job.trashed_at = now or now_utc()
        else:
            job.feed_state = "active"
            job.trashed_at = None
        return job

    def set_expired(self, job_id: str, *, now: datetime | None = None) -> Job:
        """Age a feed job into `Expired` (FR-SYS-03) — greyed, labelled "Older
        listing", still on the board. Stamps `expired_at` so the 30-day
        hard-delete clock can start. No score/history change."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        job.expired_at = now or now_utc()
        job.feed_state = "expired"
        return job

    def unexpire(self, job_id: str, *, now: datetime | None = None) -> Job:
        """Explicit un-expire (FR-SYS-03): restore an Expired job to the active
        feed and **reset the 14-day timer** by restamping `feed_since`, which is
        the aging clock (it starts at `ingested_at` and only an un-expire moves
        it), so the board's recency sort is preserved."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        job.expired_at = None
        job.feed_since = now or now_utc()
        job.feed_state = "active"
        return job

    def list_trashed_before(self, cutoff: datetime) -> list[Job]:
        """Trashed rows whose TTL has run out. A row with no `trashed_at` is
        excluded and lazily stamped by the caller, so its clock starts once."""
        stmt = select(Job).where(
            Job.feed_state == "removed", Job.trashed_at.is_not(None), Job.trashed_at <= cutoff
        )
        return list(self._s.scalars(stmt))

    def list_expired_before(self, cutoff: datetime) -> list[Job]:
        """Expired rows past the hard-delete cutoff (same null rule as above)."""
        stmt = select(Job).where(
            Job.feed_state == "expired", Job.expired_at.is_not(None), Job.expired_at <= cutoff
        )
        return list(self._s.scalars(stmt))

    def list_active_before(self, cutoff: datetime) -> list[Job]:
        """Active rows whose freshness window has run out."""
        stmt = select(Job).where(Job.feed_state == "active", Job.feed_since <= cutoff)
        return list(self._s.scalars(stmt))

    def stamp_missing_lifecycle_dates(self, *, now: datetime | None = None) -> int:
        """Start the clock on rows that entered a state before it was stamped.
        Returns how many were touched; 0 on every install past the backfill."""
        stamp = now or now_utc()
        touched = 0
        for state, column in (("removed", Job.trashed_at), ("expired", Job.expired_at)):
            result = cast(
                "CursorResult[Any]",
                self._s.execute(
                    update(Job)
                    .where(Job.feed_state == state, column.is_(None))
                    .values({column: stamp})
                ),
            )
            touched += int(result.rowcount or 0)
        return touched

    def delete(self, job_id: str) -> bool:
        """Hard-delete a job row. Scores are columns and go with it, but
        `referral_candidates`, `outreach_logs`, and `applications` hold FKs
        to `jobs.id` — the caller MUST clean those first (use
        `delete_job_cascade` in `persistence.py`). Used by the tombstone
        paths (Empty Trash / Delete forever / TTL eviction)."""
        job = self._s.get(Job, job_id)
        if job is None:
            return False
        self._s.delete(job)
        return True

    def set_score(
        self,
        job_id: str,
        *,
        scorer_impl: str,
        score_0_100: int,
        reasons: list[Any],
        breakdown_md: str,
    ) -> bool:
        """Write one scorer's rating onto the job. Returns True when this is the
        FIRST rating that scorer has produced for this job, which is what the
        priority distribution counts: a recompute must not double count."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        prefix = "llm" if scorer_impl == "scorer-llm" else "keyword"
        is_new = getattr(job, f"{prefix}_score") is None
        setattr(job, f"{prefix}_score", score_0_100)
        setattr(job, f"{prefix}_reasons", list(reasons))
        setattr(job, f"{prefix}_breakdown_md", breakdown_md)
        self._s.flush()
        return is_new

    def ids_without_any_score(self, job_ids: list[str]) -> set[str]:
        """Of these jobs, the ones no scorer has rated — what the keyword floor
        fills so no board row is ever stuck on Pending."""
        if not job_ids:
            return set()
        stmt = select(Job.id).where(
            Job.id.in_(job_ids), Job.llm_score.is_(None), Job.keyword_score.is_(None)
        )
        return set(self._s.scalars(stmt))


class TombstonesRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def exists(self, canonical_url: str) -> bool:
        stmt = select(Tombstone.id).where(Tombstone.canonical_url == canonical_url)
        return self._s.scalars(stmt).first() is not None

    def create(self, canonical_url: str, reason: str = "manual") -> Tombstone:
        tomb = Tombstone(canonical_url=canonical_url, reason=reason)
        self._s.add(tomb)
        self._s.flush()
        return tomb


class ApplicationsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, application_id: str) -> Application | None:
        return self._s.get(Application, application_id)

    def get_many(self, application_ids: list[str]) -> dict[str, Application]:
        """id → Application for a batch — one IN query for the ledger's apply
        subject pass (US-LOG-01 legibility), same shape as JobsRepo.get_many."""
        if not application_ids:
            return {}
        stmt = select(Application).where(Application.id.in_(application_ids))
        return {app.id: app for app in self._s.scalars(stmt)}

    def list(self, *, include_archived: bool = False) -> list[Application]:
        stmt = select(Application)
        if not include_archived:
            stmt = stmt.where(Application.archived_at.is_(None))
        stmt = stmt.order_by(Application.saved_at.desc(), Application.id)
        return list(self._s.scalars(stmt))

    def list_archived_before(self, cutoff: datetime) -> list[Application]:
        """Archived tracker cards whose `archived_at` is past `cutoff` — the
        configurable archived-application purge scope (FR-SYS-06). Terminal-only:
        an active card (archived_at IS NULL) is never in scope."""
        stmt = select(Application).where(
            Application.archived_at.is_not(None), Application.archived_at <= cutoff
        )
        return list(self._s.scalars(stmt))

    def job_ids(self, *, include_archived: bool = True) -> set[str]:
        """The set of job ids that have an Application — i.e. Saved (and later)
        jobs, which the board excludes (US-JB-06). Includes archived by default so
        an archived-then-restored card never double-surfaces on the board."""
        stmt = select(Application.job_id)
        if not include_archived:
            stmt = stmt.where(Application.archived_at.is_(None))
        return set(self._s.scalars(stmt))

    def create(self, job_id: str, **fields: Any) -> Application:
        app = Application(job_id=job_id, **fields)
        self._s.add(app)
        self._s.flush()
        return app

    def update(self, application_id: str, **fields: Any) -> Application:
        app = self._s.get(Application, application_id)
        if app is None:
            raise KeyError(f"application {application_id!r} not found")
        for key, value in fields.items():
            setattr(app, key, value)
        return app

    def delete(self, application_id: str) -> bool:
        app = self._s.get(Application, application_id)
        if app is None:
            return False
        self._s.delete(app)  # ORM delete → cascades to artifacts
        return True


class ArtifactsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, artifact_id: str) -> Artifact | None:
        return self._s.get(Artifact, artifact_id)

    def list_for_application(self, application_id: str) -> list[Artifact]:
        stmt = (
            select(Artifact)
            .where(Artifact.application_id == application_id)
            .order_by(Artifact.created_at, Artifact.id)
        )
        return list(self._s.scalars(stmt))

    def list_for_applications(self, application_ids: list[str]) -> list[Artifact]:
        """`list_for_application` over many cards in one IN query (F-H2). Same
        (created_at, id) order, so per-card grouping preserves the per-card order."""
        if not application_ids:
            return []
        stmt = (
            select(Artifact)
            .where(Artifact.application_id.in_(application_ids))
            .order_by(Artifact.created_at, Artifact.id)
        )
        return list(self._s.scalars(stmt))

    def get_by_operation_id(self, operation_id: str) -> Artifact | None:
        stmt = select(Artifact).where(Artifact.operation_id == operation_id)
        return self._s.scalars(stmt).first()

    def create(self, application_id: str, **fields: Any) -> Artifact:
        artifact = Artifact(application_id=application_id, **fields)
        self._s.add(artifact)
        self._s.flush()
        return artifact

    def update(self, artifact_id: str, **fields: Any) -> Artifact | None:
        artifact = self._s.get(Artifact, artifact_id)
        if artifact is None:
            return None
        for key, value in fields.items():
            setattr(artifact, key, value)
        self._s.flush()
        return artifact


class DocumentsRepo:
    """Uploaded documents attached to cards (FR-TR manual-add). One row per
    attachment; the blob on disk is content-addressed and owned by
    `app.documents`, so identical bytes on 2 cards are 2 rows and 1 file."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, document_id: str) -> Document | None:
        return self._s.get(Document, document_id)

    def list_for_application(self, application_id: str) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.application_id == application_id)
            .order_by(Document.doc_type, Document.created_at)
        )
        return list(self._s.scalars(stmt))

    def list_for_applications(self, application_ids: list[str]) -> list[Document]:
        """`list_for_application` over many cards in one IN query (F-H2). Same
        (doc_type, created_at) order, so per-card grouping preserves the order."""
        if not application_ids:
            return []
        stmt = (
            select(Document)
            .where(Document.application_id.in_(application_ids))
            .order_by(Document.doc_type, Document.created_at)
        )
        return list(self._s.scalars(stmt))

    def set(
        self,
        application_id: str,
        doc_type: str,
        *,
        sha256: str,
        byte_size: int,
        mime_type: str,
        original_filename: str,
    ) -> tuple[Document, str | None]:
        """Fill this card's `doc_type` slot, replacing whatever was there — one
        resume and one cover per card. Returns the row and the sha256 of the
        blob the replacement orphaned, if any, so the caller can unlink it."""
        existing = self._s.scalars(
            select(Document).where(
                Document.application_id == application_id,
                Document.doc_type == doc_type,
            )
        ).first()
        if existing is not None:
            replaced = existing.sha256
            existing.sha256 = sha256
            existing.byte_size = byte_size
            existing.mime_type = mime_type
            existing.original_filename = original_filename
            self._s.flush()
            return existing, self._orphaned(replaced)
        doc = Document(
            application_id=application_id,
            doc_type=doc_type,
            sha256=sha256,
            byte_size=byte_size,
            mime_type=mime_type,
            original_filename=original_filename,
        )
        self._s.add(doc)
        self._s.flush()
        return doc, None

    def delete(self, application_id: str, doc_type: str) -> str | None:
        """Detach this card's `doc_type` document (the ✕ in the editor). Returns
        the sha256 whose blob is now unreferenced, or None if another card still
        uses those bytes or nothing was attached."""
        row = self._s.scalars(
            select(Document).where(
                Document.application_id == application_id,
                Document.doc_type == doc_type,
            )
        ).first()
        if row is None:
            return None
        sha = row.sha256
        self._s.delete(row)
        self._s.flush()
        return self._orphaned(sha)

    def delete_for_application(self, application_id: str) -> list[str]:
        """Remove a purged card's documents. Returns every sha256 left with no
        row pointing at it, so the caller unlinks exactly those blobs. Before the
        table merge nothing collected these at all and each purge leaked a row
        and a file permanently (S-C37)."""
        rows = self.list_for_application(application_id)
        if not rows:
            return []
        shas = {row.sha256 for row in rows}
        for row in rows:
            self._s.delete(row)
        self._s.flush()
        return [sha for sha in sorted(shas) if self._orphaned(sha) is not None]

    def _orphaned(self, sha256: str) -> str | None:
        """`sha256` if no row references it any more, else None. The reference
        count that the 2-table shape had no way to compute."""
        still = self._s.scalars(
            select(Document.id).where(Document.sha256 == sha256).limit(1)
        ).first()
        return None if still is not None else sha256


class ApplicationEventsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def create(
        self, application_id: str, kind: str, detail: dict[str, Any] | None = None
    ) -> ApplicationEvent:
        event = ApplicationEvent(
            application_id=application_id, kind=kind, detail=detail or {}
        )
        self._s.add(event)
        self._s.flush()
        return event

    def list_for_application(self, application_id: str) -> list[ApplicationEvent]:
        stmt = (
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application_id)
            .order_by(ApplicationEvent.created_at, ApplicationEvent.id)
        )
        return list(self._s.scalars(stmt))

    def delete_for_application(self, application_id: str) -> int:
        """Remove every event of an application (`foreign_keys=ON` forbids
        orphans when the card is purged). Returns the row count deleted."""
        result = self._s.execute(
            delete(ApplicationEvent).where(
                ApplicationEvent.application_id == application_id
            )
        )
        return cast("CursorResult[Any]", result).rowcount


class ProfileRepo:
    """The master profile — single active row in P1."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get_current(self) -> MasterProfile | None:
        stmt = select(MasterProfile).order_by(MasterProfile.version.desc()).limit(1)
        return self._s.scalars(stmt).first()

    def upsert(self, resume_markdown: str) -> MasterProfile:
        current = self.get_current()
        if current is None:
            profile = MasterProfile(resume_markdown=resume_markdown, version=1)
            self._s.add(profile)
            self._s.flush()
            return profile
        if current.resume_markdown == resume_markdown:
            # Unchanged content is a no-op version-wise: scores are cached per
            # version, so a phantom bump would mark every score stale and
            # trigger a pointless "Re-score N jobs?" prompt (2026-07-23).
            return current
        current.resume_markdown = resume_markdown
        current.version += 1
        return current

    def set_application_profile(self, profile: dict[str, Any] | None) -> MasterProfile:
        """Write the structured application-profile record (FR-APP-01) onto the
        current master. Raises when no master exists yet."""
        current = self.get_current()
        if current is None:
            raise LookupError("no master profile to attach an application profile to")
        current.application_profile = profile
        return current


class EngineSettingsRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def list(self) -> list[EngineSettings]:
        return list(self._s.scalars(select(EngineSettings).order_by(EngineSettings.engine)))

    def get(self, settings_id: str) -> EngineSettings | None:
        return self._s.get(EngineSettings, settings_id)

    def get_by_engine(self, engine: str) -> EngineSettings | None:
        return self._s.scalars(
            select(EngineSettings).where(EngineSettings.engine == engine)
        ).first()

    def create(self, engine: str, **fields: Any) -> EngineSettings:
        row = EngineSettings(engine=engine, **fields)
        self._s.add(row)
        self._s.flush()
        return row

    def update(self, settings_id: str, **fields: Any) -> EngineSettings | None:
        row = self._s.get(EngineSettings, settings_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        self._s.flush()
        return row

    def delete_by_engine(self, engine: str) -> bool:
        result = cast(
            "CursorResult[Any]",
            self._s.execute(delete(EngineSettings).where(EngineSettings.engine == engine)),
        )
        return result.rowcount > 0


class CompanyResolutionsRepo:
    """Cached name → LinkedIn company-entity resolutions (FR-NW-02).

    Keyed by the stable per-employer `resolution_key` (see
    `registry/company_anchor.py`), so one typeahead + one confirm choice is
    reused across every job of the same employer."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, resolution_key: str) -> CompanyResolution | None:
        if not resolution_key:
            return None
        return self._s.scalars(
            select(CompanyResolution).where(
                CompanyResolution.resolution_key == resolution_key
            )
        ).first()

    def upsert(
        self,
        resolution_key: str,
        *,
        company_name: str,
        company_urn: str,
        company_vanity: str = "",
        industry: str = "",
        source: str = "user",
    ) -> CompanyResolution:
        row = self.get(resolution_key)
        if row is None:
            row = CompanyResolution(resolution_key=resolution_key)
            self._s.add(row)
        row.company_name = company_name
        row.company_urn = company_urn
        row.company_vanity = company_vanity
        row.industry = industry
        row.source = source
        self._s.flush()
        return row


class ContactsRepo:
    """Person-level outreach targets (US-REF-05). Identity key = linkedin_url."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, contact_id: str) -> Contact | None:
        return self._s.get(Contact, contact_id)

    def get_many(self, contact_ids: list[str]) -> dict[str, Contact]:
        """id → Contact for a batch — one IN query for the ledger's send/draft
        subject pass (US-LOG-01 legibility), same shape as JobsRepo.get_many."""
        if not contact_ids:
            return {}
        stmt = select(Contact).where(Contact.id.in_(contact_ids))
        return {contact.id: contact for contact in self._s.scalars(stmt)}

    def get_by_url(self, linkedin_url: str) -> Contact | None:
        stmt = select(Contact).where(Contact.linkedin_url == linkedin_url)
        return self._s.scalars(stmt).first()

    def list(
        self,
        *,
        company: str | None = None,
        archived_only: bool = False,
        include_candidates: bool = True,
        limit: int = 10_000,
    ) -> list[Contact]:
        """The kanban roster, most-recently-touched first and capped (S-C8).

        Every filter the roster route applies is in SQL, because a LIMIT on top
        of a Python filter bounds the wrong population: the "Deleted Contacts"
        view could show nothing while archived rows existed past the cap, and a
        big `candidate` pile (discovery writes one row per person found) could
        eat the whole budget before a single kanban card was reached.

        10,000, matching the board's `list_by_states`. The first cut capped at
        1,000 and nothing in the UI says "1,000 of 1,240", so a discovery-grown
        roster (about 10 candidate rows per company watched) would silently drop
        people past 100 companies — the same class of defect as the scoring
        window it shipped beside. The batched last-message query removed the
        real cost of a big roster; this cap now bounds DTO building only."""
        stmt = select(Contact)
        if archived_only:
            stmt = stmt.where(Contact.archived_at.is_not(None))
        else:
            stmt = stmt.where(Contact.archived_at.is_(None))
        if not include_candidates:
            stmt = stmt.where(Contact.connection_status != "candidate")
        if company:
            stmt = stmt.where(Contact.current_company == company)
        stmt = stmt.order_by(Contact.last_touched_at.desc(), Contact.id).limit(limit)
        return list(self._s.scalars(stmt))

    def list_for_referrals(
        self, *, company_names: set[str], contact_ids: set[str]
    ) -> list[Contact]:
        """The find-referrals popup roster for a role (US-NW-09 / FR-NW-02).

        A contact belongs on it if it is at the target company — matched
        **case-insensitively against ANY of `company_names`** (the raw ATS
        `job.company` AND the resolved LinkedIn entity name, which often differ,
        e.g. `hopper` vs `Hopper`) — OR it is already associated with this job
        (`contact_ids`, the reliable link discovery writes regardless of how the
        employer string is spelled). Excludes archived. This replaces a brittle
        exact-match on `job.company` that could hide a whole discovered roster."""
        lowered = {n.strip().lower() for n in company_names if n and n.strip()}
        conds = []
        if lowered:
            conds.append(func.lower(Contact.current_company).in_(lowered))
        if contact_ids:
            conds.append(Contact.id.in_(contact_ids))
        if not conds:
            return []
        stmt = (
            select(Contact)
            .where(Contact.archived_at.is_(None))
            .where(or_(*conds))
            .order_by(Contact.last_touched_at.desc(), Contact.id)
        )
        return list(self._s.scalars(stmt))

    def create(self, linkedin_url: str, **fields: Any) -> Contact:
        contact = Contact(linkedin_url=linkedin_url, **fields)
        self._s.add(contact)
        self._s.flush()
        return contact

    def upsert_by_url(self, linkedin_url: str, **fields: Any) -> Contact:
        """First-seen wins for identity fields, but refresh the mutable ones.

        Discovery re-running for the same company must not duplicate a contact
        (US-REF-04 "at most once per person"), and an already-known 1st-degree
        contact surfaces warm rather than as a new cold prospect (US-REF-01)."""
        existing = self.get_by_url(linkedin_url)
        if existing is not None:
            for key, value in fields.items():
                # Never downgrade a manually-advanced connection_status back to a
                # discovery default; discovery only refreshes profile-ish fields.
                if key == "connection_status":
                    continue
                setattr(existing, key, value)
            return existing
        return self.create(linkedin_url, **fields)

    def update(self, contact_id: str, **fields: Any) -> Contact:
        contact = self._s.get(Contact, contact_id)
        if contact is None:
            raise KeyError(f"contact {contact_id!r} not found")
        for key, value in fields.items():
            setattr(contact, key, value)
        return contact

    def list_never_accepted_before(self, cutoff: datetime) -> list[Contact]:
        """Sent-but-never-accepted connections older than `cutoff` (US-NW-11)."""
        stmt = select(Contact).where(
            Contact.archived_at.is_(None),
            Contact.connection_status == "sent",
            Contact.accepted_at.is_(None),
            Contact.sent_at.is_not(None),
            Contact.sent_at <= cutoff,
        )
        return list(self._s.scalars(stmt))

    # Live-kanban statuses the contact-status sync engine probes (US-NW-12 /
    # FR-NW-15). `candidate` (off the kanban), `converted` (the user's sacred
    # referral record — never auto-touched), and `ghosted` (terminal for auto —
    # revival is a manual drag) are excluded, so sync traffic stays bounded.
    _SYNCABLE_STATUSES = CONTACT_SYNCABLE_STATUSES

    def list_syncable(self, *, limit: int) -> list[Contact]:
        """The next `limit` contacts due for a status-sync probe (US-NW-12).

        Ordered by `last_touched_at` ASC (least-recently-touched first): each
        probe bumps `last_touched_at`, so the probed contacts rotate to the back
        and the whole live set is swept fairly over successive ticks — a natural
        round-robin cursor with no extra bookkeeping column."""
        stmt = (
            select(Contact)
            .where(Contact.archived_at.is_(None))
            .where(Contact.connection_status.in_(self._SYNCABLE_STATUSES))
            .order_by(Contact.last_touched_at.asc(), Contact.id)
            .limit(limit)
        )
        return list(self._s.scalars(stmt))

    def list_archived_before(self, cutoff: datetime) -> list[Contact]:
        """Archived (deleted) contacts whose `archived_at` is past `cutoff` — the
        permanent-purge scope (FR-SYS-06 configurable retention)."""
        stmt = select(Contact).where(
            Contact.archived_at.is_not(None), Contact.archived_at <= cutoff
        )
        return list(self._s.scalars(stmt))

    def purge(self, contact_id: str) -> bool:
        """Permanently delete a contact + its per-role asks + outreach history
        (`foreign_keys=ON` forbids orphans). Used only by the archived-contact
        retention purge — a deliberate, configurable, terminal cleanup."""
        contact = self._s.get(Contact, contact_id)
        if contact is None:
            return False
        self._s.execute(delete(ReferralCandidate).where(ReferralCandidate.contact_id == contact_id))
        self._s.execute(delete(OutreachLog).where(OutreachLog.contact_id == contact_id))
        self._s.delete(contact)
        return True


class ReferralCandidatesRepo:
    """People put forward as referral candidates for a role (US-REF-05). One row
    per (person, job): someone asked about 3 jobs is 1 contact and 3 rows."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, contact_id: str, job_id: str) -> ReferralCandidate | None:
        stmt = select(ReferralCandidate).where(
            ReferralCandidate.contact_id == contact_id,
            ReferralCandidate.job_id == job_id,
        )
        return self._s.scalars(stmt).first()

    def list_for_job(self, job_id: str) -> list[ReferralCandidate]:
        stmt = select(ReferralCandidate).where(ReferralCandidate.job_id == job_id)
        return list(self._s.scalars(stmt))

    def job_ids_with_contacts(self, job_ids: list[str]) -> set[str]:
        """The subset of `job_ids` with at least one contact link — the tracker
        list's has-candidates flag in one IN query instead of one query per
        card (F-H2)."""
        if not job_ids:
            return set()
        stmt = (
            select(ReferralCandidate.job_id)
            .where(ReferralCandidate.job_id.in_(job_ids))
            .distinct()
        )
        return set(self._s.scalars(stmt))

    def list_for_contact(self, contact_id: str) -> list[ReferralCandidate]:
        stmt = select(ReferralCandidate).where(ReferralCandidate.contact_id == contact_id)
        return list(self._s.scalars(stmt))

    def selected_contact_ids(self, job_id: str) -> set[str]:
        """The contacts currently selected for this role (FR-NW-01) — restores the
        find-referrals popup selection when a `pending` popup is reopened."""
        stmt = select(ReferralCandidate.contact_id).where(
            ReferralCandidate.job_id == job_id, ReferralCandidate.selected.is_(True)
        )
        return set(self._s.scalars(stmt))

    def upsert(
        self, contact_id: str, job_id: str, **fields: Any
    ) -> ReferralCandidate:
        existing = self.get(contact_id, job_id)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            return existing
        assoc = ReferralCandidate(contact_id=contact_id, job_id=job_id, **fields)
        self._s.add(assoc)
        self._s.flush()
        return assoc

    def delete_for_job(self, job_id: str) -> int:
        """Remove every referral-candidate row for a job (`foreign_keys=ON`
        forbids orphans when the job is deleted). Returns the row count."""
        result = self._s.execute(
            delete(ReferralCandidate).where(ReferralCandidate.job_id == job_id)
        )
        return cast("CursorResult[Any]", result).rowcount


class OutreachLogsRepo:
    """Per-message audit (database-design section 5)."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self, log_id: str) -> OutreachLog | None:
        return self._s.get(OutreachLog, log_id)

    def create(self, contact_id: str, **fields: Any) -> OutreachLog:
        log = OutreachLog(contact_id=contact_id, **fields)
        self._s.add(log)
        self._s.flush()
        return log

    def list_for_contact(self, contact_id: str) -> list[OutreachLog]:
        stmt = (
            select(OutreachLog)
            .where(OutreachLog.contact_id == contact_id)
            .order_by(OutreachLog.created_at, OutreachLog.id)
        )
        return list(self._s.scalars(stmt))

    def latest_for_contacts(self, contact_ids: list[str]) -> dict[str, OutreachLog]:
        """contact_id → its newest OutreachLog — the roster's per-card "last
        message" fallback done with one IN query instead of one `list_for_contact`
        per contact (S-C8, the F-H2 batch pattern). Ordered the same way
        `list_for_contact` is, so the row picked here is the row the per-contact
        read's `logs[-1]` picks. Contacts with no logs are absent from the map."""
        if not contact_ids:
            return {}
        stmt = select(OutreachLog).where(OutreachLog.contact_id.in_(contact_ids))
        latest: dict[str, OutreachLog] = {}
        for log in self._s.scalars(stmt):
            current = latest.get(log.contact_id)
            if current is None or (log.created_at, log.id) > (
                current.created_at, current.id
            ):
                latest[log.contact_id] = log
        return latest

    def count_sent_for_jobs(self, job_ids: list[str]) -> dict[str, int]:
        """Reaches actually sent per role (US-NW-09 per-role reached count) —
        one grouped IN query for a batch of roles (F-H2). Roles with no sent
        reaches are absent from the map (read with `.get(job_id, 0)`)."""
        if not job_ids:
            return {}
        stmt = (
            select(OutreachLog.job_id, func.count())
            .where(OutreachLog.job_id.in_(job_ids), OutreachLog.outcome == "sent")
            .group_by(OutreachLog.job_id)
        )
        return {job_id: int(n) for job_id, n in self._s.execute(stmt)}

    def list_for_job(self, job_id: str) -> list[OutreachLog]:
        stmt = select(OutreachLog).where(OutreachLog.job_id == job_id)
        return list(self._s.scalars(stmt))

    def latest_batches_for_jobs(self, job_ids: list[str]) -> dict[str, list[OutreachLog]]:
        """The OutreachLog rows of the *latest* reach-out batch per role, used
        to derive `referralsState` (FR-NW-01) — one IN query for a batch of
        roles (F-H2). The batch is keyed by `batch_id`; a NULL batch_id
        (legacy/manual single send) is its own settled batch. The latest batch =
        the one containing the most-recently-created log. Roles with no logs are
        absent from the map."""
        if not job_ids:
            return {}
        stmt = select(OutreachLog).where(OutreachLog.job_id.in_(job_ids))
        by_job: dict[str, list[OutreachLog]] = {}
        for log in self._s.scalars(stmt):
            if log.job_id is None:  # unreachable given the IN filter; typing only
                continue
            by_job.setdefault(log.job_id, []).append(log)
        batches: dict[str, list[OutreachLog]] = {}
        for job_id, logs in by_job.items():
            logs.sort(key=lambda log: (log.created_at, log.id))
            newest = logs[-1]
            if newest.batch_id is None:
                batches[job_id] = [newest]  # a solo (batchless) send is its own settled batch
            else:
                batches[job_id] = [log for log in logs if log.batch_id == newest.batch_id]
        return batches

    def nullify_for_job(self, job_id: str) -> int:
        """SET job_id = NULL on every outreach log for a deleted job
        (`foreign_keys=ON`). Outreach history is audit data — we keep the
        rows but sever the FK so the job row can go. Returns the row count."""
        result = self._s.execute(
            update(OutreachLog)
            .where(OutreachLog.job_id == job_id)
            .values(job_id=None)
        )
        return cast("CursorResult[Any]", result).rowcount


class LinkedInSessionRepo:
    """Single-row LinkedIn session state (database-design section 5)."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self) -> LinkedInSession | None:
        return self._s.scalars(select(LinkedInSession).limit(1)).first()

    def get_or_create(self) -> LinkedInSession:
        row = self.get()
        if row is None:
            row = LinkedInSession()
            self._s.add(row)
            self._s.flush()
        return row

    def update(self, **fields: Any) -> LinkedInSession:
        row = self.get_or_create()
        for key, value in fields.items():
            setattr(row, key, value)
        return row


class LinkedInSearchCursorRepo:
    """Single-row pagination cursor for the logged-in job search (Fresh search /
    Next page). Same single-row pattern as `LinkedInSessionRepo`."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def get(self) -> LinkedInSearchCursor | None:
        return self._s.scalars(select(LinkedInSearchCursor).limit(1)).first()

    def get_or_create(self) -> LinkedInSearchCursor:
        row = self.get()
        if row is None:
            row = LinkedInSearchCursor()
            self._s.add(row)
            self._s.flush()
        return row

    def update(self, **fields: Any) -> LinkedInSearchCursor:
        row = self.get_or_create()
        for key, value in fields.items():
            setattr(row, key, value)
        return row

    def clear(self) -> None:
        """Drop the cursor (Fresh search resets it; Disconnect clears it)."""
        row = self.get()
        if row is not None:
            row.fresh_at = None
            row.queries = []


class ApplyRunsRepo:
    """Durable Applier attempts (`docs/internal/archived/applier-as-built.md` section 9.1). Runs are
    append-only evidence: `update` mutates only the LIVE run's progress
    columns; a retry creates a new row via `create(retry_of_run_id=...)`."""

    def __init__(self, session: Session) -> None:
        self._s = session

    def create(self, application_id: str, **fields: Any) -> ApplyRun:
        run = ApplyRun(application_id=application_id, **fields)
        self._s.add(run)
        self._s.flush()
        return run

    def get(self, run_id: str) -> ApplyRun | None:
        return self._s.get(ApplyRun, run_id)

    def get_by_operation(self, operation_id: str) -> ApplyRun | None:
        stmt = select(ApplyRun).where(ApplyRun.operation_id == operation_id)
        return self._s.scalars(stmt).first()

    def update(self, run_id: str, **fields: Any) -> ApplyRun:
        run = self._s.get(ApplyRun, run_id)
        if run is None:
            raise ValueError(f"apply run {run_id!r} not found")
        for key, value in fields.items():
            setattr(run, key, value)
        self._s.flush()
        return run

    def list_for_application(self, application_id: str) -> list[ApplyRun]:
        stmt = (
            select(ApplyRun)
            .where(ApplyRun.application_id == application_id)
            .order_by(ApplyRun.started_at.desc(), ApplyRun.id.desc())
        )
        return list(self._s.scalars(stmt))

    def delete_for_application(self, application_id: str) -> int:
        """Purge an application's runs when the CARD itself is deleted
        (unsave/return-to-board — the whole record goes; `foreign_keys=ON`
        forbids orphans). Never used to mutate runs on a live card — those
        stay append-only evidence."""
        result = self._s.execute(
            delete(ApplyRun).where(ApplyRun.application_id == application_id)
        )
        return cast("CursorResult[Any]", result).rowcount

    def latest_for_application(self, application_id: str) -> ApplyRun | None:
        runs = self.list_for_application(application_id)
        return runs[0] if runs else None

    def latest_for_applications(self, application_ids: list[str]) -> dict[str, ApplyRun]:
        """`latest_for_application` over many cards in one IN query (F-H2) —
        same (started_at desc, id desc) ordering, first row per card wins.
        Cards with no runs are absent from the map."""
        if not application_ids:
            return {}
        stmt = (
            select(ApplyRun)
            .where(ApplyRun.application_id.in_(application_ids))
            .order_by(ApplyRun.started_at.desc(), ApplyRun.id.desc())
        )
        latest: dict[str, ApplyRun] = {}
        for run in self._s.scalars(stmt):
            latest.setdefault(run.application_id, run)
        return latest

    def list_active(self) -> list[ApplyRun]:
        """Runs a boot-recovery pass must mark interrupted (section 9.3)."""
        stmt = select(ApplyRun).where(
            ApplyRun.status.in_(APPLY_RUN_ACTIVE_STATUSES)
        )
        return list(self._s.scalars(stmt))


class Repos:
    """One session, every aggregate repo. Feature commits add their repos here."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.operations = OperationsRepo(session)
        self.preferences = PreferencesRepo(session)
        self.profile = ProfileRepo(session)
        self.engine_settings = EngineSettingsRepo(session)
        self.schedules = SchedulesRepo(session)
        self.jobs = JobsRepo(session)
        self.tombstones = TombstonesRepo(session)
        self.applications = ApplicationsRepo(session)
        self.artifacts = ArtifactsRepo(session)
        self.documents = DocumentsRepo(session)
        self.application_events = ApplicationEventsRepo(session)
        self.contacts = ContactsRepo(session)
        self.company_resolutions = CompanyResolutionsRepo(session)
        self.referral_candidates = ReferralCandidatesRepo(session)
        self.outreach_logs = OutreachLogsRepo(session)
        self.linkedin_session = LinkedInSessionRepo(session)
        self.linkedin_search_cursor = LinkedInSearchCursorRepo(session)
        self.apply_runs = ApplyRunsRepo(session)

    def all_time_cost_totals(self) -> CostTotals:
        """Live-ledger sum + the pruned aggregate = every op ever recorded. The
        source of truth for the Analytics all-time cost tiles (FR-SET-07)."""
        return add_cost_totals(
            self.operations.live_cost_totals(), self.preferences.get_cost_totals()
        )

    def commit(self) -> None:
        self.session.commit()

    def flush(self) -> None:
        self.session.flush()
