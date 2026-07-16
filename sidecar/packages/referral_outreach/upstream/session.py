# voyager_py/session.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Adapted from OpenOutreach `linkedin/browser/{session,login,nav}.py` @ a7a9101.
# Changes for the finds-you-jobs fork:
#   - Django removed: no LinkedInProfile model, no DB-persisted cookies. The
#     session loads a Playwright storage-state JSON (cookies) from a path the
#     host owns (the user logs in once in a headed browser and saves it).
#   - Upstream third-party deps dropped: `playwright_stealth` (optional import,
#     applied if present) and `termcolor` (plain logging). No behavioural IP lost.
#   - The FREEMIUM promotional-action and auto-newsletter hooks that upstream
#     ran on session start are intentionally NOT forked (they send from the
#     user's account under remote control — incompatible with finds-you-jobs's
#     no-telemetry / no-middleman vision). See README.md § "What we did NOT take".
"""Standalone LinkedIn browser session: launch Chromium, load saved cookies,
navigate at a human pace. Chromium is fetched on first use, never bundled, and
torn down after each run (NFR-MEM-02)."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .errors import BrowserUnresponsiveError, SkipProfile
from .secure_store import UnreadableStateFile, load_state_file, save_state_file

logger = logging.getLogger("voyager_py.session")

LINKEDIN_FEED_URL = "https://www.linkedin.com/feed/"
LINKEDIN_LOGIN_URL = "https://www.linkedin.com/login"
_AUTH_COOKIE_NAME = "li_at"

# Page-load jitter between actions (upstream conf.MIN_DELAY / MAX_DELAY).
MIN_DELAY = 5
MAX_DELAY = 8
BROWSER_DEFAULT_TIMEOUT_MS = 30_000
BROWSER_NAV_TIMEOUT_MS = 10_000

COMPLY_LOCATORS = [
    lambda p: p.locator("button#content__button--primary--muted"),
    lambda p: p.get_by_role("button", name="Agree to comply", exact=True),
    lambda p: p.locator("button.content__button--primary"),
]


def random_sleep(min_val: float, max_val: float) -> None:
    delay = random.uniform(min_val, max_val)
    logger.debug("Pause: %.2fs", delay)
    time.sleep(delay)


_DEBUG_README = (
    "# LinkedIn action-failure captures\n\n"
    "Each subdirectory is one failed LinkedIn action (a selector/probe miss, e.g. a\n"
    "Connect affordance the current selector chain could not find). It holds a\n"
    "`page.png` screenshot and a `page.html` dump of the profile at the moment of\n"
    "failure, so a stale selector can be diagnosed and re-worked WITHOUT re-running\n"
    "against live LinkedIn.\n\n"
    "**These files are LOCAL-ONLY and may contain personal data** (the target's\n"
    "profile, and your own logged-in chrome). Nothing here is ever uploaded. Delete\n"
    "this directory any time; it is purely a debugging aid.\n"
)


def capture_failure(session: AccountSession, step: str) -> str:
    """Save a screenshot + page HTML for a failed action to
    `<storage-state-dir>/debug/<timestamp>-<step>/` and return that path.

    The maintainer asked for this: on any selector/skip failure we want a local
    artifact to diagnose a stale selector from, never a re-run against live
    LinkedIn. Best-effort — never raises (a capture failure must not mask the
    real error); returns "" when nothing could be written.
    """
    page = session.page
    if page is None:
        return ""
    try:
        base = (
            session.storage_state_path.parent / "debug"
            if session.storage_state_path is not None
            else Path.cwd() / "linkedin-debug"
        )
        base.mkdir(parents=True, exist_ok=True)
        readme = base / "README.md"
        if not readme.exists():
            readme.write_text(_DEBUG_README, encoding="utf-8")
        stamp = time.strftime("%Y%m%dT%H%M%S")
        safe_step = "".join(c if c.isalnum() or c in "-_" else "-" for c in step)[:40]
        dest = base / f"{stamp}-{safe_step}"
        dest.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(dest / "page.png"), full_page=True)
        except Exception as e:  # noqa: BLE001 — a screenshot miss must not mask the error
            logger.debug("failure screenshot failed: %s", e)
        try:
            (dest / "page.html").write_text(page.content(), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.debug("failure html dump failed: %s", e)
        logger.info("saved action-failure capture → %s", dest)
        return str(dest)
    except Exception as e:  # noqa: BLE001 — capture is a debugging aid, never fatal
        logger.debug("capture_failure failed: %s", e)
        return ""


def _maybe_apply_stealth(context) -> None:
    """Apply playwright-stealth if the optional dep is installed; skip otherwise.
    Upstream always applied it — we keep it optional to avoid a hard dep."""
    try:
        from playwright_stealth import Stealth  # type: ignore
    except ImportError:
        logger.debug("playwright_stealth not installed — continuing without it")
        return
    try:
        Stealth().apply_stealth_sync(context)
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("stealth apply failed: %s", e)


# Chromium launch hygiene (2026-07-12 — the maintainer hit Google reCAPTCHA
# walls from the automation window): Playwright's defaults advertise automation
# (`--enable-automation` → navigator.webdriver, infobar, throwaway fingerprint).
# We (a) strip that flag and disable the AutomationControlled blink feature, and
# (b) prefer the user's INSTALLED Chrome binary (channel="chrome") over the
# bundled Chromium — a real, auto-updating Chrome build is a materially more
# trusted fingerprint. The dedicated persistent profile is kept on purpose: it
# ages trust run-over-run, while sharing the user's live default profile is a
# non-starter (Chrome locks a running profile, and automation would be driving
# the user's whole logged-in life). Falls back to bundled Chromium when no
# Chrome install exists.
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled", "--no-first-run"]
_IGNORE_DEFAULT_ARGS = ["--enable-automation"]


def _launch_persistent(playwright, user_data_dir: str, *, headless: bool):
    kwargs = {
        "headless": headless,
        "args": _LAUNCH_ARGS,
        "ignore_default_args": _IGNORE_DEFAULT_ARGS,
    }
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir, channel="chrome", **kwargs
        )
    except Exception as e:  # noqa: BLE001 — no installed Chrome → bundled build
        logger.info("installed Chrome unavailable (%s) — using bundled Chromium", e)
        return playwright.chromium.launch_persistent_context(user_data_dir, **kwargs)


def _launch_browser(playwright, *, headless: bool):
    kwargs = {
        "headless": headless,
        "args": _LAUNCH_ARGS,
        "ignore_default_args": _IGNORE_DEFAULT_ARGS,
    }
    try:
        return playwright.chromium.launch(channel="chrome", **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.info("installed Chrome unavailable (%s) — using bundled Chromium", e)
        return playwright.chromium.launch(**kwargs)


def dismiss_comply_gate(page, timeout_ms: int = 5000) -> bool:
    """Click LinkedIn's 'Agree to comply' interstitial if present."""
    for factory in COMPLY_LOCATORS:
        locator = factory(page).first
        try:
            locator.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        logger.info("Dismissing 'Agree to comply' interstitial")
        locator.click()
        return True
    return False


def _goto_feed(page) -> None:
    """Navigate to the LinkedIn feed robustly at session start.

    The feed is a heavy SPA whose ``load`` event (all sub-resources: images,
    tracking beacons, lazy widgets) frequently never fires inside the 30s nav
    timeout, so a plain ``goto`` (which defaults to ``wait_until="load"``) times
    out with ``Page.goto: Timeout 30000ms exceeded ... waiting until "load"``.
    We instead wait only for ``domcontentloaded`` — the DOM (and the auth-gated
    chrome we actually need) is present by then — then dismiss the comply gate
    and give the load event a best-effort chance without letting it hard-fail a
    session that is otherwise ready.
    """
    page.goto(LINKEDIN_FEED_URL, wait_until="domcontentloaded")
    dismiss_comply_gate(page)
    try:
        page.wait_for_load_state("load", timeout=10_000)
    except PlaywrightTimeoutError:
        logger.debug("feed 'load' state did not settle in 10s — proceeding on domcontentloaded")


class AccountSession:
    """One LinkedIn browser session backed by a saved storage-state file.

    `storage_state_path` points at a Playwright storage-state JSON (cookies)
    that the host captured once via a headed login. `headed` shows the browser
    (for the maintainer's tiny-volume live dogfood); the default is headless.
    """

    def __init__(
        self,
        storage_state_path: str | Path | None = None,
        headed: bool = False,
        user_data_dir: str | Path | None = None,
    ) -> None:
        self.storage_state_path = Path(storage_state_path) if storage_state_path else None
        self.headed = headed
        # When set, the session runs on a PERSISTENT Chromium profile at this
        # dir (cookies live on disk, survive close, and are the same profile the
        # user can reopen + log out of to end the app's session). The JSON
        # storage-state is still exported for the no-browser validate path.
        self.user_data_dir = Path(user_data_dir) if user_data_dir else None
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    # --- lifecycle ---
    def _load_storage_state(self) -> dict | None:
        if not self.storage_state_path:
            return None
        # Sealed (FYJ_SESSION_KEY) or legacy plaintext — NFR-SEC-01.
        return load_state_file(self.storage_state_path)

    def _start_persistent(self) -> None:
        """Launch on a persistent user-data-dir profile (the default path when
        `user_data_dir` is set). Seeds cookies from the legacy storage-state JSON
        on first run (migration), so an existing saved session isn't lost."""
        assert self.user_data_dir is not None
        first_run = not self.user_data_dir.exists() or not any(self.user_data_dir.iterdir())
        self.user_data_dir.mkdir(parents=True, exist_ok=True)
        self.playwright = sync_playwright().start()
        self.context = _launch_persistent(
            self.playwright, str(self.user_data_dir), headless=not self.headed
        )
        self.browser = None  # persistent context owns its own browser
        self.context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        _maybe_apply_stealth(self.context)
        # Migration: seed cookies from the legacy JSON into a fresh profile.
        seeded = False
        if first_run:
            legacy = self._load_storage_state()
            cookies = (legacy or {}).get("cookies") if legacy else None
            if cookies:
                self.context.add_cookies(cookies)
                seeded = True
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        has_cookie = seeded or any(
            c.get("name") == _AUTH_COOKIE_NAME for c in self.context.cookies()
        )
        if not has_cookie:
            raise SkipProfile(
                "no saved LinkedIn session in the profile — the user must log in "
                "once (headed); voyager_py never handles the password"
            )
        _goto_feed(self.page)
        logger.info("voyager session ready (persistent profile)")

    def start(self) -> None:
        """Launch Chromium and restore the saved session."""
        if self.user_data_dir is not None:
            self._start_persistent()
            return
        storage_state = self._load_storage_state()
        self.playwright = sync_playwright().start()
        self.browser = _launch_browser(self.playwright, headless=not self.headed)
        self.context = self.browser.new_context(storage_state=storage_state)
        self.context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        self.context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
        _maybe_apply_stealth(self.context)
        self.page = self.context.new_page()

        if storage_state is None:
            raise SkipProfile(
                "no saved LinkedIn session — the user must log in once (headed) and "
                "save a Playwright storage-state file; voyager_py never handles the password"
            )
        _goto_feed(self.page)
        logger.info("voyager session ready")

    def ensure_browser(self) -> None:
        if not self.page or self.page.is_closed():
            self.start()

    def wait(self, min_delay: float = MIN_DELAY, max_delay: float = MAX_DELAY) -> None:
        random_sleep(min_delay, max_delay)
        if self.page:
            self.page.wait_for_load_state("domcontentloaded")

    def has_auth_cookie(self) -> bool:
        state = self._load_storage_state()
        if not state:
            return False
        return any(c.get("name") == _AUTH_COOKIE_NAME for c in state.get("cookies", []))

    def save_storage_state(self) -> None:
        """Persist refreshed cookies back to the storage-state file (sealed when
        the host passed FYJ_SESSION_KEY — NFR-SEC-01)."""
        if self.context and self.storage_state_path:
            save_state_file(self.storage_state_path, self.context.storage_state())

    def close(self) -> None:
        for closer in (
            lambda: self.context and self.context.close(),
            lambda: self.browser and self.browser.close(),
            lambda: self.playwright and self.playwright.stop(),
        ):
            try:
                closer()
            except Exception as e:  # noqa: BLE001 - teardown must never mask the real error
                logger.debug("teardown step raised: %s", e)
        self.page = self.context = self.browser = self.playwright = None
        logger.info("voyager session closed")


def goto_page(session: AccountSession, action, expected_url_pattern: str,
              timeout: int = BROWSER_NAV_TIMEOUT_MS, error_message: str = "") -> None:
    """Adapted from nav.goto_page: run an action, verify the resulting URL."""
    from urllib.parse import unquote

    page = session.page
    action()
    if not page:
        return
    try:
        page.wait_for_url(lambda url: expected_url_pattern in unquote(url), timeout=timeout)
    except PlaywrightTimeoutError:
        pass
    session.wait()
    current = unquote(page.url)
    if expected_url_pattern not in current:
        if "/404" in current:
            raise SkipProfile(f"Profile returned 404 → {current}")
        raise RuntimeError(f"{error_message} → expected '{expected_url_pattern}' | got '{current}'")


def human_type(locator, text: str, min_delay: int | None = None, max_delay: int | None = None):
    """Type with randomized per-keystroke delay (upstream nav.human_type)."""
    from .pacing import HUMAN_TYPE_MAX_DELAY_MS, HUMAN_TYPE_MIN_DELAY_MS

    lo = HUMAN_TYPE_MIN_DELAY_MS if min_delay is None else min_delay
    hi = HUMAN_TYPE_MAX_DELAY_MS if max_delay is None else max_delay
    locator.type(text, delay=random.randint(lo, hi))


def raise_if_unresponsive(fired: bool, label: str, deadline_s: float) -> None:
    if fired:
        raise BrowserUnresponsiveError(f"Browser unresponsive after {int(deadline_s)}s on {label}")


# ---------------------------------------------------------------------------
# Interactive login capture (finds-you-jobs fork — new GPL code).
#
# Divergence from upstream: OpenOutreach's `playwright_login` *types the user's
# stored username/password into the login form* (linkedin/browser/login.py). The
# finds-you-jobs fork NEVER handles the password — instead we open a headed browser
# at LinkedIn's own login page, let the human complete login (incl. 2FA/checkpoint)
# themselves, and detect the auth cookie (`li_at`, the same signal upstream keys
# its saved session on) to know login succeeded, then persist the Playwright
# storage-state. No credential ever passes through this process.
# ---------------------------------------------------------------------------


def _li_at_cookie(cookies: list[dict]) -> dict | None:
    for c in cookies:
        if c.get("name") == _AUTH_COOKIE_NAME:
            return c
    return None


def _best_effort_connected_as(page) -> str:
    """The logged-in member's display name, best-effort, from the feed chrome.
    Never raises; empty string when it can't be read (e.g. a non-LinkedIn
    fixture page). Reads the DOM only — no Voyager/API call, no extra traffic."""
    for getter in (
        lambda: page.locator(".global-nav__me-photo").first.get_attribute("alt", timeout=1500),
        lambda: page.locator("img.global-nav__me-photo").first.get_attribute("alt", timeout=1500),
    ):
        try:
            val = getter()
        except Exception:  # noqa: BLE001 — best effort only
            continue
        if val:
            return val.strip()
    return ""


def capture_login(
    storage_state_path: str | Path,
    *,
    login_url: str = LINKEDIN_LOGIN_URL,
    timeout_s: float = 300.0,
    poll_interval_s: float = 1.5,
    cancelled: Callable[[], bool] | None = None,
    headed: bool = True,
    user_data_dir: str | Path | None = None,
) -> dict:
    """Open a **headed** browser at `login_url`, wait for the user to finish
    logging in (the `li_at` auth cookie appears), then persist the Playwright
    storage-state to `storage_state_path`. Returns a plain metadata dict.

    When `user_data_dir` is set the login runs on a **persistent** Chromium
    profile at that dir — the cookies stay on disk, so the same profile is
    reused for every later action and the user can reopen it to log out (which
    ends the app's session too). The JSON storage-state is still exported for
    the no-browser validate path.

    `cancelled()` (optional) is polled between cookie checks; returning True
    aborts the wait and closes the browser (the host's Cancel button). `login_url`
    is overridable so the plumbing can be verified against a LOCAL fixture that
    sets a `li_at` cookie — **no linkedin.com traffic** in tests.

    The password is never handled here: the human logs in in the visible browser.
    """
    out_path = Path(storage_state_path)
    playwright = sync_playwright().start()
    browser = None
    if user_data_dir is not None:
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
        context = _launch_persistent(playwright, str(user_data_dir), headless=not headed)
        page = context.pages[0] if context.pages else context.new_page()
    else:
        browser = _launch_browser(playwright, headless=not headed)
        context = browser.new_context()
        page = context.new_page()
    context.set_default_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(BROWSER_DEFAULT_TIMEOUT_MS)
    _maybe_apply_stealth(context)
    try:
        try:
            page.goto(login_url)
        except PlaywrightTimeoutError:
            logger.warning("login page load timed out; continuing to poll for the cookie")

        deadline = time.monotonic() + timeout_s
        cookie: dict | None = None
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled():
                raise SkipProfile("login cancelled by the user before the session cookie appeared")
            cookie = _li_at_cookie(context.cookies())
            if cookie is not None:
                break
            time.sleep(poll_interval_s)

        if cookie is None:
            raise SkipProfile(
                f"no LinkedIn session cookie ({_AUTH_COOKIE_NAME}) detected within {int(timeout_s)}s "
                "— login was not completed"
            )

        # Give any post-login redirect a moment to settle so the saved state is
        # complete, then read the member name best-effort (DOM only).
        try:
            page.wait_for_load_state("domcontentloaded", timeout=BROWSER_NAV_TIMEOUT_MS)
        except PlaywrightTimeoutError:
            pass
        connected_as = _best_effort_connected_as(page)

        # Sealed when the host passed FYJ_SESSION_KEY (NFR-SEC-01); the linger
        # flow this used to carry was retired 2026-07-09 — the persistent
        # profile keeps the session reusable without holding a window open.
        state = context.storage_state()
        save_state_file(out_path, state)
        expires = cookie.get("expires", -1)
        return {
            "connected": True,
            "connected_as": connected_as,
            "li_at_expires": expires if expires and expires > 0 else None,
            "cookie_count": len(state.get("cookies", [])),
            "storage_state_path": str(out_path),
        }
    finally:
        # `browser` is None on the persistent-context path (the context owns it).
        closers = [context.close]
        if browser is not None:
            closers.append(browser.close)
        closers.append(playwright.stop)
        for closer in closers:
            try:
                closer()
            except Exception as e:  # noqa: BLE001 — teardown must never mask the real error
                logger.debug("login teardown step raised: %s", e)


def inspect_storage_state(storage_state_path: str | Path, *, now: float | None = None) -> dict:
    """LOCAL-only session validity — read the storage-state file and report the
    `li_at` cookie's presence + expiry. **No browser, no network** (the host's
    'validate without hitting LinkedIn' path). `now` is injectable for tests.

    Reads sealed (FYJ_SESSION_KEY) or legacy plaintext files. A corrupt file
    reads as absent (tolerant, as before); a sealed file with a missing/wrong
    key raises verbatim — that is a host misconfiguration, never silent."""
    now = time.time() if now is None else now
    path = Path(storage_state_path)
    try:
        state = load_state_file(path)
    except UnreadableStateFile:
        state = None
    if state is None:
        return {"present": False, "has_auth_cookie": False, "expired": False, "li_at_expires": None}
    cookie = _li_at_cookie(state.get("cookies", []))
    if cookie is None:
        return {"present": True, "has_auth_cookie": False, "expired": False, "li_at_expires": None}
    expires = cookie.get("expires", -1)
    expires_epoch = expires if expires and expires > 0 else None
    expired = expires_epoch is not None and expires_epoch < now
    return {
        "present": True,
        "has_auth_cookie": True,
        "expired": expired,
        "li_at_expires": expires_epoch,
    }
