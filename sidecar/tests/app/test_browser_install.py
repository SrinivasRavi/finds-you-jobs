"""Covers: A16, how the app gets Chromium.

The install runs Playwright's own driver, never `sys.executable` (which frozen
is the sidecar itself); a frozen app keeps browsers in its app-data folder; and
Export names a missing browser with a code the screen can offer Download for.
The missing-browser case is real: an empty browsers folder, no mock."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sidecar.app.api import browser
from sidecar.app.main import create_app

TOKEN = "test-token-browser"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_install_command_never_runs_the_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    argv, env = browser.install_command()
    assert sys.executable not in argv
    assert "-m" not in argv
    assert Path(argv[0]).is_file() and argv[1].endswith("cli.js")
    assert argv[2:] == ["install", "chromium"]
    assert env["PW_LANG_NAME"] == "python"


def test_install_command_reaches_playwright_and_honours_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # --dry-run prints where it would install and downloads nothing.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "ms-playwright"))
    argv, env = browser.install_command()
    proc = subprocess.run(  # noqa: S603 — the command under test
        [*argv[:2], "install", "--dry-run", "chromium"],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert str(tmp_path / "ms-playwright") in proc.stdout


def test_frozen_app_keeps_browsers_in_app_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # setenv first so the undo removes what pin_browsers_path writes.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    browser.pin_browsers_path()
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(tmp_path / "ms-playwright")


def test_unfrozen_app_keeps_playwrights_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    browser.pin_browsers_path()
    assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ


def test_export_without_chromium_is_coded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    from sidecar.app.api import system as routes_mod
    from sidecar.app.browser import launch

    monkeypatch.setattr(launch, "PREFERRED_CHANNEL", None)  # no installed Chrome either
    monkeypatch.setattr(routes_mod, "downloads_dir", lambda: tmp_path / "downloads")
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/export/pdf", headers=AUTH, json={"markdown": "# Ada", "filename": "cv"}
        )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "browser_missing"


def _chrome_installed() -> bool:
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as pw:
            pw.chromium.launch(channel="chrome", headless=True).close()
        return True
    except Exception:  # noqa: BLE001 — absent or unlaunchable reads the same
        return False


def test_export_with_only_installed_chrome_needs_no_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """applier/window.md section 4.2: installed Chrome first, so a Chrome user never
    sees Download, even with Playwright's Chromium absent."""
    if not _chrome_installed():
        pytest.skip("no installed Chrome on this machine")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "empty"))
    from sidecar.app.api import system as routes_mod

    monkeypatch.setattr(routes_mod, "downloads_dir", lambda: tmp_path / "downloads")
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        resp = client.post(
            "/api/export/pdf", headers=AUTH, json={"markdown": "# Ada", "filename": "cv"}
        )
    assert resp.status_code == 200, resp.text
    assert Path(resp.json()["path"]).read_bytes()[:5] == b"%PDF-"


def test_install_status_is_readable_after_a_missed_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A21: the done event can be lost with the stream; the status route still says it."""
    from sidecar.app.api import browser as browser_mod

    calls: list[list[str]] = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **_kw):  # type: ignore[no-untyped-def]
        calls.append(argv)
        return _Done()

    monkeypatch.setattr(browser_mod.subprocess, "run", fake_run)
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        started = client.post("/api/system/install-browser", headers=AUTH).json()
        assert started == {"status": "started"}
        deadline = time.monotonic() + 5
        status: dict = {}
        while time.monotonic() < deadline:
            status = client.get("/api/system/install-browser", headers=AUTH).json()
            if status["state"] != "running":
                break
            time.sleep(0.05)
    assert status["state"] == "done", status
    assert any(argv[-2:] == ["install", "chromium"] for argv in calls)
