"""Covers: the broker surface-provider bridge (`networker_ops.build_surface_provider`).

The synchronous worker→loop bridge that lets a synchronous referral op acquire
the async-launched broker surface without blocking the serving loop: the
surface's `wait_ready` (which awaits a loop-bound event) is run ON the serving
loop via `run_coroutine_threadsafe`, while the operation's own worker thread
blocks only on the returned `concurrent.futures.Future`. No real Chrome — a fake
broker/surface stands in for the whole `sidecar/app/browser` surface.
"""

from __future__ import annotations

import asyncio
import threading

from sidecar.app.registry import networker_ops


class _FakeSurface:
    def __init__(self) -> None:
        self.ready_on: threading.Thread | None = None

    async def wait_ready(self) -> None:
        # Record which thread actually ran the await, to prove it was the loop's.
        self.ready_on = threading.current_thread()


class _FakeBroker:
    def __init__(self) -> None:
        self.surface_obj = _FakeSurface()
        self.asked: list[str] = []

    def surface(self, slug: str) -> _FakeSurface:
        self.asked.append(slug)
        return self.surface_obj


async def test_provider_readies_the_surface_on_the_loop_not_the_worker() -> None:
    loop_thread = threading.current_thread()  # the serving loop runs here
    broker = _FakeBroker()
    provider = networker_ops.build_surface_provider(broker, asyncio.get_running_loop())

    # Invoke the provider from a WORKER thread, exactly as an operation does.
    surface = await asyncio.to_thread(provider, "surface-a")

    assert surface is broker.surface_obj
    assert broker.asked == ["surface-a"]  # the slug is forwarded verbatim
    # `wait_ready` was awaited ON THE SERVING LOOP, never the worker thread — the
    # whole point of `run_coroutine_threadsafe`.
    assert surface.ready_on is loop_thread
