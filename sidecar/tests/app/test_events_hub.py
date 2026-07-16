"""Covers: A3 SSE event hub (architecture §5.3 — runner/scheduler → SSE)."""

from __future__ import annotations

import asyncio
import json

import pytest

from sidecar.app.events import EventHub, operation_event, scheduler_event


def test_operation_event_shape() -> None:
    ev = operation_event("op1", "score", "running", extra=1)
    assert ev == {
        "type": "operation",
        "payload": {"id": "op1", "kind": "score", "state": "running", "extra": 1},
    }


def test_scheduler_event_shape() -> None:
    ev = scheduler_event("s1", "scan", "enqueued", operation_id="op9")
    assert ev["type"] == "scheduler"
    assert ev["payload"] == {
        "schedule_id": "s1",
        "kind": "scan",
        "action": "enqueued",
        "operation_id": "op9",
    }


@pytest.mark.asyncio
async def test_hub_delivers_published_event_to_subscriber() -> None:
    hub = EventHub()
    hub.bind_loop(asyncio.get_running_loop())

    frames: list[str] = []

    async def consume() -> None:
        async for frame in hub.stream(heartbeat_interval=0.05, limit=1):
            frames.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)  # let the subscriber register
    assert hub.subscriber_count == 1
    hub.publish(operation_event("op1", "score", "succeeded"))
    await asyncio.wait_for(task, timeout=2)

    assert len(frames) == 1
    env = json.loads(frames[0][len("data: ") : -2])
    assert env["type"] == "operation"
    assert env["payload"]["state"] == "succeeded"
    assert hub.subscriber_count == 0  # unsubscribed on stream exit


@pytest.mark.asyncio
async def test_hub_emits_heartbeat_when_idle() -> None:
    hub = EventHub()
    hub.bind_loop(asyncio.get_running_loop())
    frames = [f async for f in hub.stream(heartbeat_interval=0.01, limit=2)]
    assert len(frames) == 2
    for frame in frames:
        env = json.loads(frame[len("data: ") : -2])
        assert env["type"] == "heartbeat"
