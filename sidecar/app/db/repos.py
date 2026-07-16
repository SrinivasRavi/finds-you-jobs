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
from .models import Operation, UserPreferences

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


class Repos:
    """One session, every aggregate repo. Feature commits add their repos here."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.operations = OperationsRepo(session)
        self.preferences = PreferencesRepo(session)

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
