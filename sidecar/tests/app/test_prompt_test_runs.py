"""Running a prompt draft without committing it (url-less-jobs.md section 11, S-C70).

The problem it closes: Save was the only way to run a prompt, so the only way to
try one was to overwrite the shipped default, generate on a real job, read it,
and edit again, with the user's working default broken for the whole loop. The
tailoring prompt is 16,285 characters and the output it governs is a resume that
goes to an employer.

The 3 invariants, each tested rather than argued:

1. The draft runs. The committed prompt does not.
2. The run writes nothing: no score on the job, no artifact, no profile.
3. The run is an ordinary operation, so its spend reaches the ledger.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.app.registry import EngineRegistry, OperationContext
from sidecar.app.registry.operations import (
    is_prompt_test,
    prompt_for,
    score_entrypoint,
    tailor_entrypoint,
)
from sidecar.modules._shared.claude_engine import EngineUsage

TOKEN = "test-token-prompt-tests"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}

REALISTIC_JD = (
    "Backend engineer role. You will build and operate Python services, design Post"
    "greSQL schemas, run workloads on AWS, and review other engineers' work. Requirem"
    "ents: strong Python, solid SQL, and production ownership experience."
)

SCORE_OUT = (
    "===SCORE===\n77\n===REASONS===\n- Strong backend overlap\n- Relocation matches\n"
    "===BREAKDOWN===\nRequirement | Match\n--- | ---\nJava | yes\n"
)

TAILOR_OUT = "===RESUME===\n# Tailored\n\nSome resume.\n===NOTES===\n- Kept every claim\n"


class _Recorder:
    """Remembers the system prompt it was handed, which is the whole question."""

    def __init__(self, reply: str = SCORE_OUT) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, EngineUsage]:
        self.prompts.append(system_prompt)
        return self.reply, EngineUsage(
            internal_calls=1, tokens_in=100, tokens_out=40, usd=0.01,
            latency_ms=5, model="fake-model",
        )


def _ctx(kind: str, snap: dict[str, Any], engine: Any, db: Any = None) -> OperationContext:
    engines = EngineRegistry()
    engines.register("fake", engine)
    engines.route(kind, engine="fake", model="fake-model")
    return OperationContext(
        kind=kind, input_snapshot=snap, engine=engines.resolve(kind), db=db,
        operation_id=None,
    )


def test_the_draft_runs_and_the_saved_prompt_does_not(tmp_path, monkeypatch) -> None:
    from sidecar.app import prompt_overrides

    monkeypatch.setattr(prompt_overrides, "resolve_data_dir", lambda *a, **k: tmp_path)
    prompt_overrides.set_override("score", "# THE SAVED ONE", tmp_path)

    saved = OperationContext(kind="score", input_snapshot={}, engine=None, db=None)
    assert prompt_for(saved, "score") == "# THE SAVED ONE"

    drafting = OperationContext(
        kind="score", input_snapshot={"prompt_draft_md": "# THE DRAFT"}, engine=None, db=None
    )
    assert prompt_for(drafting, "score") == "# THE DRAFT"
    # And the saved one is still exactly where it was.
    assert prompt_overrides.get_override("score", tmp_path) == "# THE SAVED ONE"


@pytest.mark.parametrize("draft", ["", "   ", None])
def test_an_empty_draft_falls_back_rather_than_running_nothing(
    draft, tmp_path, monkeypatch
) -> None:
    from sidecar.app import prompt_overrides

    monkeypatch.setattr(prompt_overrides, "resolve_data_dir", lambda *a, **k: tmp_path)
    ctx = OperationContext(
        kind="score", input_snapshot={"prompt_draft_md": draft}, engine=None, db=None
    )
    assert prompt_for(ctx, "score") is None


def test_the_engine_really_receives_the_draft() -> None:
    engine = _Recorder()
    ctx = _ctx(
        "score",
        {
            "job": REALISTIC_JD,
            "master_md": "# Candidate\n\nPython.",
            "prompt_draft_md": "# DRAFT RUBRIC",
            "prompt_test": True,
        },
        engine,
    )
    score_entrypoint(ctx)
    assert engine.prompts == ["# DRAFT RUBRIC"]


def test_a_test_run_writes_no_artifact(tmp_path) -> None:
    """A tailor test must not leave a document behind. The user is trying a
    prompt out, not asking for a resume."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app):
        db = app.state.db
        with db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython, SQL.")
        engine = _Recorder(TAILOR_OUT)
        ctx = _ctx(
            "tailor",
            {
                "job": REALISTIC_JD,
                "master_md": "# Candidate\n\nPython.",
                "prompt_draft_md": "# DRAFT",
                "prompt_test": True,
            },
            engine,
            db=db,
        )
        outcome = tailor_entrypoint(ctx)
        assert outcome.result_ref is not None
        assert outcome.result_ref["prompt_test"] is True
        # The output rides the operation, which is where the modal reads it.
        assert "Some resume" in outcome.result_ref["output_md"]
        assert "artifact_id" not in outcome.result_ref
        # And the ledger still gets the spend, because it is a real LLM call.
        assert outcome.usage is not None
        assert outcome.usage["usd"] == 0.01


def test_a_score_test_run_never_touches_the_job_s_real_score(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython, SQL.")
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/prompt-test",
                title="Backend Engineer", company="Acme", location="Remote",
                description=REALISTIC_JD, source_adapter="greenhouse",
            ).id
            repos.jobs.set_score(
                job_id, scorer_impl="scorer-llm", score_0_100=42,
                reasons=["before"], breakdown_md="", method="llm",
            )

        resp = client.post(
            "/api/settings/prompts/score/test",
            headers=AUTH,
            json={"markdown": "# DRAFT", "job_id": job_id},
        )
        assert resp.status_code == 202
        with db.repos() as repos:
            op = repos.operations.get(resp.json()["id"])
            assert op is not None
            assert op.input_snapshot["prompt_test"] is True
            assert op.input_snapshot["prompt_draft_md"] == "# DRAFT"
            # The job's text is copied in and its ID is NOT, which is what stops
            # the score path writing onto the real row.
            assert "job_id" not in op.input_snapshot
            assert "Backend Engineer" in op.input_snapshot["job"]
            job = repos.jobs.get(job_id)
            assert job is not None
            assert job.llm_score == 42


def test_a_pasted_description_works_with_nothing_on_the_board(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        with app.state.db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython.")
        resp = client.post(
            "/api/settings/prompts/tailor/test",
            headers=AUTH,
            json={"markdown": "# DRAFT", "job_description": REALISTIC_JD},
        )
        assert resp.status_code == 202
        assert resp.json()["kind"] == "tailor"


def test_a_job_kind_with_neither_a_job_nor_a_description_is_refused(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        with app.state.db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython.")
        resp = client.post(
            "/api/settings/prompts/cover/test", headers=AUTH, json={"markdown": "# D"}
        )
        assert resp.status_code == 422


def test_extract_needs_no_input_picker_at_all(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        with app.state.db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython.")
        resp = client.post(
            "/api/settings/prompts/extract/test", headers=AUTH, json={"markdown": "# D"}
        )
        assert resp.status_code == 202
        assert resp.json()["kind"] == "extract"


def test_the_draft_kind_needs_a_real_contact(tmp_path) -> None:
    """A made-up contact would test the prompt against input that never occurs
    (url-less-jobs.md P2)."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        with app.state.db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython.")
        assert (
            client.post(
                "/api/settings/prompts/networker_draft/test",
                headers=AUTH, json={"markdown": "# D"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/settings/prompts/networker_draft/test",
                headers=AUTH, json={"markdown": "# D", "contact_id": "nope"},
            ).status_code
            == 404
        )


def test_an_unknown_kind_and_an_empty_draft_are_both_refused(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/settings/prompts/nope/test", headers=AUTH, json={"markdown": "# D"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/settings/prompts/score/test", headers=AUTH, json={"markdown": "  "}
            ).status_code
            == 422
        )


def test_a_test_run_is_flagged_rather_than_being_its_own_kind() -> None:
    """P3: `Operation.kind` drives the Analytics grouping in 3 places, and a
    test run IS a tailor, which is what makes its cost comparable."""
    plain = OperationContext(kind="tailor", input_snapshot={}, engine=None, db=None)
    test = OperationContext(
        kind="tailor", input_snapshot={"prompt_test": True}, engine=None, db=None
    )
    assert is_prompt_test(plain) is False
    assert is_prompt_test(test) is True


# --- the ledger row (url-less-jobs.md drawing 13) -----------------------------


def test_the_ledger_names_a_run_that_has_no_job_behind_it(tmp_path) -> None:
    """A triage score and a prompt test both have no job row, and until the
    ledger learned to read `result_ref` they rendered as a bare kind with
    nothing to say. A row the user paid for has to say what it was."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            repos.profile.upsert("# Candidate\n\nPython.")
            triage = repos.operations.create(
                "score", {"job": "Staff Backend Engineer\n\n" + REALISTIC_JD}
            )
            repos.operations.mark_succeeded(
                triage.id, result_ref={"score": 72, "job_id": None, "method": "jev"}
            )
            test_run = repos.operations.create(
                "tailor",
                {"job": "Platform Engineer\n\n" + REALISTIC_JD, "prompt_test": True},
            )
            repos.operations.mark_succeeded(
                test_run.id, result_ref={"prompt_test": True, "output_md": "# R"}
            )

        rows = {r["id"]: r for r in client.get("/api/operations", headers=AUTH).json()}

        triage_row = rows[triage.id]["subject"]
        assert triage_row is not None
        assert triage_row["label"] == "Staff Backend Engineer"
        # The number and the method it came from, in the row itself.
        assert triage_row["badge"] == "72 · jev"

        test_row = rows[test_run.id]["subject"]
        assert test_row is not None
        assert test_row["label"] == "Platform Engineer"
        # P3: a test run is marked, not given its own kind, so it still groups
        # and costs as the tailor it is.
        assert test_row["badge"] == "prompt test"
        assert rows[test_run.id]["kind"] == "tailor"


def test_a_job_backed_row_is_unchanged_by_any_of_this(tmp_path) -> None:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/ledger-row",
                title="Backend Engineer", company="Acme",
                description=REALISTIC_JD, source_adapter="greenhouse",
            ).id
            op = repos.operations.create("tailor", {"job_id": job_id})
            repos.operations.mark_succeeded(op.id, result_ref={"artifact_id": "a1"})
        row = {r["id"]: r for r in client.get("/api/operations", headers=AUTH).json()}[op.id]
        assert row["subject"]["label"] == "Backend Engineer"
        assert row["subject"]["context"] == "Acme"
        assert row["subject"]["badge"] is None


def test_a_tailor_row_names_the_document_it_wrote(tmp_path) -> None:
    """url-less-jobs.md drawing 13: the ledger linked out to the posting and
    nothing else, so reading your own tailored resume meant hunting for the
    card in the Tracker. The row carries where the artifact lives now."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/artifact-row",
                title="Backend Engineer", company="Acme",
                description=REALISTIC_JD, source_adapter="greenhouse",
            ).id
            application_id = repos.applications.create(job_id).id
            artifact_id = repos.artifacts.create(
                application_id, kind="tailored", markdown="# Tailored"
            ).id
            tailor = repos.operations.create("tailor", {"job_id": job_id})
            repos.operations.mark_succeeded(tailor.id, result_ref={"artifact_id": artifact_id})
            cover_artifact = repos.artifacts.create(
                application_id, kind="cover", markdown="# Cover"
            ).id
            cover = repos.operations.create("cover", {"job_id": job_id})
            repos.operations.mark_succeeded(cover.id, result_ref={"artifact_id": cover_artifact})
            # A score row writes no document, so it must carry no destination.
            score = repos.operations.create("score", {"job_id": job_id})
            repos.operations.mark_succeeded(score.id, result_ref={"score": 72, "method": "jev"})

        rows = {r["id"]: r for r in client.get("/api/operations", headers=AUTH).json()}
        assert rows[tailor.id]["subject"]["artifact"] == {
            "application_id": application_id, "kind": "tailored",
        }
        assert rows[cover.id]["subject"]["artifact"] == {
            "application_id": application_id, "kind": "cover",
        }
        assert rows[score.id]["subject"]["artifact"] is None


def test_a_tailor_row_whose_artifact_is_gone_says_nothing(tmp_path) -> None:
    """Nothing is fabricated: a deleted artifact leaves the row as it was."""
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        db = app.state.db
        with db.repos() as repos:
            job_id = repos.jobs.create(
                canonical_url="https://ex.co/j/missing-artifact",
                title="Backend Engineer", company="Acme",
                description=REALISTIC_JD, source_adapter="greenhouse",
            ).id
            op = repos.operations.create("tailor", {"job_id": job_id})
            repos.operations.mark_succeeded(op.id, result_ref={"artifact_id": "gone"})
        row = {r["id"]: r for r in client.get("/api/operations", headers=AUTH).json()}[op.id]
        assert row["subject"]["label"] == "Backend Engineer"
        assert row["subject"]["artifact"] is None
