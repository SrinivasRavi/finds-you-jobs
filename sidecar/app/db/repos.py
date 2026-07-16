"""Typed repositories per aggregate (architecture §5, database-design §9).

Routes and the runner go through `Repos`, never a raw session. Each sub-repo is
a thin, typed surface over one aggregate; the `Repos` container binds them to a
single session (one short transaction per unit of work — AM4).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .base import now_utc
from .models import (
    Application,
    ApplicationEvent,
    Artifact,
    EngineSettings,
    Job,
    JobScore,
    MasterProfile,
    Operation,
    Schedule,
    Tombstone,
    UserPreferences,
)

# ---------------------------------------------------------------------------
# Lifetime cost aggregate (US-LOG-01 #2 / FR-SET-07)
# ---------------------------------------------------------------------------
# Ledger retention prunes old terminal ops, so summing the live ledger alone
# would silently forget pruned spend. Before pruning we fold the pruned ops'
# usd/tokens into a persistent aggregate (UserPreferences.ui_state["cost_totals"]);
# the all-time totals surface = live-ledger sum + this aggregate.

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


def _accumulate_op(agg: CostTotals, op: Operation) -> None:
    """Fold one operation's usage into a running cost aggregate."""
    usage = op.usage or {}
    usd = float(usage.get("usd") or 0.0)
    agg["usd"] += usd
    agg["tokens_in"] += int(usage.get("tokens_in") or 0)
    agg["tokens_out"] += int(usage.get("tokens_out") or 0)
    agg["operations"] += 1
    if op.state == "failed":
        agg["failed"] += 1
    by_kind = agg["by_kind"]
    by_kind[op.kind] = float(by_kind.get(op.kind, 0.0)) + usd


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
        op = Operation(kind=kind, state="queued", input_snapshot=input_snapshot)
        self._s.add(op)
        self._s.flush()
        return op

    def get(self, operation_id: str) -> Operation | None:
        return self._s.get(Operation, operation_id)

    def list_by_state(self, state: str) -> list[Operation]:
        stmt = (
            select(Operation)
            .where(Operation.state == state)
            .order_by(Operation.created_at, Operation.id)
        )
        return list(self._s.scalars(stmt))

    def list_recent(self, limit: int = 100) -> list[Operation]:
        stmt = select(Operation).order_by(Operation.created_at.desc()).limit(limit)
        return list(self._s.scalars(stmt))

    def trim_to(self, keep: int) -> int:
        """Delete all but the `keep` most-recent terminal operations — the P1
        ledger retention (US-LOG-01 #2: ~5 pages). Only terminal rows are
        pruned so an in-flight `queued`/`running` op is never dropped mid-flight.
        Returns the number deleted."""
        terminal = ("succeeded", "failed", "cancelled")
        keep_ids = select(Operation.id).where(Operation.state.in_(terminal)).order_by(
            Operation.created_at.desc()
        ).limit(keep)
        stmt = delete(Operation).where(
            Operation.state.in_(terminal), Operation.id.not_in(keep_ids)
        )
        result = cast("CursorResult[Any]", self._s.execute(stmt))
        return result.rowcount or 0

    def sum_terminal_beyond(self, keep: int) -> CostTotals:
        """The cost aggregate of the terminal ops `trim_to(keep)` would prune —
        i.e. all-but-the-newest-`keep` terminal rows. Folded into the persistent
        lifetime aggregate *before* pruning so all-time spend survives retention."""
        terminal = ("succeeded", "failed", "cancelled")
        stmt = (
            select(Operation)
            .where(Operation.state.in_(terminal))
            .order_by(Operation.created_at.desc())
            .offset(keep)
        )
        agg = _empty_cost_totals()
        for op in self._s.scalars(stmt):
            _accumulate_op(agg, op)
        return agg

    def live_cost_totals(self) -> CostTotals:
        """The cost aggregate over every operation still in the table (all states;
        in-flight rows carry no usage and contribute only to the op count). Added
        to the pruned aggregate to yield the all-time totals."""
        agg = _empty_cost_totals()
        for op in self._s.scalars(select(Operation)):
            _accumulate_op(agg, op)
        return agg

    def list_by_kind_states(self, kind: str, states: set[str]) -> list[Operation]:
        stmt = select(Operation).where(
            Operation.kind == kind, Operation.state.in_(states)
        )
        return list(self._s.scalars(stmt))

    def score_states_by_job(self) -> dict[str, set[str]]:
        """job_id → the set of its `score` operation states — the board's
        Score-failed derivation (FR-JB-07 / NFR-OFFLINE-02). A job with a failed
        score op and no cached score resolves to `Score failed`, never a
        perpetual Pending. (Bounded by ledger retention; a pruned failure simply
        re-reads as Pending, and the Remove→Add-back retry path re-scores.)"""
        result: dict[str, set[str]] = {}
        stmt = select(Operation).where(Operation.kind == "score")
        for op in self._s.scalars(stmt):
            job_id = (op.input_snapshot or {}).get("job_id")
            if job_id:
                result.setdefault(job_id, set()).add(op.state)
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

    def any_in_flight(self, kind: str) -> bool:
        stmt = select(Operation.id).where(
            Operation.kind == kind, Operation.state.in_(("queued", "running"))
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

        Trashing records `trashed_at` in `source_meta` so the 7-day TTL tick
        (FR-SYS-03/FR-SYS-04) can age it out; restoring clears that bookkeeping
        and returns the row to the active feed — its score/history are untouched.
        `source_meta` is otherwise unused for scanned/pasted jobs, so overloading
        it here needs no schema change."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        meta = dict(job.source_meta or {})
        if trashed:
            job.feed_state = "removed"
            meta["trashed_at"] = (now or now_utc()).isoformat()
        else:
            job.feed_state = "active"
            meta.pop("trashed_at", None)
        job.source_meta = meta or None
        return job

    def set_expired(self, job_id: str, *, now: datetime | None = None) -> Job:
        """Age a feed job into `Expired` (FR-SYS-03) — greyed, labelled "Older
        listing", still on the board. Stamps `expired_at` in `source_meta` (the
        same JSON-overload pattern as `trashed_at`) so the 30-day hard-delete
        clock can start. No score/history change."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        meta = dict(job.source_meta or {})
        meta["expired_at"] = (now or now_utc()).isoformat()
        job.feed_state = "expired"
        job.source_meta = meta
        return job

    def unexpire(self, job_id: str, *, now: datetime | None = None) -> Job:
        """Explicit un-expire (FR-SYS-03): restore an Expired job to the active
        feed and **reset the 14-day timer** by stamping `feed_since` (the aging
        clock reads `feed_since` when present, else `ingested_at`, so the sort
        order — recency — is preserved)."""
        job = self._s.get(Job, job_id)
        if job is None:
            raise KeyError(f"job {job_id!r} not found")
        meta = dict(job.source_meta or {})
        meta.pop("expired_at", None)
        meta["feed_since"] = (now or now_utc()).isoformat()
        job.feed_state = "active"
        job.source_meta = meta or None
        return job

    def delete(self, job_id: str) -> bool:
        """Hard-delete a job row + its cached scores (foreign_keys=ON forbids
        orphaned `JobScore` rows). Used by the tombstone paths (Empty Trash /
        Delete forever / TTL eviction) — the caller writes the `Tombstone`."""
        job = self._s.get(Job, job_id)
        if job is None:
            return False
        self._s.execute(delete(JobScore).where(JobScore.job_id == job_id))
        self._s.delete(job)
        return True


class JobScoresRepo:
    def __init__(self, session: Session) -> None:
        self._s = session

    def get_cached(
        self, job_id: str, profile_version: int, scorer_impl: str = "scorer-llm"
    ) -> JobScore | None:
        stmt = select(JobScore).where(
            JobScore.job_id == job_id,
            JobScore.profile_version == profile_version,
            JobScore.scorer_impl == scorer_impl,
        )
        return self._s.scalars(stmt).first()

    def latest_for_jobs(
        self, job_ids: list[str], profile_version: int, scorer_impl: str = "scorer-llm"
    ) -> dict[str, JobScore]:
        """The cached score per job for one `(profile_version, scorer_impl)` —
        the board join (FR-JB-01 sort)."""
        if not job_ids:
            return {}
        stmt = select(JobScore).where(
            JobScore.job_id.in_(job_ids),
            JobScore.profile_version == profile_version,
            JobScore.scorer_impl == scorer_impl,
        )
        return {row.job_id: row for row in self._s.scalars(stmt)}

    def scored_job_ids(
        self, profile_version: int, scorer_impl: str = "scorer-llm"
    ) -> set[str]:
        stmt = select(JobScore.job_id).where(
            JobScore.profile_version == profile_version,
            JobScore.scorer_impl == scorer_impl,
        )
        return set(self._s.scalars(stmt))

    def create(self, **fields: Any) -> JobScore:
        score = JobScore(**fields)
        self._s.add(score)
        self._s.flush()
        return score

    def upsert(
        self,
        *,
        job_id: str,
        profile_version: int,
        scorer_impl: str = "scorer-llm",
        **fields: Any,
    ) -> JobScore:
        """Cache write: refresh the row for a `(job, version, impl)` triple, or
        create it. A recompute of the same cache key never duplicates."""
        existing = self.get_cached(job_id, profile_version, scorer_impl)
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            self._s.flush()
            return existing
        return self.create(
            job_id=job_id,
            profile_version=profile_version,
            scorer_impl=scorer_impl,
            **fields,
        )


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
        self.job_scores = JobScoresRepo(session)
        self.tombstones = TombstonesRepo(session)
        self.applications = ApplicationsRepo(session)
        self.artifacts = ArtifactsRepo(session)
        self.application_events = ApplicationEventsRepo(session)

    def prune_ledger(self, keep: int) -> int:
        """Ledger retention that preserves all-time spend: fold the usd/tokens of
        the terminal ops about to be pruned into the persistent lifetime aggregate
        (`ui_state["cost_totals"]`), then delete them. One transaction, so a crash
        mid-way never double-counts or loses a delta. Returns the number pruned."""
        pruned = self.operations.sum_terminal_beyond(keep)
        self.preferences.add_cost_totals(pruned)
        return self.operations.trim_to(keep)

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
