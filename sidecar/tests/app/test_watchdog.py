"""Covers: A1 scaffold — orphan watchdog decision + loop (architecture §4.4 step 4).

The decision is a pure function; the loop is driven with an injected `get_ppid`
so no real reparenting is needed.
"""

from __future__ import annotations

import asyncio

import pytest

from sidecar.app.watchdog import is_orphaned, watch_parent


def test_is_orphaned_same_parent_is_false() -> None:
    assert is_orphaned(4242, 4242) is False


def test_is_orphaned_on_reparent_is_true() -> None:
    assert is_orphaned(4242, 4243) is True


def test_is_orphaned_reparented_to_init_is_true() -> None:
    # POSIX subreaper / init pid 1.
    assert is_orphaned(4242, 1) is True
    # Even if original was somehow 1, a live parent is never 1.
    assert is_orphaned(1, 1) is True


@pytest.mark.asyncio
async def test_watch_parent_fires_callback_once_on_reparent() -> None:
    calls = {"n": 0}

    async def on_orphaned() -> None:
        calls["n"] += 1

    # Parent looks alive twice, then reparents to pid 1.
    ppids = iter([4242, 4242, 1])

    def fake_getppid() -> int:
        return next(ppids)

    await watch_parent(
        4242, on_orphaned, poll_interval=0, get_ppid=fake_getppid
    )
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_watch_parent_cancellable_while_healthy() -> None:
    async def on_orphaned() -> None:  # pragma: no cover - never called here
        raise AssertionError("should not fire while healthy")

    task = asyncio.create_task(
        watch_parent(4242, on_orphaned, poll_interval=0.01, get_ppid=lambda: 4242)
    )
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
