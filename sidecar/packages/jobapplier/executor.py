# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""Action executor (docs/internal/applier.md §4.2/§4.3/§5.3).

The executor is the enforcement layer: it re-checks everything the prompt
promises, so a prompt-injected or confused model still cannot step outside
the contract. Independently of what the model asks for:

- an action must reference an element id from the CURRENT observation —
  stale ids raise ``StaleElementError`` instead of guessing (§4.1);
- ``navigate`` obeys a scheme/host policy — private/loopback redirect targets
  are rejected (§4.3);
- only user-approved artifacts can be uploaded, chosen by artifact_id — the
  model never sees or supplies a filesystem path (§5.3);
- password/TOTP inputs cannot be filled — the product holds no site
  credentials (§4.3);
- every mutating action is verified by read-back where the control supports
  it, and the outcome is reported honestly (§4.2).
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page

from .actions import Action
from .observe import Observation, ObservedElement
from .types import ApplyRequest, DisallowedActionError, StaleElementError
from .upstream.constants import SKYVERN_ID_ATTR

_NAV_TIMEOUT_MS = 30_000
_ACTION_TIMEOUT_MS = 10_000


@dataclass(frozen=True)
class ExecOutcome:
    """What actually happened, with read-back evidence where available."""

    ok: bool
    note: str = ""


class UrlPolicy:
    """Scheme/host policy for navigate + redirect checks (§4.3).

    ``allow_local`` exists ONLY so tests can drive local fixture pages
    (file:// and loopback); the production loop constructs the default."""

    def __init__(self, *, allow_local: bool = False) -> None:
        self.allow_local = allow_local

    def check(self, url: str) -> str | None:
        """None when allowed, else a redacted refusal reason."""
        parsed = urlparse(url)
        if self.allow_local and parsed.scheme == "file":
            return None
        if parsed.scheme not in {"http", "https"}:
            return f"scheme {parsed.scheme!r} is not allowed"
        host = parsed.hostname or ""
        if not host:
            return "URL has no host"
        if self.allow_local:
            return None
        if host == "localhost" or host.endswith(".local"):
            return "loopback/link-local host"
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return None  # a normal DNS name
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            return "private/loopback address"
        return None


class Executor:
    """Executes validated Actions against the live page for one run."""

    def __init__(self, page: Page, request: ApplyRequest, policy: UrlPolicy) -> None:
        self._page = page
        self._request = request
        self._policy = policy
        self._observation: Observation | None = None

    def bind_observation(self, obs: Observation) -> None:
        """The loop calls this after every observe; ids from any earlier
        observation are dead from this moment (§4.1)."""
        self._observation = obs

    async def execute(self, action: Action) -> ExecOutcome:
        handler = getattr(self, f"_do_{action.tool}", None)
        if handler is None:  # finish/report_blocked terminate in the loop
            raise DisallowedActionError(f"{action.tool} is not executable")
        return await handler(action)

    # -- element plumbing ---------------------------------------------------

    def resolve(self, element_id: str) -> ObservedElement:
        obs = self._observation
        if obs is None:
            raise StaleElementError("no current observation")
        for element in obs.elements:
            if element.element_id == element_id:
                return element
        raise StaleElementError(
            f"element {element_id!r} is not in the current observation"
        )

    async def _locator(self, element: ObservedElement):
        """Locate by the per-scan ``unique_id`` attribute, searching the main
        frame first and then every child frame. The ids are globally unique
        per observation, so the first hit is THE element — this deliberately
        does not trust ``frame_index`` positions, because the observation
        numbers only the frames it walked (filtered), not ``page.frames``
        order. Not found anywhere → the observation is stale (§4.1)."""
        selector = f'[{SKYVERN_ID_ATTR}="{element.unique_id}"]'
        main = self._page.locator(selector).first
        if await main.count():
            return main
        for frame in self._page.frames:
            candidate = frame.locator(selector).first
            if await candidate.count():
                return candidate
        raise StaleElementError(
            f"element {element.element_id!r} ({element.label or element.tag}) "
            "is no longer on the page"
        )

    # -- tools ---------------------------------------------------------------

    async def _do_click(self, action: Action) -> ExecOutcome:
        element = self.resolve(str(action.args["element_id"]))
        locator = await self._locator(element)
        await locator.click(timeout=_ACTION_TIMEOUT_MS)
        return ExecOutcome(ok=True, note=f"clicked {element.label or element.tag}")

    async def _do_navigate(self, action: Action) -> ExecOutcome:
        url = urljoin(self._page.url, str(action.args["url"]))
        refusal = self._policy.check(url)
        if refusal is not None:
            return ExecOutcome(ok=False, note=f"navigation refused: {refusal}")
        await self._page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        landed = self._policy.check(self._page.url)
        if landed is not None:
            return ExecOutcome(ok=False, note=f"redirect refused: {landed}")
        return ExecOutcome(ok=True, note=f"at {self._page.url}")

    async def _do_scroll(self, action: Action) -> ExecOutcome:
        amount = int(float(action.args.get("amount", 600)))
        delta = amount if action.args["direction"] == "down" else -amount
        await self._page.mouse.wheel(0, delta)
        return ExecOutcome(ok=True, note=f"scrolled {action.args['direction']}")

    async def _do_wait(self, action: Action) -> ExecOutcome:
        seconds = float(action.args["seconds"])  # bounded by parse_action
        await self._page.wait_for_timeout(seconds * 1000)
        return ExecOutcome(ok=True, note=f"waited {seconds:g}s")

    async def _do_fill(self, action: Action) -> ExecOutcome:
        element = self.resolve(str(action.args["element_id"]))
        if element.attributes.get("type", "").lower() == "password":
            return ExecOutcome(
                ok=False, note="refused: password fields are never filled"
            )
        value = str(action.args["value"])
        locator = await self._locator(element)
        await locator.fill(value, timeout=_ACTION_TIMEOUT_MS)
        read_back = await locator.input_value(timeout=_ACTION_TIMEOUT_MS)
        if read_back != value:
            return ExecOutcome(ok=False, note="read-back mismatch after fill")
        return ExecOutcome(ok=True, note=f"filled {element.label or element.tag}")

    async def _do_select(self, action: Action) -> ExecOutcome:
        element = self.resolve(str(action.args["element_id"]))
        option = str(action.args["option"])
        locator = await self._locator(element)
        selected = await locator.select_option(
            label=option, timeout=_ACTION_TIMEOUT_MS
        )
        if not selected:
            return ExecOutcome(ok=False, note=f"option {option!r} not selected")
        return ExecOutcome(ok=True, note=f"selected {option!r}")

    async def _do_check(self, action: Action) -> ExecOutcome:
        element = self.resolve(str(action.args["element_id"]))
        locator = await self._locator(element)
        await locator.check(timeout=_ACTION_TIMEOUT_MS)
        if not await locator.is_checked():
            return ExecOutcome(ok=False, note="checkbox did not read back checked")
        return ExecOutcome(ok=True, note=f"checked {element.label or element.tag}")

    async def _do_upload_artifact(self, action: Action) -> ExecOutcome:
        element = self.resolve(str(action.args["element_id"]))
        artifact_id = str(action.args["artifact_id"])
        artifact = next(
            (a for a in self._request.artifacts if a.artifact_id == artifact_id),
            None,
        )
        if artifact is None:
            return ExecOutcome(
                ok=False, note="refused: not a user-approved artifact"
            )
        if element.attributes.get("type", "").lower() != "file":
            return ExecOutcome(ok=False, note="target is not a file input")
        locator = await self._locator(element)
        await locator.set_input_files(artifact.path, timeout=_ACTION_TIMEOUT_MS)
        read_back = await locator.evaluate("el => el.files.length")
        if not read_back:
            return ExecOutcome(ok=False, note="upload did not register a file")
        return ExecOutcome(ok=True, note=f"uploaded {artifact.label}")
