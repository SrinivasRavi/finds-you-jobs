# finds-you-jobs — AGPL-3.0-only.
"""The deterministic decider against real headless Chromium and local file://
fixtures — no model, zero network, zero cost. Proves the code rung fills the
spine from the profile, skips honeypots by pattern, refuses passwords, drives a
drag-drop upload, and hands off ready_for_human without ever submitting."""

from pathlib import Path

from playwright.async_api import async_playwright

from sidecar.packages.jobapplier.decide import Decider
from sidecar.packages.jobapplier.executor import UrlPolicy
from sidecar.packages.jobapplier.loop import run_apply
from sidecar.packages.jobapplier.types import (
    ApplyControl,
    ApplyRequest,
    ApplyStatus,
    ArtifactRef,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Descriptive persona keys the decider's derivers read (the shipped eval's
# persona shape). No atom is hardcoded in the decider; every value flows here.
PERSONA = {
    "legal name": "Tenet Loader (first Tenet, last Loader)",
    "email": "tenetloader@gmail.com",
    "phone": "+91 9999999999",
    "location": "Mumbai, Maharashtra, India",
    "citizenship": "India",
    "work authorization": "authorized in India only; requires employer sponsorship elsewhere",
    "experience": "9 years professional software engineering",
    "current role": "Software Engineer, self-employed at srini404.com",
}
PREFERENCES = {
    "demographics": "decline on every demographic survey",
    "consents": "consent to recruitment processing; decline marketing",
    "how heard about the job": "LinkedIn job listing",
    "cover letters": "no cover letter is held",
}
READBACK_JS = """() => Array.from(document.querySelectorAll('input,select,textarea')).map(e => ({
  name: e.getAttribute('name'), type: e.type || null,
  value: (e.type === 'checkbox' || e.type === 'radio') ? null : e.value,
  checked: (e.type === 'checkbox' || e.type === 'radio') ? e.checked : null,
}))"""


def _request(tmp_path: Path, job_url: str, resume: Path | None = None) -> ApplyRequest:
    artifacts = ()
    if resume is not None:
        artifacts = (
            ArtifactRef(artifact_id="art-resume", label="master resume (PDF)", path=str(resume), kind="resume"),
        )
    return ApplyRequest(
        run_id="run-d",
        application_id="app-d",
        job_url=job_url,
        company="Acme",
        role="Staff Engineer",
        jd_text="Own the monolith.",
        profile_facts=PERSONA,
        preferences=PREFERENCES,
        approved_links=(
            "https://www.linkedin.com/in/tenet-loader/",
            "https://github.com/tenetloader",
            "https://srini404.com",
        ),
        artifacts=artifacts,
        resume_label="master resume",
        screenshot_dir=str(tmp_path / "shots"),
    )


async def _run_decider(request: ApplyRequest):
    """Run the decider through the real loop, returning (result, dom_values)."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        try:
            result = await run_apply(
                page, request, Decider(), lambda _ev: None, ApplyControl(),
                policy=UrlPolicy(allow_local=True),
            )
            values = await page.evaluate(READBACK_JS)
        finally:
            await browser.close()
    return result, {v["name"]: v for v in values if v["name"]}


async def test_decider_fills_the_spine(tmp_path: Path) -> None:
    result, dom = await _run_decider(_request(tmp_path, (FIXTURES / "form.html").as_uri()))

    assert result.status is ApplyStatus.READY_FOR_HUMAN
    assert dom["name"]["value"] == "Tenet Loader"
    assert dom["email"]["value"] == "tenetloader@gmail.com"
    # The portal password is never filled.
    assert dom["portal_password"]["value"] == ""
    # The how-did-you-hear select landed on LinkedIn.
    assert dom["source"]["value"] == "li"


async def test_decider_skips_a_honeypot(tmp_path: Path) -> None:
    result, dom = await _run_decider(_request(tmp_path, (FIXTURES / "honeypot_form.html").as_uri()))

    assert result.status is ApplyStatus.READY_FOR_HUMAN
    # Real fields filled...
    assert dom["name"]["value"] == "Tenet Loader"
    assert dom["email"]["value"] == "tenetloader@gmail.com"
    assert dom["linkedin"]["value"] == "https://www.linkedin.com/in/tenet-loader/"
    # ...but the honeypot stays pristine even though it renders visible here.
    assert dom["hp_7f2b"]["value"] == ""


async def test_decider_uploads_resume_to_dropzone(tmp_path: Path) -> None:
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 fake resume")
    result, _ = await _run_decider(_request(tmp_path, (FIXTURES / "dropzone.html").as_uri(), resume))

    assert result.status is ApplyStatus.READY_FOR_HUMAN
    upload = next((f for f in result.fields if f.action == "upload"), None)
    assert upload is not None and upload.ok, result.fields
