"""The in-app browser, driven by route (audit-2026-10-14.md section 8.11, part B).

AGPL-3.0-only. These routes drive the referral surface, the Chrome the LinkedIn
view shows. Each runs on that surface's lane, so it never races a send. The
Applier's element tree names what the page offers, and its executor types and
chooses with read-back. A click, an upload or a new URL can carry something out
of the app, so the agent's waits for the user's OK (`autonomy`, action "browse");
the card names what it will do."""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sidecar.packages.jobapplier.actions import Action
from sidecar.packages.jobapplier.executor import Executor, StaleElementError, UrlPolicy
from sidecar.packages.jobapplier.observe import Observation, ObservedElement, observe
from sidecar.packages.jobapplier.prompt import render_elements
from sidecar.packages.jobapplier.types import ApplyRequest, ArtifactRef

from ..browser.broker import BrowserLaunchError
from ..browser.host import get_host
from ..registry import networker_ops
from ..registry.autonomy import NotAuthorized, authorize, caller
from . import dto
from .deps import db as _db

router = APIRouter()

# 1 page step's budget on the lane; the surface kills a Chrome that outruns it.
_STEP_DEADLINE_S = 60.0
TEXT_CHARS = 8000

# The last read of each surface: the ids the agent acts on come from here.
_LAST: dict[str, Observation] = {}
_LAST_LOCK = threading.Lock()


def _slug() -> str:
    from sidecar.packages.referral_outreach import referral_surface_slug

    return referral_surface_slug()


def _on_page(fn: Callable[[Any], Coroutine[Any, Any, Any]]) -> Any:
    """Run `fn(page)` on the surface's lane, with the async page on the browser host."""
    provider = networker_ops.SURFACE_PROVIDER
    if provider is None:
        raise HTTPException(status_code=503, detail="the in-app browser is unavailable")
    try:
        surface = provider(_slug())
        future = surface.run_on_lane(
            lambda session: get_host().run(lambda: fn(session.page.target)),
            deadline_s=_STEP_DEADLINE_S,
        )
        return future.result(timeout=_STEP_DEADLINE_S + 10)
    except BrowserLaunchError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _element(element_id: str) -> tuple[Observation, ObservedElement]:
    with _LAST_LOCK:
        obs = _LAST.get(_slug())
    if obs is not None:
        for element in obs.elements:
            if element.element_id == element_id:
                return obs, element
    raise HTTPException(
        status_code=409, detail=f"no element {element_id!r} on the last page read; read it again"
    )


def _require_outreach(request: Request) -> None:
    """The in-app browser is Referral Outreach's: shut until the user opts in."""
    with _db(request).repos() as repos:
        if not repos.preferences.get_or_create().voyager_risk_marker_on:
            raise HTTPException(
                status_code=409, detail="Referral Outreach is off; turn it on in Settings first."
            )


def _ask(request: Request, what: str) -> None:
    """403 with `what` named, unless the caller may act unasked; the chat shows a card."""
    try:
        authorize(_db(request), "browse", caller())
    except NotAuthorized as exc:
        raise HTTPException(status_code=403, detail=f"{what}: {exc.reason}") from exc


def _describe(element: ObservedElement) -> str:
    name = element.label or element.text.strip()[:80] or element.attributes.get("type", "")
    return f"the {element.tag} {name!r}" if name else f"the {element.tag}"


def _act(element_id: str, tool: str, args: dict[str, Any],
         artifacts: tuple[ArtifactRef, ...] = ()) -> dto.BrowserActionResult:
    obs, _ = _element(element_id)
    request = ApplyRequest(
        run_id="chat", application_id="", job_url=obs.url, company="", role="",
        jd_text="", profile_facts={}, preferences={}, approved_links=(),
        artifacts=artifacts, resume_label="",
    )

    async def _run(page: Any) -> tuple[bool, str, str]:
        executor = Executor(page, request, UrlPolicy())
        executor.bind_observation(obs)
        outcome = await executor.execute(Action(tool=tool, args={"element_id": element_id, **args}))
        return outcome.ok, outcome.note, page.url

    try:
        ok, note, url = _on_page(_run)
    except StaleElementError as exc:
        raise HTTPException(status_code=409, detail=f"{exc}; read the page again") from exc
    return dto.BrowserActionResult(ok=ok, note=note, url=url)


@router.get("/api/browser/page")
def browser_page(request: Request) -> dto.BrowserPageDTO:
    """Read the in-app browser: its URL, title and every element you can act on, as
    `e1`, `e2`, ... Read it before you click, type, choose or upload, and again
    after the page changes: the ids hold until the next read."""
    _require_outreach(request)
    obs: Observation = _on_page(observe)
    with _LAST_LOCK:
        _LAST[_slug()] = obs
    return dto.BrowserPageDTO(url=obs.url, title=obs.title, elements=render_elements(obs))


@router.get("/api/browser/page/text")
def browser_page_text(request: Request) -> dto.BrowserTextDTO:
    """The visible text of the page the in-app browser shows."""
    _require_outreach(request)

    async def _read(page: Any) -> tuple[str, str]:
        text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        return page.url, str(text or "")

    url, text = _on_page(_read)
    return dto.BrowserTextDTO(url=url, text=text[:TEXT_CHARS], truncated=len(text) > TEXT_CHARS)


@router.post("/api/browser/navigate")
def browser_navigate(
    request: Request, payload: dto.BrowserNavigateRequest
) -> dto.BrowserActionResult:
    """Open a URL in the in-app browser, logged in as the user. The user confirms
    each one you ask for: a URL can carry data out."""
    _require_outreach(request)
    from ..registry.apply_op import _dev_enabled

    # Local pages only under the e2e's dev flag, the Applier's same seam.
    refused = UrlPolicy(allow_local=_dev_enabled()).check(payload.url)
    if refused is not None:
        raise HTTPException(status_code=422, detail=f"URL refused: {refused}")
    _ask(request, f"Open {payload.url}")
    provider = networker_ops.SURFACE_PROVIDER
    if provider is None:
        raise HTTPException(status_code=503, detail="the in-app browser is unavailable")
    try:
        # The surface's own navigate: it refuses until the view has sent its real size.
        url = provider(_slug()).navigate(payload.url).result(timeout=_STEP_DEADLINE_S)
    except BrowserLaunchError as exc:
        raise HTTPException(
            status_code=409, detail=f"{exc}. Open the LinkedIn view once, then try again."
        ) from exc
    return dto.BrowserActionResult(ok=True, note=f"opened {url}", url=str(url))


@router.post("/api/browser/click")
def browser_click(
    request: Request, payload: dto.BrowserElementRequest
) -> dto.BrowserActionResult:
    """Click an element from the last page read. A click can send or submit, so the
    user confirms each one you ask for."""
    _require_outreach(request)
    obs, element = _element(payload.element_id)
    _ask(request, f"Click {_describe(element)} on {obs.url}")
    return _act(payload.element_id, "click", {})


@router.post("/api/browser/type")
def browser_type(request: Request, payload: dto.BrowserTypeRequest) -> dto.BrowserActionResult:
    """Type text into a field from the last page read, replacing its value, and read
    it back. It never presses Enter and never fills a password."""
    _require_outreach(request)
    return _act(payload.element_id, "fill", {"value": payload.text})


@router.post("/api/browser/choose")
def browser_choose(request: Request, payload: dto.BrowserChooseRequest) -> dto.BrowserActionResult:
    """Choose an option in a select or a dropdown from the last page read."""
    _require_outreach(request)
    return _act(payload.element_id, "select", {"option": payload.option})


@router.post("/api/browser/upload")
def browser_upload(
    request: Request, payload: dto.BrowserUploadRequest
) -> dto.BrowserActionResult:
    """Attach 1 of the user's documents to a file field from the last page read, as
    a PDF: "master_resume", or the id of a tailored resume or cover letter. A page
    can send a file the moment it is picked, so the user confirms each one."""
    _require_outreach(request)
    with _db(request).repos() as repos:
        if payload.document_id == "master_resume":
            profile = repos.profile.get_current()
            markdown, label = (profile.resume_markdown if profile else ""), "master resume"
        else:
            artifact = repos.artifacts.get(payload.document_id)
            markdown = artifact.markdown if artifact is not None else ""
            label = artifact.kind.replace("_", " ") if artifact is not None else ""
    if not markdown:
        raise HTTPException(status_code=404, detail=f"no document {payload.document_id!r}")
    obs, element = _element(payload.element_id)
    _ask(request, f"Upload your {label} to {_describe(element)} on {obs.url}")
    with tempfile.TemporaryDirectory(prefix="fyj-upload-") as tmp:
        path = Path(tmp) / f"{label.replace(' ', '-')}.pdf"

        async def _render() -> None:
            from ..registry.pdf import render_resume_pdf_async

            await render_resume_pdf_async(await get_host().playwright(), markdown, str(path))

        get_host().run(_render, timeout=_STEP_DEADLINE_S)
        artifact_ref = ArtifactRef(
            artifact_id=payload.document_id, label=label, path=str(path), kind="resume"
        )
        return _act(payload.element_id, "upload_artifact",
                    {"artifact_id": payload.document_id}, (artifact_ref,))
