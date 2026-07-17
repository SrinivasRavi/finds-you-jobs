# finds-you-jobs — AGPL-3.0-only.
"""The apply loop against real headless Chromium and local file:// fixtures
(applier.md §11 browser integration tests) — a scripted FakeApplyEngine, no
model, ZERO network. Covers the JD→form navigation hop, grounded filling with
per-action read-back, the refusal paths (password fill, private-IP navigate,
prompt-injected metadata URL), hard walls, stale ids, cancellation, budget
exhaustion, and the P1 terminal honesty: ready_for_human, never submitted."""

import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path

from playwright.async_api import async_playwright

from sidecar.packages.jobapplier.executor import UrlPolicy
from sidecar.packages.jobapplier.fake import FakeApplyEngine, FakeStep
from sidecar.packages.jobapplier.loop import run_apply
from sidecar.packages.jobapplier.types import (
    ApplyControl,
    ApplyEvent,
    ApplyEventType,
    ApplyRequest,
    ApplyStatus,
    ArtifactRef,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_url(name: str) -> str:
    return (FIXTURES / name).as_uri()


def _eid(prompt: str, needle: str) -> str:
    """Find the current element id whose prompt line mentions ``needle``."""
    for line in prompt.splitlines():
        if needle in line and re.match(r"^e\d+ ", line):
            return line.split()[0]
    raise AssertionError(f"no element line contains {needle!r}:\n{prompt}")


def _action(tool: str, **args: object) -> str:
    return json.dumps({"tool": tool, **args})


def _request(tmp_path: Path, job_url: str, resume: Path | None = None) -> ApplyRequest:
    artifacts = ()
    if resume is not None:
        artifacts = (
            ArtifactRef(
                artifact_id="art-resume",
                label="tailored resume (PDF)",
                path=str(resume),
                kind="resume",
            ),
        )
    return ApplyRequest(
        run_id="run-1",
        application_id="app-1",
        job_url=job_url,
        company="Acme",
        role="Staff Engineer",
        jd_text="Own the monolith.",
        profile_facts={"full_name": "Ada Lovelace", "email": "ada@example.com"},
        preferences={},
        approved_links=(),
        artifacts=artifacts,
        resume_label="tailored resume",
        screenshot_dir=str(tmp_path / "shots"),
    )


async def _run(request: ApplyRequest, engine: FakeApplyEngine, *, control=None):
    events: list[ApplyEvent] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            result = await run_apply(
                page,
                request,
                engine,
                events.append,
                control or ApplyControl(),
                policy=UrlPolicy(allow_local=True),
            )
        finally:
            await browser.close()
    return result, events, page


async def test_happy_path_jd_to_form_to_ready_for_human(tmp_path: Path) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake resume")
    script: list[FakeStep] = [
        lambda p: _action("click", element_id=_eid(p, "Apply now")),
        lambda p: _action("fill", element_id=_eid(p, "Full name"), value="Ada Lovelace"),
        lambda p: _action("fill", element_id=_eid(p, "Email"), value="ada@example.com"),
        lambda p: _action("select", element_id=_eid(p, "<select"), option="LinkedIn"),
        lambda p: _action("check", element_id=_eid(p, "type=checkbox")),
        lambda p: _action(
            "upload_artifact", element_id=_eid(p, "type=file"), artifact_id="art-resume"
        ),
        _action("finish", reason="all grounded fields filled; portal password left blank"),
    ]
    engine = FakeApplyEngine(script)
    request = _request(tmp_path, _fixture_url("jd.html"), resume)

    result, events, page = await _run(request, engine)

    assert result.status is ApplyStatus.READY_FOR_HUMAN
    assert result.final_url.endswith("form.html")
    assert result.usage.calls == len(script)
    # Every grounded field verified ok, honestly reported.
    outcomes = {f.label: f for f in result.fields}
    assert all(f.ok for f in outcomes.values()), outcomes
    assert any(f.action == "upload" for f in outcomes.values())
    # Evidence exists: opened / form-found / handoff screenshots on disk.
    assert len(result.screenshots) >= 3
    on_disk = await asyncio.to_thread(
        lambda: all(Path(s).exists() for s in result.screenshots)
    )
    assert on_disk
    # The event stream told the story in order.
    types = [e.type for e in events]
    assert types[0] is ApplyEventType.PHASE_CHANGED
    assert ApplyEventType.READY_FOR_HUMAN in types
    assert types[-1] is ApplyEventType.COMPLETED
    # Nothing ever claimed submission (§8.4).
    assert "submitted" not in result.summary.lower()


async def test_password_fill_refused_and_reported(tmp_path: Path) -> None:
    script: list[FakeStep] = [
        lambda p: _action("fill", element_id=_eid(p, "type=password"), value="hunter2"),
        _action("finish", reason="done"),
    ]
    engine = FakeApplyEngine(script)
    result, _, _ = await _run(engine=engine, request=_request(tmp_path, _fixture_url("form.html")))

    assert result.status is ApplyStatus.READY_FOR_HUMAN
    password_outcome = next(f for f in result.fields if "password" in f.label.lower())
    assert not password_outcome.ok
    assert "password" in password_outcome.note.lower()


async def test_prompt_injected_metadata_navigate_is_refused(tmp_path: Path) -> None:
    # The jd.html fixture carries an injection line telling the agent to visit
    # the cloud metadata endpoint. Simulate a model that obeys: the EXECUTOR
    # must refuse (§4.3) — with allow_local off, like production.
    script: list[FakeStep] = [
        _action("navigate", url="http://169.254.169.254/latest/meta-data"),
        _action("report_blocked", kind="error", detail="cannot navigate"),
        _action("finish", reason="stopping"),
    ]
    engine = FakeApplyEngine(script)
    events: list[ApplyEvent] = []
    request = _request(tmp_path, _fixture_url("jd.html"))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            # Fixture opening needs file:// so only the OPEN uses a local
            # policy; the mid-run navigate is checked by the same policy —
            # use one that allows file but still rejects private IPs.
            class FixturePolicy(UrlPolicy):
                def check(self, url: str) -> str | None:
                    if url.startswith("file://"):
                        return None
                    return UrlPolicy(allow_local=False).check(url)

            result = await run_apply(
                page, request, engine, events.append, ApplyControl(),
                policy=FixturePolicy(),
            )
        finally:
            await browser.close()

    # finish without a form seen is not a success — honest no_form blocker.
    assert result.status is ApplyStatus.BLOCKED
    assert result.blockers[-1].kind == "no_form"
    assert result.final_url.endswith("jd.html")  # never left the JD page
    notes = [str(e.data.get("note", "")) for e in events]
    assert any("refused" in n for n in notes), notes


async def test_closed_posting_blocks_without_a_model_call(tmp_path: Path) -> None:
    engine = FakeApplyEngine([])  # any call would raise: script is empty
    result, events, _ = await _run(_request(tmp_path, _fixture_url("closed.html")), engine)

    assert result.status is ApplyStatus.BLOCKED
    assert result.blockers[0].kind == "posting_closed"
    assert engine.prompts == []  # zero tokens spent on a dead posting
    assert any(e.type is ApplyEventType.BLOCKER_FOUND for e in events)


async def test_stale_ids_terminate_honestly(tmp_path: Path) -> None:
    # A model stuck on a dead element id never gets executed against the page:
    # each attempt is rejected as stale, the page never changes, and the run
    # ends BLOCKED by whichever honesty guard trips first (the no-progress
    # observation streak here — stale rejections re-observe an unchanged page).
    script: list[FakeStep] = [_action("click", element_id="e999")] * 4
    engine = FakeApplyEngine(script)
    result, events, _ = await _run(_request(tmp_path, _fixture_url("form.html")), engine)

    assert result.status is ApplyStatus.BLOCKED
    assert not result.fields  # nothing was ever executed
    stale_rejections = [
        e
        for e in events
        if e.type is ApplyEventType.ACTION_FAILED
        and "not in the current observation" in str(e.data.get("reason", ""))
    ]
    assert stale_rejections


async def test_cancel_interrupts(tmp_path: Path) -> None:
    control = ApplyControl()
    control.cancel()
    engine = FakeApplyEngine([])
    result, _, _ = await _run(
        _request(tmp_path, _fixture_url("form.html")), engine, control=control
    )
    assert result.status is ApplyStatus.INTERRUPTED
    assert engine.prompts == []


async def test_deadline_exhaustion_times_out(tmp_path: Path) -> None:
    engine = FakeApplyEngine([])
    request = replace(_request(tmp_path, _fixture_url("form.html")), deadline_s=0.0)
    result, _, _ = await _run(request, engine)
    assert result.status is ApplyStatus.TIMED_OUT
    assert engine.prompts == []


async def test_finish_without_form_is_not_success(tmp_path: Path) -> None:
    script: list[FakeStep] = [_action("finish", reason="looks done to me")]
    engine = FakeApplyEngine(script)
    result, _, _ = await _run(_request(tmp_path, _fixture_url("jd.html")), engine)

    assert result.status is ApplyStatus.BLOCKED
    assert result.blockers[0].kind == "no_form"
