"""Covers: A16, how the app gets Chromium.

The install runs Playwright's own driver, never `sys.executable` (which frozen
is the sidecar itself); a frozen app keeps browsers in its app-data folder; and
Export names a missing browser with a code the screen can offer Download for.
The missing-browser case is real: an empty browsers folder, no mock."""

from __future__ import annotations

import os
import subprocess
import sys
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
