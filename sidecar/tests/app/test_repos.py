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


def test_profile_upsert_increments_version(migrated_db: Database) -> None:
    db = migrated_db
    with db.repos() as repos:
        p = repos.profile.upsert("# v1")
        assert p.version == 1
    with db.repos() as repos:
        p = repos.profile.upsert("# v2")
        assert p.version == 2
    with db.repos() as repos:
        current = repos.profile.get_current()
        assert current is not None
        assert current.resume_markdown == "# v2"


def test_preferences_single_row(migrated_db: Database) -> None:
    db = migrated_db
    with db.repos() as repos:
        repos.preferences.update(freshness_days=14, voyager_risk_marker_on=True)
    with db.repos() as repos:
        prefs = repos.preferences.get()
        assert prefs is not None
        assert prefs.freshness_days == 14
        assert prefs.voyager_risk_marker_on is True


def test_engine_settings_never_returns_plaintext_key(migrated_db: Database) -> None:
    db = migrated_db
    with db.repos() as repos:
        row = repos.engine_settings.create(
            "openrouter", key_encrypted=b"ciphertext", default_model="x"
        )
        assert row.key_encrypted == b"ciphertext"
    with db.repos() as repos:
        rows = repos.engine_settings.list()
        assert len(rows) == 1 and rows[0].engine == "openrouter"


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
