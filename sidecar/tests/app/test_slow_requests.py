"""Covers the two-bar request timer and the event-loop lag monitor.

The maintainer's own database answers every GET route under 22 ms, so the
pre-existing 1 s warning bar alone would never fire and couldn't catch a
regression while it's still cheap to notice (`SLOW_REQUEST_SECONDS` /
`SLOW_REQUEST_INFO_SECONDS` in `main.py`). The lag monitor
(`observability/loop_lag.py`) covers what the request timer structurally
cannot: a stall with no request in flight at all.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app import main as main_module
from sidecar.app.main import create_app
from sidecar.app.observability.loop_lag import monitor_loop_lag

TOKEN = "test-token-slow"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


def _slow_request_records(
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "slow request" in r.getMessage()]


# -- the two request-timer bars ----------------------------------------------


def test_a_fast_request_logs_neither_bar(
    app_client: tuple[FastAPI, TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    _app, client = app_client
    with caplog.at_level(logging.INFO, logger="fyj.sidecar"):
        client.get("/healthz", headers=AUTH)
    assert _slow_request_records(caplog) == []


def test_a_request_over_the_info_bar_logs_at_info_only(
    app_client: tuple[FastAPI, TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    """SLOW_REQUEST_SECONDS stays at its real 1 s value, so a request that's
    merely over the informational bar must not also cross the warning one."""
    _app, client = app_client
    with (
        caplog.at_level(logging.INFO, logger="fyj.sidecar"),
        patch.object(main_module, "SLOW_REQUEST_INFO_SECONDS", 0.0),
    ):
        client.get("/healthz", headers=AUTH)

    records = _slow_request_records(caplog)
    assert len(records) == 1
    assert records[0].levelname == "INFO"
    assert "/healthz" in records[0].getMessage()


def test_a_request_over_the_warn_bar_logs_at_warning_only(
    app_client: tuple[FastAPI, TestClient], caplog: pytest.LogCaptureFixture
) -> None:
    """S-C26: a request that will cost a restart still gets its own WARNING
    line, and — since the bars are elif, not two independent ifs — it fires
    once, not twice."""
    _app, client = app_client
    with (
        caplog.at_level(logging.INFO, logger="fyj.sidecar"),
        patch.object(main_module, "SLOW_REQUEST_SECONDS", 0.0),
    ):
        client.get("/healthz", headers=AUTH)

    records = _slow_request_records(caplog)
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert "/healthz" in records[0].getMessage()


# -- the event-loop lag monitor ----------------------------------------------


def _lag_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "event loop lag" in r.getMessage()]


async def test_lag_monitor_warns_when_the_loop_is_genuinely_blocked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = asyncio.create_task(monitor_loop_lag(interval=0.02, threshold=0.03))

    async def _block_the_loop() -> None:
        time.sleep(0.08)  # noqa: ASYNC251 — the point of this test is a real block

    with caplog.at_level(logging.WARNING, logger="fyj.sidecar"):
        await asyncio.create_task(_block_the_loop())
        await asyncio.sleep(0.05)  # let the monitor's delayed wake-up log

    monitor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor

    lagged = _lag_records(caplog)
    assert len(lagged) >= 1
    assert lagged[0].levelname == "WARNING"


async def test_lag_monitor_is_quiet_when_the_loop_is_healthy(
    caplog: pytest.LogCaptureFixture,
) -> None:
    monitor = asyncio.create_task(monitor_loop_lag(interval=0.02, threshold=0.03))

    with caplog.at_level(logging.WARNING, logger="fyj.sidecar"):
        await asyncio.sleep(0.1)  # several intervals, nothing blocking the loop

    monitor.cancel()
    with pytest.raises(asyncio.CancelledError):
        await monitor

    assert _lag_records(caplog) == []


async def test_lag_monitor_is_cancellable_mid_sleep() -> None:
    task = asyncio.create_task(monitor_loop_lag(interval=0.05, threshold=0.05))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_lag_monitor_task_is_cancelled_by_the_real_lifespan_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wires through the real `create_app` lifespan (not the standalone
    coroutine) to prove `loop_lag_task` rides the same cancel-then-await
    block as scheduler_task/watchdog_task/reaper_task — a leaked task is a
    known past defect in this codebase."""
    real_monitor = main_module.monitor_loop_lag
    captured: dict[str, asyncio.Task[None]] = {}

    async def _tracking_monitor(*args: object, **kwargs: object) -> None:
        task = asyncio.current_task()
        assert task is not None
        captured["task"] = task
        await real_monitor(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(main_module, "monitor_loop_lag", _tracking_monitor)

    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        client.get("/healthz", headers=AUTH)

    task = captured["task"]
    assert task.done()
    assert task.cancelled()
