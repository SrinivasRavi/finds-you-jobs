"""resolve_log_dir precedence — the flight recorder must never write into a
packaged app bundle (signed-bundle boot crash, apple-signing.md section 7)."""

from __future__ import annotations

import sys
from pathlib import Path

from sidecar.app.logging_setup import _REPO_ROOT, resolve_log_dir


def test_dev_default_is_repo_logs(monkeypatch):
    monkeypatch.delenv("FYJ_LOG_DIR", raising=False)
    assert resolve_log_dir() == _REPO_ROOT / "logs"


def test_frozen_build_uses_app_data_dir_not_the_bundle(monkeypatch, tmp_path):
    monkeypatch.delenv("FYJ_LOG_DIR", raising=False)
    monkeypatch.setenv("FYJ_DATA_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert resolve_log_dir() == tmp_path / "profile" / "logs"


def test_fyj_log_dir_wins_over_frozen(monkeypatch, tmp_path):
    monkeypatch.setenv("FYJ_LOG_DIR", str(tmp_path / "elsewhere"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert resolve_log_dir() == tmp_path / "elsewhere"


def test_explicit_arg_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setenv("FYJ_LOG_DIR", str(tmp_path / "ignored"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert resolve_log_dir(tmp_path / "explicit") == Path(tmp_path / "explicit")
