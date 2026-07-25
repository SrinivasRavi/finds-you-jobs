"""Covers: the Applier app integration (docs/internal/applier.md §8/§9).

  US-level behavior — Apply off the Tracker card creates a durable ApplyRun
  immediately (no pre-confirm modal), the op drives the jobapplier agent
  against a LOCAL fixture form, and the run lands ready_for_human with
  redacted field outcomes + on-disk screenshot evidence. Attestation moves
  the card to Applied; the generic operations endpoint refuses `apply`.

ZERO model calls, zero external traffic: the engine is the scripted
FakeApplyEngine injected through the op's dev knobs (FYJ_APPLY_DEV=1), the
job URL is a file:// fixture, and the browser is headless Chromium.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app

TOKEN = "test-token-apply"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

FIXTURES = Path(__file__).parent.parent / "packages" / "jobapplier" / "fixtures"


def _make_client(tmp_path: Path) -> Generator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


@pytest.fixture
def app_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[FastAPI, TestClient]]:
    monkeypatch.setenv("FYJ_APPLY_DEV", "1")
    # The op resolves the run dir from the app data dir.
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path / "data"))
    yield from _make_client(tmp_path)


def _action(tool: str, **args: object) -> str:
    return json.dumps({"tool": tool, **args})


def _seed_card(client: TestClient) -> tuple[str, str]:
    """Master profile + a fixture-form job + a Saved application."""
    resp = client.post(
        "/api/profile",
        headers=AUTH,
        json={"resume_markdown": "# Ada Lovelace\n\nBackend engineer."},
    )
    assert resp.status_code in (200, 201)
    job = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": (FIXTURES / "form.html").as_uri(),
            "title": "Staff Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Own the monolith.",
            "source_adapter": "paste-url",
        },
    ).json()
    application = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()
    return job["id"], application["id"]


# 120 s: the suite now carries several real-Chromium tests; a cold browser
# spawn under a loaded suite can push a run past 60 s (2026-07-17 flake).
def _wait_terminal(client: TestClient, run_id: str, timeout: float = 300.0) -> dict:
    """Poll the run to a terminal state. 300s ceiling (was 120s): a cold
    Chromium launch on a loaded dev machine was observed at 122s (2026-07-18),
    turning a fixed 120s wait into a pure load flake — a different test failed
    on each run, all 5 pass in ~3s on an idle machine. The ceiling only binds
    on genuine failure; idle runtime is unchanged."""
    deadline = time.monotonic() + timeout
    run: dict = {}
    while time.monotonic() < deadline:
        run = client.get(f"/api/apply-runs/{run_id}", headers=AUTH).json()
        if run["status"] not in ("queued", "waiting_for_packet", "running"):
            return run
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} never landed: {run}")


# The scripted flow mirrors the package happy path, but the ids are computed
# server-side per observation — the script uses element-id-free heuristics via
# a tiny templating trick: the op's dev script accepts literal replies only,
# so we script against the STABLE fixture (ids assigned in document order).
# Document order in form.html: e1 form? … — instead of guessing, the script
# fills by trying e-ids in order; the loop tolerates rejected/failed steps and
# the test asserts on the terminal contract, not per-step success.
def _happy_script() -> list[str]:
    return [
        _action("fill", element_id="e2", value="Ada Lovelace"),
        _action("fill", element_id="e3", value="ada@example.com"),
        _action("finish", reason="grounded fields filled; remainder left for review"),
    ]


def test_apply_run_lands_ready_for_human(app_client) -> None:
    _app, client = app_client
    _job_id, app_id = _seed_card(client)

    resp = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                "engine_script": _happy_script(),
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    )
    assert resp.status_code == 202, resp.text
    run = resp.json()
    assert run["status"] == "queued"  # honest: not started yet (2026-07-17)

    final = _wait_terminal(client, run["id"])
    assert final["status"] == "ready_for_human", final
    assert final["final_url"].endswith("form.html")
    assert final["screenshot_count"] >= 2
    assert final["usage"]["calls"] == 3
    # Redacted evidence only: labels and outcomes, never values.
    assert not re.search(r"ada@example\.com", json.dumps(final["fields"]))

    # Evidence PNGs are served by index, path-free.
    shot = client.get(f"/api/apply-runs/{run['id']}/screenshots/0", headers=AUTH)
    assert shot.status_code == 200
    assert shot.content[:8] == b"\x89PNG\r\n\x1a\n"

    # The card settled the exclusive intent and surfaces the latest run.
    card = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert card["intent"] == "apply"
    assert card["applyRunStatus"] == "ready_for_human"
    assert card["applyRunId"] == run["id"]

    # History endpoint sees exactly one immutable run.
    runs = client.get(f"/api/applications/{app_id}/apply-runs", headers=AUTH).json()
    assert [r["id"] for r in runs] == [run["id"]]


def test_attest_submitted_moves_card_to_applied(app_client) -> None:
    _app, client = app_client
    _job_id, app_id = _seed_card(client)
    run = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                "engine_script": [_action("finish", reason="filled")],
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    ).json()
    final = _wait_terminal(client, run["id"])
    assert final["status"] == "ready_for_human"

    attested = client.post(
        f"/api/apply-runs/{run['id']}/attest",
        headers=AUTH,
        json={"submitted": True},
    ).json()
    assert attested["status"] == "submitted"
    assert attested["submit_evidence"] == "user_attested"
    card = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert card["column"] == "applied"
    assert card["applied_via"] == "applier"


def test_attest_didnt_submit_leaves_card(app_client) -> None:
    _app, client = app_client
    _job_id, app_id = _seed_card(client)
    run = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                "engine_script": [_action("finish", reason="filled")],
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    ).json()
    _wait_terminal(client, run["id"])
    kept = client.post(
        f"/api/apply-runs/{run['id']}/attest",
        headers=AUTH,
        json={"submitted": False},
    ).json()
    assert kept["status"] == "ready_for_human"
    card = client.get(f"/api/applications/{app_id}", headers=AUTH).json()
    assert card["column"] == "saved"


def test_apply_cannot_be_enqueued_generically(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/operations/apply", headers=AUTH, json={})
    assert resp.status_code == 422


def test_closed_posting_blocks_with_zero_model_calls(app_client) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/profile",
        headers=AUTH,
        json={"resume_markdown": "# Ada Lovelace\n\nBackend engineer."},
    )
    assert resp.status_code in (200, 201)
    job = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": (FIXTURES / "closed.html").as_uri(),
            "title": "Staff Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Own the monolith.",
            "source_adapter": "paste-url",
        },
    ).json()
    app_id = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()["id"]
    run = client.post(
        f"/api/applications/{app_id}/apply",
        headers=AUTH,
        json={
            "dev": {
                # Any model call would consume a step; an empty script raises —
                # proving zero tokens are spent on a dead posting.
                "engine_script": [_action("finish", reason="unreachable")],
                "allow_local": True,
                "headed": False,
                "review_wait_s": 0,
            }
        },
    ).json()
    final = _wait_terminal(client, run["id"])
    assert final["status"] == "blocked"
    assert final["blockers"][0]["kind"] == "posting_closed"
    assert final["usage"]["calls"] == 0


# ---------------------------------------------------------------------------
# F-M3 — a packet-wait timeout must NEVER attach a blank resume: fall back to
# the real master resume, or fail typed when there is none.
# ---------------------------------------------------------------------------


def _pending_packet_card(db) -> tuple[str, str]:
    """A card whose head tailored_resume is still generating (its op queued)."""
    with db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="https://example.com/fm3",
            title="Staff Engineer",
            company="Acme",
            source_adapter="paste-url",
        )
        card = repos.applications.create(job_id=job.id, column="saved")
        op = repos.operations.create("tailor", {"application_id": card.id})
        repos.artifacts.create(card.id, kind="tailored_resume", operation_id=op.id)
        run = repos.apply_runs.create(card.id, status="queued")
        return card.id, run.id


def test_packet_timeout_falls_back_to_real_master_resume(
    migrated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from sidecar.app.registry import apply_op
    from sidecar.app.registry.operations import OperationContext
    from sidecar.packages.jobapplier import ApplyControl

    monkeypatch.setattr(apply_op, "_PACKET_WAIT_MAX_S", 0.0)
    app_id, run_id = _pending_packet_card(migrated_db)
    with migrated_db.repos() as repos:
        repos.profile.upsert("# Ada Lovelace — master resume")
    ctx = OperationContext(
        kind="apply", input_snapshot={}, db=migrated_db, operation_id="op-fm3"
    )
    md, label, artifact_id = asyncio.run(
        apply_op._wait_for_packet(ctx, app_id, run_id, ApplyControl())
    )
    assert md == "# Ada Lovelace — master resume"
    assert label == "master resume"
    assert artifact_id is None


def test_packet_timeout_without_master_resume_raises_typed(
    migrated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from sidecar.app.registry import apply_op
    from sidecar.app.registry.operations import OperationContext
    from sidecar.packages.jobapplier import ApplyControl

    monkeypatch.setattr(apply_op, "_PACKET_WAIT_MAX_S", 0.0)
    app_id, run_id = _pending_packet_card(migrated_db)  # no master profile saved
    ctx = OperationContext(
        kind="apply", input_snapshot={}, db=migrated_db, operation_id="op-fm3b"
    )
    with pytest.raises(ValueError, match="never arrived"):
        asyncio.run(apply_op._wait_for_packet(ctx, app_id, run_id, ApplyControl()))


def test_packet_wait_failure_lands_the_run_row_terminal_failed(
    migrated_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F-M3 follow-up (2026-07-25): the packet-wait raise fires AFTER the run
    row was set `waiting_for_packet`. The runner fails the OPERATION, but the
    RUN row must land terminal too — not show "waiting for résumé" forever."""
    import asyncio

    from sidecar.app.registry import apply_op
    from sidecar.app.registry.operations import OperationContext
    from sidecar.packages.jobapplier import ApplyControl

    monkeypatch.setattr(apply_op, "_PACKET_WAIT_MAX_S", 0.0)
    app_id, run_id = _pending_packet_card(migrated_db)  # no master profile saved
    ctx = OperationContext(
        kind="apply",
        input_snapshot={"run_id": run_id, "application_id": app_id},
        db=migrated_db,
        operation_id="op-fm3c",
    )
    with pytest.raises(ValueError, match="never arrived"):
        asyncio.run(apply_op._apply_async(ctx, app_id, ApplyControl()))
    with migrated_db.repos() as repos:
        run = repos.apply_runs.get(run_id)
        assert run is not None
        assert run.status == "failed"  # terminal — never stuck waiting_for_packet
        assert run.phase == "failed"
        assert run.ended_at is not None
        assert "never arrived" in run.summary


def test_post_packet_failure_lands_the_run_row_terminal_failed(
    migrated_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Late-failure finalize (2026-07-25 follow-up): failures AFTER the packet
    wait — PDF render, browser launch, run_apply raising — must land the run
    row terminal too, not strand it `running` until boot sweep."""
    import asyncio

    from sidecar.app.registry import apply_op
    from sidecar.app.registry.operations import OperationContext
    from sidecar.packages.jobapplier import ApplyControl

    monkeypatch.setenv("FYJ_APPLY_DEV", "1")
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path / "data"))
    with migrated_db.repos() as repos:
        repos.profile.upsert("# Ada Lovelace — master resume")
        job = repos.jobs.create(
            canonical_url="https://example.com/late-fail",
            title="Staff Engineer",
            company="Acme",
            source_adapter="paste-url",
        )
        card = repos.applications.create(job_id=job.id, column="saved")
        run = repos.apply_runs.create(card.id, status="queued")
        app_id, run_id = card.id, run.id

    async def exploding_render(*a: object, **kw: object) -> None:
        raise RuntimeError("pdf render exploded")

    monkeypatch.setattr(apply_op, "render_resume_pdf_async", exploding_render)
    ctx = OperationContext(
        kind="apply",
        input_snapshot={
            "run_id": run_id,
            "application_id": app_id,
            "dev_engine_script": [_action("finish", reason="unreachable")],
        },
        db=migrated_db,
        operation_id="op-late-fail",
    )
    with pytest.raises(RuntimeError, match="pdf render exploded"):
        asyncio.run(apply_op._apply_async(ctx, app_id, ApplyControl()))
    with migrated_db.repos() as repos:
        run_row = repos.apply_runs.get(run_id)
        assert run_row is not None
        assert run_row.status == "failed"  # terminal — never stuck `running`
        assert run_row.phase == "failed"
        assert run_row.ended_at is not None
        assert "pdf render exploded" in run_row.summary


def test_submit_failure_finalizes_route_created_run(app_client, monkeypatch) -> None:
    """The start_apply strand (2026-07-25): the route creates the ApplyRun row
    BEFORE runner.submit — a submit that throws must land that row terminal
    (`failed`), never strand it `queued` until boot sweep. The error still
    propagates as the route's 500."""
    app, client = app_client
    _job_id, app_id = _seed_card(client)

    def rejecting_submit(kind: str, snapshot: dict) -> str:
        raise RuntimeError("executor rejected the dispatch")

    monkeypatch.setattr(app.state.runner, "submit", rejecting_submit)
    with pytest.raises(RuntimeError, match="executor rejected"):
        client.post(f"/api/applications/{app_id}/apply", headers=AUTH, json={})

    runs = client.get(f"/api/applications/{app_id}/apply-runs", headers=AUTH).json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["phase"] == "failed"
    assert runs[0]["ended_at"] is not None
    assert "executor rejected" in runs[0]["summary"]


# ---------------------------------------------------------------------------
# Cancelling a QUEUED apply op must finalize its ApplyRun (2026-07-25): the
# entrypoint that would finalize it never runs, so the generic operations-cancel
# route has to land the durable run `interrupted` itself — or start_apply's
# single-flight returns the dead run on every later Apply click for that card.
# ---------------------------------------------------------------------------


def _two_cards_with_apply_a_running(tmp_path: Path):
    """A live-runner app whose `apply` slot is OCCUPIED by a blocking fake run
    (card A), so a second card's apply op stays GENUINELY queued behind the
    single-flight (policy.py "apply":1) — the exact strand scenario. Returns
    (app, client, db, release_event, card_b_id, run_b_id, op_b_id). The caller
    must set `release` before teardown so the blocked worker can drain."""
    import threading

    from sidecar.app.registry import (
        OperationContext,
        OperationOutcome,
        OperationRegistry,
    )

    started, release = threading.Event(), threading.Event()

    def blocking_apply(ctx: OperationContext) -> OperationOutcome:
        started.set()
        release.wait(timeout=5)
        return OperationOutcome()

    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        operation_registry=OperationRegistry({"apply": blocking_apply}),
        enable_scheduler=False,
    )
    client = TestClient(app)
    client.__enter__()
    db = app.state.db
    with db.repos() as repos:
        job_a = repos.jobs.create(canonical_url="jA", title="A", source_adapter="lever")
        job_b = repos.jobs.create(canonical_url="jB", title="B", source_adapter="lever")
        card_a = repos.applications.create(job_a.id)
        card_b = repos.applications.create(job_b.id)
        run_a = repos.apply_runs.create(card_a.id, status="queued", phase="queued")
        card_a_id, run_a_id, card_b_id = card_a.id, run_a.id, card_b.id
    # A occupies the single-flight apply slot (blocks in the fake entrypoint).
    app.state.runner.submit(
        "apply", {"application_id": card_a_id, "run_id": run_a_id}
    )
    assert started.wait(timeout=5)
    # B is inserted queued; with the apply slot full the pump can't dispatch it,
    # so it stays queued deterministically (this is start_apply's row shape).
    with db.repos() as repos:
        run_b = repos.apply_runs.create(card_b_id, status="queued", phase="queued")
        op_b = repos.operations.create(
            "apply", {"application_id": card_b_id, "run_id": run_b.id}
        )
        repos.apply_runs.update(run_b.id, operation_id=op_b.id)
        run_b_id, op_b_id = run_b.id, op_b.id
    return app, client, db, release, card_b_id, run_b_id, op_b_id


def test_cancel_queued_apply_op_interrupts_its_run(tmp_path: Path) -> None:
    app, client, db, release, card_b_id, run_b_id, op_b_id = (
        _two_cards_with_apply_a_running(tmp_path)
    )
    try:
        resp = client.post(f"/api/operations/{op_b_id}/cancel", headers=AUTH)
        assert resp.status_code == 202, resp.text
        assert resp.json() == {"id": op_b_id, "kind": "apply", "state": "cancelled"}

        # The op is cancelled AND its run landed `interrupted` (not `failed`)
        # with ended_at stamped — no longer an ACTIVE row.
        with db.repos() as repos:
            assert repos.operations.get(op_b_id).state == "cancelled"
            run_row = repos.apply_runs.get(run_b_id)
            assert run_row is not None
            assert run_row.status == "interrupted"
            assert run_row.phase == "interrupted"
            assert run_row.ended_at is not None

        # Single-flight no longer returns the dead run: a fresh Apply for card B
        # mints a NEW run (it enqueues behind the still-blocked slot, so it stays
        # queued — that it is a different id is the point).
        started = client.post(
            f"/api/applications/{card_b_id}/apply", headers=AUTH, json={}
        ).json()
        assert started["id"] != run_b_id
    finally:
        release.set()
        client.__exit__(None, None, None)


def test_cancel_queued_apply_via_run_route_still_interrupts(tmp_path: Path) -> None:
    """The dedicated apply-run cancel route (POST /api/apply-runs/{id}/cancel)
    still lands a queued run `interrupted` — this path finalizes the run itself
    and must keep working alongside the generic-route fix."""
    app, client, db, release, _card_b_id, run_b_id, op_b_id = (
        _two_cards_with_apply_a_running(tmp_path)
    )
    try:
        body = client.post(f"/api/apply-runs/{run_b_id}/cancel", headers=AUTH).json()
        assert body["status"] == "interrupted"
        with db.repos() as repos:
            assert repos.operations.get(op_b_id).state == "cancelled"
            assert repos.apply_runs.get(run_b_id).ended_at is not None
    finally:
        release.set()
        client.__exit__(None, None, None)


def test_finalize_failure_does_not_mask_original_error(
    migrated_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task 2 (2026-07-25): when the run-row finalizer itself raises inside the
    late-failure handler (a transient DB error), the ORIGINAL error must still
    propagate — the runner captures it verbatim into the ledger (NFR-SIDE-04),
    and a swapped-in finalize error would corrupt that capture."""
    import asyncio

    from sidecar.app.registry import apply_op
    from sidecar.app.registry.operations import OperationContext
    from sidecar.packages.jobapplier import ApplyControl

    monkeypatch.setenv("FYJ_APPLY_DEV", "1")
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path / "data"))
    with migrated_db.repos() as repos:
        repos.profile.upsert("# Ada — master resume")
        job = repos.jobs.create(
            canonical_url="https://example.com/mask",
            title="Eng",
            company="Acme",
            source_adapter="paste-url",
        )
        card = repos.applications.create(job_id=job.id, column="saved")
        run = repos.apply_runs.create(card.id, status="queued")
        app_id, run_id = card.id, run.id

    async def exploding_render(*a: object, **kw: object) -> None:
        raise RuntimeError("original render failure")

    def exploding_finalize(db: object, rid: str, summary: str) -> None:
        raise RuntimeError("finalize DB error")

    monkeypatch.setattr(apply_op, "render_resume_pdf_async", exploding_render)
    monkeypatch.setattr(apply_op, "finalize_run_failed", exploding_finalize)
    ctx = OperationContext(
        kind="apply",
        input_snapshot={
            "run_id": run_id,
            "application_id": app_id,
            "dev_engine_script": [_action("finish", reason="x")],
        },
        db=migrated_db,
        operation_id="op-mask",
    )
    # The ORIGINAL render failure propagates — NOT the finalize DB error.
    with pytest.raises(RuntimeError, match="original render failure"):
        asyncio.run(apply_op._apply_async(ctx, app_id, ApplyControl()))


# ---------------------------------------------------------------------------
# F-M8 — apply-run artifact dirs are removed with their rows, safely.
# ---------------------------------------------------------------------------


def test_purge_run_dirs_is_guarded_and_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sidecar.app.registry.apply_op import purge_run_dirs

    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    base = tmp_path / "apply_runs"
    (base / "r1" / "screenshots").mkdir(parents=True)
    (base / "r1" / "resume.pdf").write_bytes(b"%PDF-")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")

    # Missing dirs and escape attempts must neither raise nor touch anything
    # outside the base; the real dir goes.
    purge_run_dirs(["r1", "never-materialized", "../outside.txt", "..", ""])

    assert not (base / "r1").exists()
    assert outside.exists()
    assert base.exists()
    assert tmp_path.exists()


def test_card_delete_removes_run_dirs_from_disk(app_client, tmp_path: Path) -> None:
    app, client = app_client
    _job_id, app_id = _seed_card(client)
    db = app.state.db
    with db.repos() as repos:
        run_id = repos.apply_runs.create(app_id, status="ready_for_human").id
    run_dir = tmp_path / "data" / "apply_runs" / run_id
    (run_dir / "screenshots").mkdir(parents=True)
    (run_dir / "resume.pdf").write_text("frozen")

    resp = client.delete(f"/api/applications/{app_id}", headers=AUTH)
    assert resp.status_code == 204
    assert not run_dir.exists()
    # And a card with no run dir on disk still deletes cleanly.
    _job2, app2 = _seed_card_second(client)
    with db.repos() as repos:
        repos.apply_runs.create(app2, status="failed")
    assert client.delete(f"/api/applications/{app2}", headers=AUTH).status_code == 204


def _seed_card_second(client: TestClient) -> tuple[str, str]:
    job = client.post(
        "/api/jobs",
        headers=AUTH,
        json={
            "canonical_url": "https://example.com/fm8-second",
            "title": "Second Engineer",
            "company": "Acme",
            "location": "Remote",
            "description": "Second card.",
            "source_adapter": "paste-url",
        },
    ).json()
    application = client.post(
        "/api/applications",
        headers=AUTH,
        json={"job_id": job["id"], "generate_resume": False, "generate_cover": False},
    ).json()
    return job["id"], application["id"]


def test_retention_purge_removes_run_dirs_from_disk(
    migrated_db, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from sidecar.app.db.base import now_utc
    from sidecar.app.registry.persistence import purge_archived_applications

    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    with migrated_db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="https://example.com/fm8-retention",
            title="Old Engineer",
            company="Acme",
            source_adapter="paste-url",
        )
        card = repos.applications.create(
            job_id=job.id, column="saved", archived_at=now_utc() - timedelta(days=40)
        )
        run_id = repos.apply_runs.create(card.id, status="failed").id
        app_id = card.id
    run_dir = tmp_path / "apply_runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "resume.pdf").write_text("frozen")

    purged = purge_archived_applications(migrated_db, retention_days=30)
    assert app_id in purged
    assert not run_dir.exists()
