# voyager_py/tests/test_login_capture.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Login-capture plumbing, verified against a LOCAL fixture — **never**
linkedin.com. A tiny localhost server serves a page that sets a `li_at` cookie
(the same signal a real LinkedIn login drops); `capture_login` must detect it and
persist a Playwright storage-state. Real Chromium, headless, zero LinkedIn
traffic. Live login is a maintainer-only, tiny-volume action (README § Live use).
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# Chromium is fetched on demand (never bundled); skip cleanly if it isn't present.
playwright_sync = pytest.importorskip("playwright.sync_api")

from sidecar.packages.referral_outreach.upstream.session import (  # noqa: E402
    capture_login,
    inspect_storage_state,
)

_FIXTURE_HTML = (
    "<!doctype html><html><head><meta charset='utf-8'>"
    "<title>Fixture login</title></head><body>"
    "<h1>fixture login page</h1>"
    "<script>document.cookie = 'li_at=FAKE_FIXTURE_TOKEN; path=/';</script>"
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


def test_capture_login_detects_cookie_and_saves_state(fixture_login_url, tmp_path):
    state_path = tmp_path / "linkedin" / "storage_state.json"
    result = capture_login(
        state_path,
        login_url=fixture_login_url,
        timeout_s=20.0,
        poll_interval_s=0.25,
        headed=False,  # CI-friendly; production login is headed
    )
    assert result["connected"] is True
    assert result["cookie_count"] >= 1
    assert state_path.exists()

    saved = json.loads(state_path.read_text())
    names = {c["name"] for c in saved["cookies"]}
    assert "li_at" in names

    # And the LOCAL validator (no browser) reads it back as a live session.
    info = inspect_storage_state(state_path)
    assert info["present"] and info["has_auth_cookie"] and not info["expired"]


def test_capture_login_times_out_without_cookie(tmp_path):
    # A blank about:blank page never sets li_at → SkipProfile within the timeout.
    from sidecar.packages.referral_outreach.upstream.errors import SkipProfile

    state_path = tmp_path / "storage_state.json"
    with pytest.raises(SkipProfile):
        capture_login(
            state_path,
            login_url="about:blank",
            timeout_s=2.0,
            poll_interval_s=0.25,
            headed=False,
        )
    assert not state_path.exists()


def test_capture_login_cancelled(tmp_path):
    from sidecar.packages.referral_outreach.upstream.errors import SkipProfile

    state_path = tmp_path / "storage_state.json"
    with pytest.raises(SkipProfile, match="cancelled"):
        capture_login(
            state_path,
            login_url="about:blank",
            timeout_s=20.0,
            poll_interval_s=0.25,
            cancelled=lambda: True,  # aborts on the first poll
            headed=False,
        )


def test_capture_login_seals_state_when_key_set(
    tmp_path, fixture_login_url, monkeypatch
) -> None:
    """NFR-SEC-01: with FYJ_SESSION_KEY in the env (how the host always runs
    this worker), the saved storage-state is encrypted at rest — no cookie
    name/value readable in the file — and the local validator still reads it."""
    from cryptography.fernet import Fernet

    from sidecar.packages.referral_outreach.upstream.secure_store import SESSION_KEY_ENV

    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    state_path = tmp_path / "linkedin" / "storage_state.json"
    result = capture_login(
        state_path,
        login_url=fixture_login_url,
        timeout_s=20.0,
        poll_interval_s=0.25,
        headed=False,
    )
    assert result["connected"] is True
    raw = state_path.read_text()
    assert "fyj_sealed" in raw
    assert "li_at" not in raw and "FAKE_FIXTURE_TOKEN" not in raw  # no plaintext secret
    info = inspect_storage_state(state_path)
    assert info["present"] and info["has_auth_cookie"] and not info["expired"]


def test_login_capture_persistent_profile(tmp_path, fixture_login_url) -> None:
    """Persistent-profile login (2026-07-09): cookies persist in the user-data
    dir, and the JSON storage-state is still exported for the validate path."""
    from sidecar.packages.referral_outreach.upstream.session import (
        capture_login,
        inspect_storage_state,
    )

    profile = tmp_path / "profile"
    out = tmp_path / "state.json"
    result = capture_login(
        out,
        login_url=fixture_login_url,
        timeout_s=30,
        poll_interval_s=0.2,
        headed=False,
        user_data_dir=profile,
    )
    assert result["connected"] is True
    assert profile.exists() and any(profile.iterdir())  # profile populated
    assert out.exists()  # JSON mirror for the no-browser validate path
    assert inspect_storage_state(out)["has_auth_cookie"] is True
