# voyager_py/tests/test_voyager_csrf_freshness.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The csrf-token header must equal the JSESSIONID cookie the SAME voyager
fetch carries — the 2026-08-14 cold-boot 403 (`provenance.md`, "Live csrf-token
derivation"; evidence in `docs/internal/evidence/2026-08-15-voyager-403-spike/`).

`PlaywrightLinkedinAPI.__init__` snapshots the header from the jar at
construction, and `_fetch`'s origin assertion can NAVIGATE after that snapshot.
On a cold broker surface after a clean Chrome exit the jar carries no
JSESSIONID (session cookies are purged), so the snapshot is empty; the feed
load then mints a fresh JSESSIONID, and the fetch used to send the new cookie
with the stale header — voyager answers 403, which the profile path misreports
as ProfileInaccessibleError. The fix derives the header from the live jar at
fetch time (Python re-read + in-page document.cookie as the last word).

ZERO linkedin.com: the REAL client + a broker-backed `AccountSession` run over
Playwright route interception — the feed and voyager URLs are fulfilled
locally (the feed mints/rotates JSESSIONID via Set-Cookie; the voyager route
enforces header == minted cookie, 403 on mismatch, exactly the live contract),
and every other request is aborted before it can leave the machine.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from sidecar.packages.referral_outreach.upstream import session as session_mod  # noqa: E402
from sidecar.packages.referral_outreach.upstream.client import (  # noqa: E402
    PlaywrightLinkedinAPI,
)
from sidecar.packages.referral_outreach.upstream.session import AccountSession  # noqa: E402

FEED_URL = "https://www.linkedin.com/feed/"
VOYAGER_GLOB = "https://www.linkedin.com/voyager/api/**"
_PROFILE_TYPE = "com.linkedin.voyager.dash.identity.profile.Profile"


def _profile_response() -> dict:
    return {
        "data": {"*elements": ["urn:li:fsd_profile:jane"]},
        "included": [
            {
                "entityUrn": "urn:li:fsd_profile:jane",
                "$type": _PROFILE_TYPE,
                "$recipeTypes": [
                    "com.linkedin.voyager.dash.deco.identity.profile.FullProfileWithEntities"
                ],
                "publicIdentifier": "jane-doe",
                "firstName": "Jane",
                "lastName": "Doe",
                "headline": "Staff Engineer at Acme",
            }
        ],
    }


class _SurfaceSession:
    """The duck-typed handle a broker lane passes to its callable."""

    def __init__(self, page: Any, context: Any) -> None:
        self.page = page
        self.context = context


class _InlineSurface:
    """A minimal broker-surface stand-in: `run_on_lane` runs the callable
    inline on the calling thread (the tests own the only thread that may touch
    these Playwright objects) and hands back a settled Future, matching the
    real lane's `concurrent.futures.Future` contract incl. exception
    propagation through `.result()`."""

    def __init__(self, page: Any, context: Any) -> None:
        self._session = _SurfaceSession(page, context)

    def run_on_lane(self, fn: Callable[[Any], Any]) -> Future:
        fut: Future = Future()
        try:
            fut.set_result(fn(self._session))
        except BaseException as exc:  # noqa: BLE001 — the lane propagates verbatim
            fut.set_exception(exc)
        return fut


class _Fixture:
    """Routes: the feed mints a fresh quoted JSESSIONID per navigation; the
    voyager endpoint answers 200 only when the csrf-token header equals the
    latest minted (unquoted) value, else 403 — LinkedIn's csrf contract."""

    def __init__(self, context: Any) -> None:
        self.feed_hits = 0
        self.minted = ""
        self.seen_csrf: list[str] = []
        self.aborted: list[str] = []
        # Registration order matters: Playwright matches routes LAST-first, so
        # the abort-everything net goes down first and the two fulfilled URLs
        # take precedence over it. Nothing escapes to the wire either way.
        context.route("**/*", self._abort)
        context.route(FEED_URL, self._feed)
        context.route(VOYAGER_GLOB, self._voyager)

    def _abort(self, route: Any) -> None:
        self.aborted.append(route.request.url)
        route.abort()

    def _feed(self, route: Any) -> None:
        self.feed_hits += 1
        self.minted = f"ajax:80000000000000{self.feed_hits:02d}"
        route.fulfill(
            status=200,
            headers={
                "content-type": "text/html; charset=utf-8",
                # Quoted, as LinkedIn historically sets it — both derivation
                # layers must strip the quotes. Domain is set exactly as live
                # LinkedIn does (the broker profile's cookie DB records
                # host_key `.www.linkedin.com`), so a rotation REPLACES a
                # restored stale cookie instead of standing beside it.
                "set-cookie": (
                    f'JSESSIONID="{self.minted}"; '
                    "Domain=.www.linkedin.com; Path=/; Secure"
                ),
            },
            body="<html><body>fixture feed</body></html>",
        )

    def _voyager(self, route: Any) -> None:
        header = route.request.headers.get("csrf-token", "")
        self.seen_csrf.append(header)
        if header != self.minted:
            route.fulfill(status=403, body="CSRF check failed")
            return
        route.fulfill(
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(_profile_response()),
        )


@pytest.fixture()
def surface(monkeypatch: pytest.MonkeyPatch):
    # Realism knobs orthogonal to the mechanism under test: the comply-gate
    # probe spends 3 × 5 s of selector waits on a fixture page with no gate,
    # and the page-jitter waits 5-8 s per action.
    monkeypatch.setattr(
        session_mod, "dismiss_comply_gate", lambda page, timeout_ms=5000: False
    )
    monkeypatch.setattr(session_mod, "random_sleep", lambda *_a, **_k: None)
    pw = playwright_sync.sync_playwright().start()
    browser = None
    try:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context()
        fixture = _Fixture(context)
        page = context.new_page()  # a cold broker surface: about:blank
        yield _InlineSurface(page, context), context, page, fixture
    finally:
        if browser is not None:
            browser.close()
        pw.stop()


def _add_li_at(context: Any) -> None:
    context.add_cookies(
        [
            {
                "name": "li_at",
                "value": "AQE-fixture-auth",
                "domain": ".www.linkedin.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            }
        ]
    )


def _get_profile_as_the_send_path_does(session: AccountSession, sink: dict) -> dict:
    """The exact construction order `actions.send_dm`/`get_contact_sync_state`
    use: bind on the lane, THEN build the client (snapshotting the jar), THEN
    the first voyager call (whose origin assertion may navigate)."""

    def _act() -> dict:
        api = PlaywrightLinkedinAPI(session=session)
        sink["snapshot"] = api.headers["csrf-token"]
        parsed, _raw = api.get_profile(public_identifier="jane-doe")
        return parsed

    return session.run_browser(_act)


def test_cold_boot_with_no_jsessionid_sends_the_minted_token_then_stays_warm(surface):
    inline, context, page, fixture = surface
    # The measured cold-boot jar after a clean Chrome exit: li_at persisted,
    # JSESSIONID (a session cookie) purged.
    _add_li_at(context)
    session = AccountSession(surface_provider=lambda _slug: inline)

    sink: dict = {}
    parsed = _get_profile_as_the_send_path_does(session, sink)

    assert sink["snapshot"] == ""  # the construction snapshot really was empty
    assert fixture.feed_hits == 1  # the origin assertion spent one feed load
    assert fixture.seen_csrf == [fixture.minted]  # the LIVE value went out
    assert parsed["public_identifier"] == "jane-doe"

    # Warm repeat (the live retry that succeeded): a NEW client per action on
    # the settled jar, no extra navigation, still the minted value.
    sink2: dict = {}
    parsed2 = _get_profile_as_the_send_path_does(session, sink2)
    assert sink2["snapshot"] == fixture.minted
    assert fixture.feed_hits == 1
    assert fixture.seen_csrf[-1] == fixture.minted
    assert parsed2["public_identifier"] == "jane-doe"


def test_stale_snapshot_is_overridden_by_the_rotated_cookie(surface):
    inline, context, page, fixture = surface
    # The crash-restore variant: the jar still carries an OLD JSESSIONID, and
    # the feed load rotates it after the client snapshotted the stale value.
    _add_li_at(context)
    context.add_cookies(
        [
            {
                "name": "JSESSIONID",
                "value": '"ajax:0000stale0000"',
                "domain": ".www.linkedin.com",
                "path": "/",
                "secure": True,
            }
        ]
    )
    session = AccountSession(surface_provider=lambda _slug: inline)

    sink: dict = {}
    parsed = _get_profile_as_the_send_path_does(session, sink)

    assert sink["snapshot"] == "ajax:0000stale0000"  # quoted jar value, stripped
    assert fixture.minted != "ajax:0000stale0000"
    assert fixture.seen_csrf == [fixture.minted]  # rotation won over the snapshot
    assert parsed["public_identifier"] == "jane-doe"


def test_the_fixture_rejects_a_stale_header_with_403(surface):
    """The control arm: the fixture voyager endpoint really discriminates —
    a raw in-page fetch that pins the PRE-ROTATION header (what the pre-fix
    client did) gets the exact HTTP 403 the live failure showed."""
    inline, context, page, fixture = surface
    _add_li_at(context)
    page.goto(FEED_URL, wait_until="domcontentloaded")  # mints the cookie
    status = page.evaluate(
        """(stale) => fetch(
               "https://www.linkedin.com/voyager/api/identity/dash/profiles?q=memberIdentity",
               {headers: {"csrf-token": stale}, credentials: "include"}
           ).then(r => r.status)""",
        "ajax:0000stale0000",
    )
    assert status == 403
