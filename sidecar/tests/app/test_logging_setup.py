"""The flight recorder: where it writes, and that it writes each line once.

`resolve_log_dir` precedence, because the recorder must never write into a
packaged app bundle (signed-bundle boot crash, apple-signing.md section 7);
and `setup_flight_recorder`'s idempotence, because the boot path calls it more
than once and a missed guard duplicates every line the recorder holds.
"""

from __future__ import annotations

import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from sidecar.app.logging_setup import (
    _REPO_ROOT,
    get_logger,
    resolve_log_dir,
    setup_flight_recorder,
)


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


def _file_handlers(path: Path) -> list[RotatingFileHandler]:
    resolved = path.resolve()
    return [
        h
        for h in get_logger().handlers
        if isinstance(h, RotatingFileHandler)
        and Path(h.baseFilename).resolve() == resolved
    ]


def test_repeated_setup_does_not_stack_handlers(tmp_path):
    log_path = setup_flight_recorder(tmp_path)
    try:
        for _ in range(3):
            setup_flight_recorder(tmp_path)
        assert len(_file_handlers(log_path)) == 1
    finally:
        for handler in _file_handlers(log_path):
            get_logger().removeHandler(handler)
            handler.close()


def test_a_symlinked_log_dir_still_counts_as_the_same_file(tmp_path):
    """The bug this pins: `baseFilename` is `abspath`, so it keeps symlinks.

    A data dir reached through one (`/tmp` on macOS is `/private/tmp`, and every
    throwaway test profile lives there) made the idempotence guard compare
    `/tmp/...` against `/private/tmp/...`, never match, and add a handler per
    call — so the flight recorder wrote every line once per boot-path call.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    log_path = setup_flight_recorder(link)
    try:
        setup_flight_recorder(real)
        setup_flight_recorder(link)
        assert len(_file_handlers(log_path)) == 1
    finally:
        for handler in _file_handlers(log_path):
            get_logger().removeHandler(handler)
            handler.close()
