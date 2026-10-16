# voyager_py/tests/test_session_on_host.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""The self-launch session runs on the app's 1 browser host (audit-2026-10-14.md
section 8.9, stage 4). Real Chromium, headless, a LOCAL fixture standing in for
the feed: never linkedin.com."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.async_api")

from sidecar.app.browser.host import SyncProxy, get_host, launch  # noqa: E402
from sidecar.packages.referral_outreach.upstream import session as session_mod  # noqa: E402
from sidecar.packages.referral_outreach.upstream.session import AccountSession  # noqa: E402


class _Feed(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
        body = b"<!doctype html><title>Fixture feed</title><h1>feed</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # type: ignore[override]
        return


@pytest.fixture()
def feed_url(monkeypatch: pytest.MonkeyPatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Feed)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address  # type: ignore[misc]
    url = f"http://{host}:{port}/feed/"
    monkeypatch.setattr(session_mod, "LINKEDIN_FEED_URL", url)
    monkeypatch.setattr(session_mod, "COMPLY_LOCATORS", [])  # no gate to wait 5 s on
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


def _state(tmp_path, host: str):
    path = tmp_path / "storage_state.json"
    cookie = {"name": "li_at", "value": "FAKE", "domain": host, "path": "/",
              "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}
    path.write_text(json.dumps({"cookies": [cookie], "origins": []}), encoding="utf-8")
    return path


def _host_still_launches() -> None:
    """The session's close must leave the host's driver running for everyone else."""
    host = get_host()

    async def _probe():
        browser = await launch((await host.playwright()).chromium, headless=True)
        await browser.close()

    host.run(_probe, timeout=60)


@pytest.mark.parametrize("persistent", [True, False], ids=["profile", "storage-state"])
def test_self_launch_drives_chrome_through_the_host(tmp_path, feed_url, persistent) -> None:
    session = AccountSession(
        storage_state_path=_state(tmp_path, "127.0.0.1"),
        user_data_dir=tmp_path / "profile" if persistent else None,
    )
    try:
        session.start()
    except Exception as exc:  # pragma: no cover, CI without any Chromium
        pytest.skip(f"no Chromium to launch: {exc}")
    try:
        assert isinstance(session.page, SyncProxy)
        assert session.page.url == feed_url
        # A hung page is ended from another thread: the sync API refused that.
        killer = threading.Thread(target=session.kill_browser)
        killer.start()
        killer.join(10)
        assert session.page.is_closed()
    finally:
        session.close()
    _host_still_launches()
