"""Covers: A1 scaffold — SSE typed envelope shape (architecture §4.1 events.py)."""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import ValidationError

from sidecar.app.events import (
    SSE_ENVELOPE_ADAPTER,
    format_sse,
    heartbeat_event,
    heartbeat_stream,
    make_event,
    operation_event,
    register_sse_schemas,
    scheduler_event,
)


def test_make_event_shape() -> None:
    ev = make_event("heartbeat", {"seq": 3, "ts": 4.5})
    assert ev == {"type": "heartbeat", "payload": {"seq": 3, "ts": 4.5}}


def test_make_event_rejects_a_misshapen_envelope_under_test_env() -> None:
    """The choke-point validation (F-M2 follow-up): a publish site that drops
    or renames a load-bearing key must fail loudly in dev/test — under pytest,
    `_strict_envelopes()` is always true, so `make_event` raises."""
    with pytest.raises(ValidationError):
        make_event("heartbeat", {"seq": 3})  # ts missing
    with pytest.raises(ValidationError):
        make_event("operation", {"id": "op-1", "kind": "score", "state": "renamed_state"})
    with pytest.raises(ValidationError):
        make_event("networker", {"operation_id": "op-1", "phase": "sent"})  # id renamed
    with pytest.raises(ValidationError):
        make_event("not_a_stream_type", {"anything": True})


def test_make_event_logs_and_still_publishes_in_prod(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """In production an invalid envelope must never crash the sidecar: it is
    logged (warning) and shipped anyway."""
    from sidecar.app import events as events_mod

    monkeypatch.setattr(events_mod, "_strict_envelopes", lambda: False)
    with caplog.at_level(logging.WARNING, logger="fyj.sidecar"):
        ev = make_event("heartbeat", {"seq": 3})  # ts missing — invalid
    assert ev == {"type": "heartbeat", "payload": {"seq": 3}}
    assert any("invalid SSE envelope" in r.message for r in caplog.records)


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


# ─── F-M2: the typed envelope models mirror what the app really publishes ────
# Each sample below is a verbatim copy of a real publish site's shape; if a
# publisher renames a load-bearing key, validation here fails instead of the
# frontend's invalidation silently going dead.


def test_envelope_models_accept_real_published_shapes() -> None:
    samples = [
        heartbeat_event(3),
        operation_event("op1", "score", "succeeded", result_ref={"x": 1}, usage=None),
        operation_event("op2", "scan", "failed", error="boom"),
        scheduler_event("s1", "scan", "enqueued", operation_id="op9"),
        # networker_ops.py discover/send phases
        make_event("networker", {"id": "op3", "phase": "discovered",
                                 "company": "Acme", "job_id": "j1", "count": 4}),
        make_event("networker", {"id": "op3", "phase": "candidate",
                                 "company": "Acme", "job_id": "j1", "contact_id": "c1"}),
        make_event("networker", {"id": "op4", "phase": "sent", "contact_id": "c1",
                                 "job_id": None, "sent": True, "reason": "", "quota": None}),
        # apply_op.py progress envelope
        make_event("apply", {"run_id": "r1", "operation_id": "op5",
                             "event": "apply.phase_changed", "phase": "filling"}),
        # linkedin_op.py session states
        make_event("linkedin", {"id": "op6", "state": "connected", "connected_as": "me"}),
        # api/browser.py install progress
        make_event("browser_install", {"state": "installing", "message": None}),
    ]
    for sample in samples:
        validated = SSE_ENVELOPE_ADAPTER.validate_python(sample)
        assert validated.type == sample["type"]


def test_envelope_models_reject_renamed_load_bearing_key() -> None:
    with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError
        SSE_ENVELOPE_ADAPTER.validate_python(
            {"type": "apply", "payload": {"runId": "r1", "operation_id": "o", "event": "e"}}
        )


def test_register_sse_schemas_is_idempotent_and_carries_llm_kinds() -> None:
    from sidecar.app.registry.engine_config import LLM_KINDS

    schema: dict = {"components": {"schemas": {}}}
    register_sse_schemas(schema)
    register_sse_schemas(schema)  # second call must be a no-op
    components = schema["components"]["schemas"]
    assert "SSEEnvelope" in components
    assert "OperationEventPayload" in components
    # F-L3: the routable kinds ride the schema so the frontend can't drift.
    assert components["LlmKind"]["enum"] == list(LLM_KINDS)
