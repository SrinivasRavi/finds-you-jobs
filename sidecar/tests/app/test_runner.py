"""Covers: A3 Operation Runner (architecture §5.3).

Happy path (state transitions + usage ledger + events), verbatim error
propagation (NFR-SIDE-04), and the per-kind concurrency policy — the pure
`can_start` decision plus live single-flight / LLM-≤2 enforcement.
"""

from __future__ import annotations

import threading

import pytest

from sidecar.app.db import Database
from sidecar.app.registry import (
    EngineNotConfiguredError,
    OperationContext,
    OperationOutcome,
    OperationRegistry,
)
from sidecar.app.runner import DEFAULT_POLICY, OperationRunner, can_start

from .conftest import wait_for_state


def _success(ctx: OperationContext) -> OperationOutcome:
    return OperationOutcome(
        result_ref={"echo": ctx.input_snapshot},
        usage={"internal_calls": 1, "usd": 0.02, "model": "fake-model"},
        engine="fake-engine",
        model="fake-model",
    )


def _boom(ctx: OperationContext) -> OperationOutcome:
    raise ValueError("exact failure text 42")


class _Blocking:
    """Entrypoint that blocks until released — lets a test observe in-flight ops."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Semaphore(0)
        self._lock = threading.Lock()
        self._current = 0
        self.max_concurrent = 0

    def __call__(self, ctx: OperationContext) -> OperationOutcome:
        with self._lock:
            self._current += 1
            self.max_concurrent = max(self.max_concurrent, self._current)
        self.started.release()
        self.release.wait(timeout=5)
        with self._lock:
            self._current -= 1
        return OperationOutcome()


def _running_count(db: Database) -> int:
    with db.repos() as repos:
        return len(repos.operations.list_by_state("running"))


# -- pure policy -----------------------------------------------------------


def test_can_start_llm_group_caps_at_two() -> None:
    assert can_start("score", ["score"], DEFAULT_POLICY) is True
    assert can_start("tailor", ["score", "cover"], DEFAULT_POLICY) is False  # 2 LLM running


def test_can_start_scan_is_single_flight() -> None:
    assert can_start("scan", [], DEFAULT_POLICY) is True
    assert can_start("scan", ["scan"], DEFAULT_POLICY) is False


def test_apply_runs_beside_llm_ops_but_single_flight() -> None:
    # The agentic apply op WAITS for a still-generating tailored resume
    # (applier.md §8.1), so `tailor` MUST be startable while apply runs —
    # exclusivity here dead-locked the packet wait (2026-07-17 dogfood).
    assert can_start("apply", ["score"], DEFAULT_POLICY) is True
    assert can_start("tailor", ["apply"], DEFAULT_POLICY) is True
    assert can_start("apply", ["apply"], DEFAULT_POLICY) is False  # one run at a time
    assert can_start("apply", [], DEFAULT_POLICY) is True


def test_linkedin_login_stays_exclusive() -> None:
    assert can_start("linkedin_login", ["score"], DEFAULT_POLICY) is False
    assert can_start("score", ["linkedin_login"], DEFAULT_POLICY) is False
    assert can_start("linkedin_login", [], DEFAULT_POLICY) is True


def test_dispatch_priority_orders_interactive_before_bulk() -> None:
    from sidecar.app.runner.policy import dispatch_priority

    # An apply the user is watching outranks the score fan-out, and the
    # tailor it waits on outranks scores too (the starvation fix).
    assert dispatch_priority("apply") < dispatch_priority("tailor")
    assert dispatch_priority("tailor") < dispatch_priority("score")
    assert dispatch_priority("score") < dispatch_priority("scan")
    kinds = ["score", "score", "apply", "tailor", "scan"]
    kinds.sort(key=dispatch_priority)
    assert kinds[:2] == ["apply", "tailor"]


# -- live runner -----------------------------------------------------------


def test_happy_path_persists_state_usage_and_events(migrated_db: Database) -> None:
    db = migrated_db
    events: list[dict] = []
    runner = OperationRunner(
        db, registry=OperationRegistry({"score": _success}), publish=events.append
    )
    runner.start()
    try:
        op_id = runner.submit("score", {"job_id": "J9"})
        wait_for_state(db, op_id, "succeeded")
    finally:
        runner.shutdown(drain_timeout=2)

    with db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.state == "succeeded"
        assert op.usage == {"internal_calls": 1, "usd": 0.02, "model": "fake-model"}
        assert op.result_ref == {"echo": {"job_id": "J9"}}
        assert op.engine == "fake-engine"

    states = [e["payload"]["state"] for e in events if e["type"] == "operation"]
    assert states == ["queued", "running", "succeeded"]


def test_failure_records_error_verbatim(migrated_db: Database) -> None:
    db = migrated_db
    events: list[dict] = []
    runner = OperationRunner(
        db, registry=OperationRegistry({"score": _boom}), publish=events.append
    )
    runner.start()
    try:
        op_id = runner.submit("score", {})
        wait_for_state(db, op_id, "failed")
    finally:
        runner.shutdown(drain_timeout=2)

    with db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.state == "failed"
        assert op.error == "ValueError: exact failure text 42"
    failed = [e for e in events if e["payload"]["state"] == "failed"]
    assert failed and failed[0]["payload"]["error"] == "ValueError: exact failure text 42"


def test_unconfigured_engine_fails_cleanly(migrated_db: Database) -> None:
    db = migrated_db

    # Mirrors the real LLM-kind wrapper pattern: an entrypoint that requires a
    # routed engine raises the typed not-configured error when none is set.
    # (The default registry is empty at the core-storage commit; the real
    # score wrapper registers with its module commit.)
    def _needs_engine(ctx: OperationContext) -> OperationOutcome:
        if ctx.engine is None:
            raise EngineNotConfiguredError(ctx.kind)
        return OperationOutcome()

    runner = OperationRunner(
        db, registry=OperationRegistry({"score": _needs_engine}), engines=None
    )
    runner.start()
    try:
        op_id = runner.submit("score", {"master_md": "m", "job": "j"})
        wait_for_state(db, op_id, "failed")
    finally:
        runner.shutdown(drain_timeout=2)
    with db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.state == "failed"
        assert op.error is not None
        assert EngineNotConfiguredError.__name__ in op.error
        assert "no engine configured" in op.error


def test_scan_is_single_flight_live(migrated_db: Database) -> None:
    db = migrated_db
    blocking = _Blocking()
    runner = OperationRunner(db, registry=OperationRegistry({"scan": blocking}))
    runner.start()
    try:
        for _ in range(3):
            runner.submit("scan", {})
        assert blocking.started.acquire(timeout=3)  # first started
        # No second scan may start while one is running.
        assert blocking.started.acquire(timeout=0.4) is False
        assert _running_count(db) == 1
    finally:
        blocking.release.set()
        runner.shutdown(drain_timeout=3)
    assert blocking.max_concurrent == 1


def test_llm_group_caps_at_two_live(migrated_db: Database) -> None:
    db = migrated_db
    blocking = _Blocking()
    runner = OperationRunner(
        db,
        registry=OperationRegistry(
            {"score": blocking, "tailor": blocking, "cover": blocking}
        ),
    )
    runner.start()
    try:
        runner.submit("score", {})
        runner.submit("tailor", {})
        runner.submit("cover", {})
        assert blocking.started.acquire(timeout=3)
        assert blocking.started.acquire(timeout=3)  # two started
        assert blocking.started.acquire(timeout=0.4) is False  # third held back
        assert _running_count(db) == 2
    finally:
        blocking.release.set()
        runner.shutdown(drain_timeout=3)
    assert blocking.max_concurrent == 2


def test_on_success_hook_fires_on_success_only_and_is_contained(
    migrated_db: Database,
) -> None:
    """The chain hook runs after a success (never a failure), and a raising
    hook is contained — the operation stays `succeeded`."""
    db = migrated_db
    calls: list[tuple[str, str]] = []

    def hook(operation_id: str, kind: str) -> None:
        calls.append((operation_id, kind))
        raise RuntimeError("chain boom — must not fail the op")

    runner = OperationRunner(
        db,
        registry=OperationRegistry({"score": _success, "cover": _boom}),
        publish=lambda _e: None,
        on_success=hook,
    )
    runner.start()
    try:
        ok_id = runner.submit("score", {})
        assert wait_for_state(db, ok_id, "succeeded") == "succeeded"
        bad_id = runner.submit("cover", {})
        assert wait_for_state(db, bad_id, "failed") == "failed"
    finally:
        runner.shutdown(drain_timeout=2)

    assert calls == [(ok_id, "score")]


def test_span_recording_failure_never_wedges_the_operation(
    migrated_db: Database, monkeypatch
) -> None:
    """Cross-track review fix: a raising span recorder must not leave the op
    stuck in `running` — state transition, SSE event, and the on_success chain
    all still run (the span is additive, never load-bearing)."""
    import sidecar.app.runner.runner as runner_mod

    def _boom_span(*_a, **_k):
        raise RuntimeError("span recorder boom")

    monkeypatch.setattr(runner_mod, "record_span_success", _boom_span)
    monkeypatch.setattr(runner_mod, "record_span_failure", _boom_span)

    db = migrated_db
    chained: list[str] = []
    events: list[dict] = []
    runner = OperationRunner(
        db,
        registry=OperationRegistry({"score": _success, "cover": _boom}),
        publish=events.append,
        on_success=lambda _op_id, kind: chained.append(kind),
    )
    runner.start()
    try:
        ok_id = runner.submit("score", {})
        assert wait_for_state(db, ok_id, "succeeded") == "succeeded"
        bad_id = runner.submit("cover", {})
        assert wait_for_state(db, bad_id, "failed") == "failed"
    finally:
        runner.shutdown(drain_timeout=2)

    assert chained == ["score"]  # chain still fired despite the raising recorder
    states = [e["payload"]["state"] for e in events if e["type"] == "operation"]
    assert states.count("succeeded") == 1 and states.count("failed") == 1


def test_ledger_retention_trims_to_cap(migrated_db: Database) -> None:
    """US-LOG-01 #2: trim_to keeps the N most-recent terminal ops; in-flight
    (queued/running) rows are never pruned."""
    db = migrated_db
    with db.repos() as repos:
        # 6 terminal (succeeded) + 1 still-queued.
        for i in range(6):
            op = repos.operations.create("score", {"n": i})
            repos.operations.mark_succeeded(op.id, usage={"usd": 0.0})
        pending = repos.operations.create("scan", {})
        deleted = repos.operations.trim_to(3)
    assert deleted == 3  # 6 terminal - keep 3
    with db.repos() as repos:
        remaining = repos.operations.list_recent(50)
        terminal = [o for o in remaining if o.state == "succeeded"]
        assert len(terminal) == 3  # newest 3 kept
        assert any(o.id == pending.id for o in remaining)  # queued survives


def test_prune_ledger_preserves_all_time_spend(migrated_db: Database) -> None:
    """FR-SET-07 / US-LOG-01 #2: retention prunes old terminal ops, but their
    usd/tokens are folded into the persistent lifetime aggregate first — so the
    all-time totals equal ledger + aggregate, not just the retained window."""
    db = migrated_db
    with db.repos() as repos:
        for i in range(6):
            op = repos.operations.create("score", {"n": i})
            repos.operations.mark_succeeded(
                op.id, usage={"usd": 0.10, "tokens_in": 100, "tokens_out": 50}
            )
        full = repos.all_time_cost_totals()  # before pruning: all six are live
    assert full["operations"] == 6
    assert full["usd"] == pytest.approx(0.60)
    assert full["by_kind"]["score"] == pytest.approx(0.60)

    with db.repos() as repos:
        pruned = repos.prune_ledger(3)  # keep newest 3, fold the other 3
    assert pruned == 3

    with db.repos() as repos:
        # The three pruned ops now live only in the aggregate…
        agg = repos.preferences.get_cost_totals()
        assert agg["operations"] == 3
        assert agg["usd"] == pytest.approx(0.30)
        # …and are still counted in the all-time totals (3 live + 3 pruned).
        after = repos.all_time_cost_totals()
        assert after["operations"] == 6
        assert after["usd"] == pytest.approx(0.60)
        assert after["tokens_in"] == 600
        assert after["tokens_out"] == 300
        assert after["by_kind"]["score"] == pytest.approx(0.60)
        # The live ledger really did shrink — the aggregate is what saved the total.
        assert len(repos.operations.list_recent(50)) == 3


def test_prune_ledger_conserves_totals_across_repeated_prunes(migrated_db: Database) -> None:
    """The all-time totals are conserved no matter how many prune cycles run —
    each prune folds into (never overwrites) the aggregate, so earlier pruned
    spend is never lost even when a later prune folds a still-live op."""
    db = migrated_db
    with db.repos() as repos:
        for _ in range(4):
            op = repos.operations.create("tailor", {})
            repos.operations.mark_succeeded(op.id, usage={"usd": 0.25})
        repos.prune_ledger(1)  # first cycle: fold 3 tailor
    with db.repos() as repos:
        for _ in range(3):
            op = repos.operations.create("cover", {})
            repos.operations.mark_succeeded(op.id, usage={"usd": 0.50})
        repos.prune_ledger(1)  # second cycle: folds across both kinds
        total = repos.all_time_cost_totals()
    # 4×0.25 + 3×0.50 = 2.50, conserved across both prune cycles.
    assert total["usd"] == pytest.approx(2.50)
    assert total["operations"] == 7
    assert total["by_kind"]["tailor"] == pytest.approx(1.0)
    assert total["by_kind"]["cover"] == pytest.approx(1.5)


def test_usage_mapping_is_faithful_to_the_engine(migrated_db: Database) -> None:
    """Cost-correctness (maintainer Q): the model + tokens + usd the engine
    reports flow into the operations row unchanged — so when a real API engine
    reports real numbers, the ledger is right by construction (not by hope)."""
    db = migrated_db
    reported = {
        "internal_calls": 2,
        "tokens_in": 1234,
        "tokens_out": 567,
        "usd": 0.0891,
        "model": "anthropic/claude-opus-4",
    }

    def _entry(ctx: OperationContext) -> OperationOutcome:
        return OperationOutcome(usage=dict(reported), engine="openrouter", model=reported["model"])

    runner = OperationRunner(
        db, registry=OperationRegistry({"score": _entry}), publish=lambda _e: None
    )
    runner.start()
    try:
        op_id = runner.submit("score", {})
        wait_for_state(db, op_id, "succeeded")
    finally:
        runner.shutdown(drain_timeout=2)
    with db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.usage == reported  # verbatim, no rounding/loss
        assert op.model == "anthropic/claude-opus-4"
        assert op.engine == "openrouter"
