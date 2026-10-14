# voyager_py/tests/test_broker_profile_reconciliation.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Phase 5 profile reconciliation, proven against a local fixture, never
linkedin.com.

A loopback page sets a persistent `li_at`. `capture_login` into the broker's
per-slug profile detects it and seals the storage state; a real headless
`BrowserSurface` on the same slug reads it back; and while 1 Chrome holds the
profile a second open is refused. Only real Chrome enforces that lock, so it's
skipped without the Chrome channel. Anchors: `profile_dir`, NFR-SEC-01."""

from __future__ import annotations

import asyncio
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from sidecar.app.browser import BrowserBroker, BrowserLaunchError  # noqa: E402
from sidecar.packages.referral_outreach.upstream.secure_store import (  # noqa: E402
    SESSION_KEY_ENV,
)
from sidecar.packages.referral_outreach.upstream.session import (  # noqa: E402
    SURFACE_SLUG,
    AccountSession,
    capture_login,
    inspect_storage_state,
)

# A de-headlessed Chrome UA, so the broker never spends a throwaway launch to
# resolve one (the pattern `test_broker.py` uses).
FAKE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

# A PERSISTENT li_at (max-age set), so Chromium flushes it to the profile's
# cookie store on close — exactly like the real long-lived li_at, and the reason
# a fresh persistent context on the same dir reads it back. A session cookie (no
# max-age) would live only in memory and never survive the profile reopen.
_FIXTURE_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>Fixture login</title></head><body>"
    "<h1>fixture login page</h1>"
    "<script>document.cookie = "
    "'li_at=FAKE_FIXTURE_TOKEN; path=/; max-age=31536000';</script>"
    "</body></html>"
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        body = _FIXTURE_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # type: ignore[override]  # silence the test log
        return


@pytest.fixture()
def fixture_login_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Handler))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address  # type: ignore[misc]
    try:
        yield f"http://{host}:{port}/login"
    finally:
        server.shutdown()
        server.server_close()


async def test_login_seals_into_broker_profile_and_headless_surface_reads_it_back(
    tmp_path: Path, fixture_login_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.fernet import Fernet

    # The host always runs the login with FYJ_SESSION_KEY set → sealed at rest.
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())

    # The broker owns the per-slug layout; resolve the login target THROUGH it,
    # exactly as the production path helpers do (`linkedin_profile_dir` /
    # `linkedin_storage_path` now derive `<data>/browser/<slug>/{profile,
    # storage_state.json}` from this same convention). The slug is a runtime
    # argument — the broker never learns the vendor.
    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=5.0,
    )
    profile_dir = broker.profile_dir(SURFACE_SLUG)
    storage_state = profile_dir.parent / "storage_state.json"

    # 1) The one-time (here: fixture) login writes the SEALED session INTO the
    # broker's per-slug profile dir. Off the loop: `capture_login` drives sync
    # Playwright, which may not run on a thread with a live asyncio loop.
    result = await asyncio.to_thread(
        capture_login,
        storage_state,
        login_url=fixture_login_url,
        timeout_s=20.0,
        poll_interval_s=0.25,
        headed=False,  # CI-friendly; the real one-time login is headed (maintainer)
        user_data_dir=profile_dir,
    )
    assert result["connected"] is True
    assert profile_dir.exists() and any(profile_dir.iterdir())  # profile populated

    # Sealed at rest (NFR-SEC-01): the artifact exists, is encrypted, and carries
    # no readable cookie name/value. And the no-browser validator reads it back.
    raw = storage_state.read_text(encoding="utf-8")
    assert "fyj_sealed" in raw
    assert "li_at" not in raw and "FAKE_FIXTURE_TOKEN" not in raw
    assert inspect_storage_state(storage_state)["has_auth_cookie"] is True

    # 2) A real headless broker surface on the same slug picks the session back up.
    # The broker drops `--use-mock-keychain`, so the profile's cookie jar is
    # unreadable to it; the sealed storage-state seeded on first bind restores
    # `li_at`, and the action reads it back off the surface's own context.
    surface = broker.surface(SURFACE_SLUG)
    try:
        await surface.wait_ready(timeout_seconds=60)
    except BrowserLaunchError as exc:  # pragma: no cover — CI without any browser
        broker.shutdown()
        pytest.skip(f"real browser unavailable: {exc}")
    try:
        session = AccountSession(
            storage_state_path=storage_state,
            surface_provider=lambda _slug: surface,
        )
        # Any action through the broker-backed session triggers the first-lane
        # seed on the surface thread (like the operation runner: off the loop).
        await asyncio.to_thread(session.run_browser, lambda: None)

        # The surface's OWN context now carries the session — read it straight
        # off the surface, proving the broker (not just the session alias) picked
        # the login's `li_at` back up.
        cookie_names = await asyncio.wrap_future(
            surface.run_on_lane(lambda s: [c["name"] for c in s.context.cookies()])
        )
        assert "li_at" in cookie_names

        # Detaching the session leaves the broker-owned surface alive.
        session.close()
        assert not surface.is_dead
    finally:
        broker.shutdown()


def test_singleton_lock_refuses_a_second_open_while_the_profile_is_held(
    tmp_path: Path, fixture_login_url: str
) -> None:
    """The login window must be closed before the surface opens, as a test. Sync, so
    raw Playwright runs here; at `channel="chrome"`, skipped without it."""
    # Seed a real li_at into the broker's per-slug profile via the same test login.
    profile_dir = tmp_path / "data" / "browser" / SURFACE_SLUG / "profile"
    storage_state = profile_dir.parent / "storage_state.json"
    result = capture_login(
        storage_state,
        login_url=fixture_login_url,
        timeout_s=20.0,
        poll_interval_s=0.25,
        headed=False,
        user_data_dir=profile_dir,
    )
    assert result["connected"] is True

    playwright = sync_playwright().start()

    def _open_chrome():
        return playwright.chromium.launch_persistent_context(
            str(profile_dir), channel="chrome", headless=True
        )

    try:
        try:
            held = _open_chrome()
        except PlaywrightError as exc:  # pragma: no cover — CI without real Chrome
            pytest.skip(f"real Chrome channel unavailable: {exc}")

        # While one Chrome holds the profile, a second open on it is refused.
        # POSIX says so in words ("Failed to create a ProcessSingleton"); on
        # Windows the process just exits 21, which is that same verdict as a
        # number — CHROME_RESULT_CODE_PROFILE_IN_USE, the 17th code after
        # content::RESULT_CODE_LAST_CODE (frozen at 5 by a static_assert).
        try:
            with pytest.raises(PlaywrightError, match=r"ProcessSingleton|exitCode=21"):
                _open_chrome()
        finally:
            held.close()

        # After the holder closes, the headless open succeeds and reads li_at back.
        reopened = _open_chrome()
        try:
            assert "li_at" in [c.get("name") for c in reopened.cookies()]
        finally:
            reopened.close()
    finally:
        playwright.stop()
