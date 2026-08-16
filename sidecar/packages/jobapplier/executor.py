# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
"""Action executor (docs/internal/archived/applier-as-built.md section 4.2/section 4.3/section 5.3).

The executor is the enforcement layer: it re-checks everything the prompt
promises, so a prompt-injected or confused model still cannot step outside
the contract. Independently of what the model asks for:

- an action must reference an element id from the CURRENT observation —
  stale ids raise ``StaleElementError`` instead of guessing (section 4.1);
- ``navigate`` obeys a scheme/host policy — private/loopback redirect targets
  are rejected (section 4.3);
- only user-approved artifacts can be uploaded, chosen by artifact_id — the
  model never sees or supplies a filesystem path (section 5.3);
- password/TOTP inputs cannot be filled — the product holds no site
  credentials (section 4.3);
- every mutating action is verified by read-back where the control supports
  it, and the outcome is reported honestly (section 4.2).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from .actions import Action
from .observe import Observation, ObservedElement
from .types import ApplyRequest, DisallowedActionError, StaleElementError
from .upstream.constants import SKYVERN_ID_ATTR

_NAV_TIMEOUT_MS = 30_000
_ACTION_TIMEOUT_MS = 10_000
_OPTION_WAIT_MS = 3_000
_SETTLE_MS = 150

# Options a custom dropdown/typeahead reveals: ARIA listbox options (Ashby)
# or menu items (BambooHR's fab-Select renders a [role=menu] of menuitems).
_OPTION_SELECTOR = "[role=option], [role=menuitem]"

# A click on a rich widget must observably change SOMETHING; this fingerprint
# is compared before/after so a swallowed click (SPA re-render races, an
# overlay eating the event) is caught instead of blindly reported ok. It reads
# the element's own state attributes, its parent's inputs (Ashby yes/no pairs
# keep their state in a hidden sibling checkbox), and the open-overlay count.
_CLICK_STATE_JS = """el => {
  const own = [el.className || '', el.getAttribute('aria-pressed'),
               el.getAttribute('aria-checked'), el.getAttribute('aria-selected'),
               el.getAttribute('aria-expanded')].join('|');
  const parent = el.parentElement;
  const kin = parent
    ? Array.from(parent.querySelectorAll('input'))
        .map(i => `${i.name}=${i.checked}`).join(';') + '|' + (parent.className || '')
    : '';
  const overlays = document.querySelectorAll('[role=listbox],[role=menu],[role=dialog]').length;
  return own + '::' + kin + '::' + overlays;
}"""


def _norm_option(text: str) -> str:
    """Case, punctuation (typographic apostrophes included) and whitespace
    never distinguish menu options; compare on the alphanumeric skeleton."""
    return re.sub(r"\s+", " ", re.sub(r"[^0-9a-z ]+", "", text.lower())).strip()


def _match_option(texts: list[str], wanted: str) -> int | None:
    """Index of the option matching ``wanted``: exact (normalized), else a
    UNIQUE prefix match, else a UNIQUE containment either way. The prefix tier
    exists because containment alone ties "India" between "India" and "British
    Indian Ocean Territory". Never a blind first-option guess."""
    want = _norm_option(wanted)
    if not want:
        return None
    cleaned = [_norm_option(t) for t in texts]
    for i, t in enumerate(cleaned):
        if t == want:
            return i
    starts = [i for i, t in enumerate(cleaned) if t and t.startswith(want)]
    if len(starts) == 1:
        return starts[0]
    hits = [i for i, t in enumerate(cleaned) if t and (want in t or t in want)]
    if len(hits) == 1:
        return hits[0]
    return _match_bucket(texts, wanted)


def _match_bucket(texts: list[str], wanted: str) -> int | None:
    """When ``wanted`` is a bare number and the options are numeric ranges
    ("0-5 years" / "6-10 years" / "10+ years"), the UNIQUE range containing it.
    Ambiguous boundaries (10 in both "6-10" and "10+") return None rather than
    guess. This is derivation (9 years of experience is in the 6-10 bucket),
    the only kind of inference the fill contract permits."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", wanted)
    if not m:
        return None
    n = float(m.group(1))
    matches: list[int] = []
    for i, t in enumerate(texts):
        lo_hi = re.search(r"(\d+)\s*(?:-|to|–)\s*(\d+)", t)
        plus = re.search(r"(\d+)\s*\+", t)
        if lo_hi and int(lo_hi.group(1)) <= n <= int(lo_hi.group(2)):
            matches.append(i)
        elif plus and n >= int(plus.group(1)):
            matches.append(i)
    return matches[0] if len(matches) == 1 else None


@dataclass(frozen=True)
class ExecOutcome:
    """What actually happened, with read-back evidence where available."""

    ok: bool
    note: str = ""


class UrlPolicy:
    """Scheme/host policy for navigate + redirect checks (section 4.3).

    Literal IPs only, on purpose: Playwright resolves inside the browser, so a
    resolve-then-navigate check here would be a second lookup proving nothing.
    The fetch layer's guard (`modules/_shared/url_guard.url_refusal`) DOES
    resolve; the two are pinned against one shared case table in
    `sidecar/tests/modules/shared/test_url_guard_corpus.py`, which is where that
    divergence is recorded (duplication audit D-M10).

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
        observation are dead from this moment (section 4.1)."""
        self._observation = obs

    async def execute(self, action: Action) -> ExecOutcome:
        handler = getattr(self, f"_do_{action.tool}", None)
        if handler is None:  # finish/report_blocked terminate in the loop
            raise DisallowedActionError(f"{action.tool} is not executable")
        try:
            return await handler(action)
        except PlaywrightError as exc:
            # A page-level failure of ONE action (timeout on a non-fillable
            # element, a detached node mid-click) is a failed action the loop
            # can recover from — not a dead browser. A truly closed page
            # surfaces again on the next observe and lands as INTERRUPTED.
            if self._page.is_closed():
                raise
            reason = str(exc).splitlines()[0][:200]
            return ExecOutcome(ok=False, note=f"{action.tool} failed: {reason}")

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
        order. Not found anywhere → the observation is stale (section 4.1)."""
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
        name = element.label or element.text or element.tag
        try:
            before = await locator.evaluate(_CLICK_STATE_JS)
        except PlaywrightError:
            before = None
        await locator.click(timeout=_ACTION_TIMEOUT_MS)
        if before is None:
            return ExecOutcome(ok=True, note=f"clicked {name}")
        await self._page.wait_for_timeout(_SETTLE_MS)
        try:
            after = await locator.evaluate(_CLICK_STATE_JS)
        except PlaywrightError:
            # The click consumed the element (navigation or a re-render that
            # replaced the node): it plainly had an effect.
            return ExecOutcome(ok=True, note=f"clicked {name}")
        if after == before:
            # A SPA re-render or an overlay dismissal can swallow the first
            # click; the state comparison proves nothing changed, so one
            # retry cannot toggle an already-applied answer back off.
            await self._page.wait_for_timeout(2 * _SETTLE_MS)
            await locator.click(timeout=_ACTION_TIMEOUT_MS)
            await self._page.wait_for_timeout(_SETTLE_MS)
            try:
                after = await locator.evaluate(_CLICK_STATE_JS)
            except PlaywrightError:
                return ExecOutcome(ok=True, note=f"clicked {name}")
            if after == before:
                return ExecOutcome(
                    ok=True,
                    note=f"clicked {name} (no observable state change)",
                )
        return ExecOutcome(ok=True, note=f"clicked {name}")

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
        name = element.label or element.tag
        role = (element.role or str(element.attributes.get("role") or "")).lower()
        aria_auto = str(element.attributes.get("aria-autocomplete") or "").lower()
        if role == "combobox" or aria_auto in ("list", "both"):
            return await self._commit_combobox(locator, value, name)
        return ExecOutcome(ok=True, note=f"filled {name}")

    async def _visible_options(self):
        """The VISIBLE menu options on the page, or (None, []) when none
        appear in time. Visibility filtering matters: a closed menu elsewhere
        on the page also carries option/menuitem nodes."""
        options = self._page.locator(_OPTION_SELECTOR).locator("visible=true")
        try:
            await options.first.wait_for(state="visible", timeout=_OPTION_WAIT_MS)
        except PlaywrightError:
            return None, []
        return options, await options.all_inner_texts()

    async def _commit_combobox(
        self, locator, value: str, name: str
    ) -> ExecOutcome:
        """A typeahead combobox discards uncommitted text on blur (Ashby's
        location field): typing alone verifies and then silently vanishes.
        Commit by clicking the suggestion matching the typed value, and report
        honestly when the suggestion list cannot match (a US-state list given a
        non-US value)."""
        options, texts = await self._visible_options()
        pick = _match_option(texts, value) if options is not None else None
        if pick is None:
            # Some typeaheads (Greenhouse's geocoded location) only match on a
            # short query: the full "City, State, Country" string returns
            # nothing while its prefix surfaces the exact option. Retype a
            # prefix and match the suggestions against the FULL wanted value.
            await locator.fill("", timeout=_ACTION_TIMEOUT_MS)
            await locator.press_sequentially(value[:5], delay=40)
            options, texts = await self._visible_options()
            pick = _match_option(texts, value) if options is not None else None
        if options is None:
            return ExecOutcome(
                ok=True,
                note=f"filled {name} (combobox: no suggestions; may not persist)",
            )
        if pick is None:
            return ExecOutcome(
                ok=False,
                note=f"combobox: no suggestion matches {value!r} "
                f"({len(texts)} offered); value will not persist",
            )
        picked = texts[pick].strip()
        await options.nth(pick).click(timeout=_ACTION_TIMEOUT_MS)
        # The widget applies the pick asynchronously; read back after a beat.
        await self._page.wait_for_timeout(_SETTLE_MS)
        committed = await locator.input_value(timeout=_ACTION_TIMEOUT_MS)
        return ExecOutcome(
            ok=True,
            note=f"filled {name} (committed suggestion {(committed or picked)!r})",
        )

    async def _do_select(self, action: Action) -> ExecOutcome:
        element = self.resolve(str(action.args["element_id"]))
        option = str(action.args["option"])
        locator = await self._locator(element)
        if element.tag.lower() == "select":
            selected = await locator.select_option(
                label=option, timeout=_ACTION_TIMEOUT_MS
            )
            if not selected:
                return ExecOutcome(ok=False, note=f"option {option!r} not selected")
            return ExecOutcome(ok=True, note=f"selected {option!r}")
        # An editable typeahead (Greenhouse's location/react-select renders an
        # input): options only populate on typing, so a bare open-click shows
        # nothing. Route through the combobox commit, which types and retries
        # on a prefix.
        if element.tag.lower() in ("input", "textarea"):
            await locator.fill(option, timeout=_ACTION_TIMEOUT_MS)
            return await self._commit_combobox(
                locator, option, element.label or element.tag
            )
        # A custom dropdown: a button (BambooHR's fab-Select) or combobox
        # toggle that opens an ARIA menu/listbox. Open it, pick the matching
        # option, and read the toggle back.
        await locator.click(timeout=_ACTION_TIMEOUT_MS)
        options, texts = await self._visible_options()
        if options is None:
            return ExecOutcome(
                ok=False, note=f"no option menu appeared under {element.label or element.tag}"
            )
        pick = _match_option(texts, option)
        if pick is None:
            # Close the menu so it cannot cover later targets.
            await self._page.keyboard.press("Escape")
            return ExecOutcome(
                ok=False,
                note=f"option {option!r} not in the menu ({len(texts)} offered)",
            )
        await options.nth(pick).click(timeout=_ACTION_TIMEOUT_MS)
        return ExecOutcome(ok=True, note=f"selected {texts[pick].strip()!r}")

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
        locator = await self._locator(element)

        # A plain <input type=file>: set it directly and read back its count.
        if element.attributes.get("type", "").lower() == "file":
            await locator.set_input_files(artifact.path, timeout=_ACTION_TIMEOUT_MS)
            if not await locator.evaluate("el => el.files.length"):
                return ExecOutcome(ok=False, note="upload did not register a file")
            await self._settle_after_upload()
            return ExecOutcome(ok=True, note=f"uploaded {artifact.label}")

        # A drag-drop zone: the styled control wraps a hidden <input type=file>,
        # the shape that outnumbers plain file inputs 20 to 3 on the corpus.
        # Prefer that nested input and read back its file count.
        nested = locator.locator("input[type=file]").first
        if await nested.count():
            await nested.set_input_files(artifact.path, timeout=_ACTION_TIMEOUT_MS)
            if await self._upload_registered(nested):
                await self._settle_after_upload()
                return ExecOutcome(ok=True, note=f"uploaded {artifact.label}")

        # An Attach BUTTON with the real <input type=file> as a sibling
        # (Greenhouse): walk up a few ancestors and take the input only when
        # it is unambiguous — 2 file inputs in scope could be the resume and
        # the cover letter, and guessing between them is worse than failing.
        for up in ("..", "../..", "../../.."):
            scope = locator.locator(up).locator("input[type=file]")
            if await scope.count() == 1:
                await scope.first.set_input_files(
                    artifact.path, timeout=_ACTION_TIMEOUT_MS
                )
                if await self._upload_registered(scope.first):
                    await self._settle_after_upload()
                    return ExecOutcome(ok=True, note=f"uploaded {artifact.label}")
                break

        # No reachable input: some zones open a native file chooser on click.
        # Drive that — the chooser takes the path executor-side, so no
        # filesystem path is ever exposed (section 5.3).
        try:
            async with self._page.expect_file_chooser(
                timeout=_ACTION_TIMEOUT_MS
            ) as chooser_info:
                await locator.click(timeout=_ACTION_TIMEOUT_MS)
            chooser = await chooser_info.value
            await chooser.set_files(artifact.path)
            await self._settle_after_upload()
            return ExecOutcome(ok=True, note=f"uploaded {artifact.label} via chooser")
        except PlaywrightError:
            pass
        # The click may have opened a source MENU instead (Jobvite's Select →
        # My Computer / Dropbox / Drive). Pick the local-file item — searching
        # every frame, since Jobvite renders its form inside an iframe — and
        # expect the chooser from IT.
        # The item may be an <a>, a menuitem, or (Jobvite) a <span role=button>
        # reading "File" inside a role=dialog. Match on a role=button too, held
        # to the local-file words so a submit/close control never qualifies.
        menu_sel = (
            _OPTION_SELECTOR
            + ", [role=menu] a, ul li a, [role=dialog] [role=button], [role=menu] [role=button]"
        )
        local_re = re.compile(r"\b(computer|device|browse|local|file|upload)\b", re.I)
        for frame in [self._page.main_frame, *self._page.frames]:
            local_item = (
                frame.locator(menu_sel)
                .locator("visible=true")
                .filter(has_text=local_re)
                .first
            )
            try:
                if not await local_item.count():
                    continue
                async with self._page.expect_file_chooser(
                    timeout=_ACTION_TIMEOUT_MS
                ) as chooser_info:
                    await local_item.click(timeout=_ACTION_TIMEOUT_MS)
                chooser = await chooser_info.value
                await chooser.set_files(artifact.path)
                await self._settle_after_upload()
                return ExecOutcome(
                    ok=True, note=f"uploaded {artifact.label} via source menu"
                )
            except PlaywrightError:
                continue
        return ExecOutcome(ok=False, note="no file input reachable from this control")

    async def _upload_registered(self, file_input) -> bool:
        """True when the file registered. An upload widget may CONSUME its
        input node the moment a file lands (Greenhouse swaps the attach block
        for an uploaded-state view), so a vanished node right after
        ``set_input_files`` succeeded reads as registered, not as failure."""
        try:
            return bool(
                await file_input.evaluate(
                    "el => el.files.length", timeout=2_000
                )
            )
        except PlaywrightError:
            return True

    async def _settle_after_upload(self) -> None:
        """An accepted resume commonly triggers a server-side parse whose
        completion re-renders the form (Ashby's autofill-from-resume). Acting
        during that re-render loses clicks and typed values, so wait for the
        network to settle, bounded — an SPA with a polling channel never goes
        fully idle."""
        try:
            await self._page.wait_for_load_state("networkidle", timeout=6_000)
        except PlaywrightError:
            pass
        await self._page.wait_for_timeout(400)
