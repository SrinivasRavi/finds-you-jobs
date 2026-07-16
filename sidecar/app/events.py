"""SSE hub (architecture §4.1 `events.py`).

One stream, typed envelopes `{type, payload}`. A1 shipped the heartbeat-only
stub; A3 grows it into the real operation-state / scheduler-result hub via
`EventHub` — a thread-safe fan-out that the runner (worker threads) and the
scheduler (event loop) both publish into, and every SSE client subscribes to.
The envelope shape is the contract that stays stable.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

HEARTBEAT_INTERVAL_SECONDS = 2.0


def make_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A typed SSE envelope. Every event on the stream has this shape."""
    return {"type": event_type, "payload": payload}


def heartbeat_event(seq: int, *, now: float | None = None) -> dict[str, Any]:
    """The A1 heartbeat envelope. Pure — unit-testable."""
    ts = time.time() if now is None else now
    return make_event("heartbeat", {"seq": seq, "ts": ts})


def format_sse(event: dict[str, Any]) -> str:
    """Serialize an envelope to an SSE `data:` frame."""
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


async def heartbeat_stream(
    *,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
    limit: int | None = None,
) -> AsyncIterator[str]:
    """Yield SSE heartbeat frames forever (or `limit` frames, for tests)."""
    seq = 0
    while limit is None or seq < limit:
        yield format_sse(heartbeat_event(seq))
        seq += 1
        await asyncio.sleep(interval)


def operation_event(
    operation_id: str, kind: str, state: str, **extra: Any
) -> dict[str, Any]:
    """Typed envelope for an operation state change (architecture §5.3)."""
    payload: dict[str, Any] = {"id": operation_id, "kind": kind, "state": state}
    payload.update(extra)
    return make_event("operation", payload)


def scheduler_event(schedule_id: str, kind: str, action: str, **extra: Any) -> dict[str, Any]:
    """Typed envelope for a scheduler decision (enqueued / skipped)."""
    payload: dict[str, Any] = {"schedule_id": schedule_id, "kind": kind, "action": action}
    payload.update(extra)
    return make_event("scheduler", payload)


class EventHub:
    """Thread-safe SSE fan-out (architecture §5.3 "publishes to the SSE hub").

    The runner's worker threads and the scheduler's coroutine both call
    `publish`. Delivery into each subscriber's `asyncio.Queue` is marshalled
    onto the bound event loop when publishing from another thread; when no loop
    is bound (unit tests on the same thread) it delivers inline.
    """

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the serving loop so cross-thread publishes marshal onto it."""
        self._loop = loop

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        loop = self._loop
        for queue in subscribers:
            if loop is not None and loop.is_running():
                loop.call_soon_threadsafe(queue.put_nowait, event)
            else:
                queue.put_nowait(event)

    def _subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def _unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    async def stream(
        self,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS,
        limit: int | None = None,
    ) -> AsyncIterator[str]:
        """SSE frames: real events as they arrive, heartbeats on idle.

        `limit` (tests) caps the total number of frames yielded.
        """
        queue = self._subscribe()
        seq = 0
        emitted = 0
        try:
            while limit is None or emitted < limit:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                    yield format_sse(event)
                except TimeoutError:
                    yield format_sse(heartbeat_event(seq))
                    seq += 1
                emitted += 1
        finally:
            self._unsubscribe(queue)
