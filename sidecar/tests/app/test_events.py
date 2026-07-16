"""Covers: A1 scaffold — SSE typed envelope shape (architecture §4.1 events.py)."""

from __future__ import annotations

import json

import pytest

from sidecar.app.events import (
    format_sse,
    heartbeat_event,
    heartbeat_stream,
    make_event,
)


def test_make_event_shape() -> None:
    ev = make_event("heartbeat", {"seq": 3})
    assert ev == {"type": "heartbeat", "payload": {"seq": 3}}


def test_heartbeat_event_carries_seq_and_ts() -> None:
    ev = heartbeat_event(7, now=123.5)
    assert ev == {"type": "heartbeat", "payload": {"seq": 7, "ts": 123.5}}


def test_format_sse_is_valid_frame() -> None:
    frame = format_sse(heartbeat_event(0, now=1.0))
    assert frame.endswith("\n\n")
    assert frame.startswith("data: ")
    payload = json.loads(frame[len("data: ") : -2])
    assert payload["type"] == "heartbeat"
    assert payload["payload"] == {"seq": 0, "ts": 1.0}


@pytest.mark.asyncio
async def test_heartbeat_stream_emits_bounded_frames() -> None:
    frames = [frame async for frame in heartbeat_stream(interval=0, limit=3)]
    assert len(frames) == 3
    seqs = []
    for frame in frames:
        assert frame.startswith("data: ") and frame.endswith("\n\n")
        env = json.loads(frame[len("data: ") : -2])
        assert env["type"] == "heartbeat"
        seqs.append(env["payload"]["seq"])
    assert seqs == [0, 1, 2]
