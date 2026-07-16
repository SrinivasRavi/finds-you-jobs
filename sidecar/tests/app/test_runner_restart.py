"""Covers: A3 Operation Runner — restart durability (NFR-LONG-02).

Reproduces the post-crash DB state (a `running` row the dead process never
finished + a still-`queued` row), boots a fresh runner, and asserts boot
recovery: orphaned `running` → `failed` with the honest restart note; `queued`
re-runs to completion.
"""

from __future__ import annotations

from sidecar.app.db import Database
from sidecar.app.registry import OperationContext, OperationOutcome, OperationRegistry
from sidecar.app.runner import RESTART_NOTE, OperationRunner

from .conftest import wait_for_state


def _success(ctx: OperationContext) -> OperationOutcome:
    return OperationOutcome(result_ref={"ok": True}, usage={"internal_calls": 1})


def test_boot_recovery_fails_orphans_and_reruns_queued(migrated_db: Database) -> None:
    db = migrated_db

    # Seed the exact DB state a crash leaves behind (no live workers).
    with db.repos() as repos:
        orphan = repos.operations.create("score", {"job_id": "A"})
        repos.operations.mark_running(orphan.id)  # was running when we "crashed"
        queued = repos.operations.create("score", {"job_id": "B"})
        orphan_id, queued_id = orphan.id, queued.id

    events: list[dict] = []
    runner = OperationRunner(
        db, registry=OperationRegistry({"score": _success}), publish=events.append
    )
    runner.start()  # boot recovery + pump
    try:
        wait_for_state(db, queued_id, "succeeded")
    finally:
        runner.shutdown(drain_timeout=3)

    with db.repos() as repos:
        orphan = repos.operations.get(orphan_id)
        queued = repos.operations.get(queued_id)
        assert orphan is not None and queued is not None
        assert orphan.state == "failed"
        assert orphan.error == RESTART_NOTE
        assert orphan.finished_at is not None
        assert queued.state == "succeeded"

    # The orphan's failure was surfaced as an event too (never silent).
    orphan_failed = [
        e
        for e in events
        if e["type"] == "operation"
        and e["payload"]["id"] == orphan_id
        and e["payload"]["state"] == "failed"
    ]
    assert orphan_failed and orphan_failed[0]["payload"]["error"] == RESTART_NOTE


def test_recover_with_nothing_pending_is_noop(migrated_db: Database) -> None:
    db = migrated_db
    runner = OperationRunner(db, registry=OperationRegistry({"score": _success}))
    runner.start()
    runner.shutdown(drain_timeout=1)
    with db.repos() as repos:
        assert repos.operations.list_recent() == []
