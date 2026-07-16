"""Covers: core storage — operations repo round-trips (database-design §9)."""

from __future__ import annotations

from sidecar.app.db import Database


def test_operation_lifecycle_round_trip(migrated_db: Database) -> None:
    db = migrated_db
    with db.repos() as repos:
        op = repos.operations.create("scan", {"source": "greenhouse"})
        op_id = op.id
        assert op.state == "queued"

    with db.repos() as repos:
        repos.operations.mark_running(op_id)
    with db.repos() as repos:
        repos.operations.mark_succeeded(
            op_id,
            result_ref={"jobs": 3},
            usage={"usd": 0.01, "tokens_in": 100, "tokens_out": 20},
            engine="fake",
            model="fake-model",
        )

    with db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.state == "succeeded"
        assert op.result_ref == {"jobs": 3}
        assert op.usage == {"usd": 0.01, "tokens_in": 100, "tokens_out": 20}
        assert op.engine == "fake"
        assert op.model == "fake-model"
        assert op.started_at is not None and op.finished_at is not None
        assert op.created_at.tzinfo is not None  # UTCDateTime returns tz-aware


def test_operation_failure_keeps_error_verbatim(migrated_db: Database) -> None:
    db = migrated_db
    with db.repos() as repos:
        op = repos.operations.create("score", {})
        op_id = op.id
    with db.repos() as repos:
        repos.operations.mark_failed(op_id, error="ValueError: exact text 42")
    with db.repos() as repos:
        op = repos.operations.get(op_id)
        assert op is not None
        assert op.state == "failed"
        assert op.error == "ValueError: exact text 42"
