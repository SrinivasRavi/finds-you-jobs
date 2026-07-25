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
import logging
import os
import sys
import threading
import time
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

HEARTBEAT_INTERVAL_SECONDS = 2.0

# Each SSE subscriber's queue is bounded; on overflow the OLDEST event is
# dropped. Safe by design: events are invalidation hints, not state — a
# consumer that misses one refetches the same fresh snapshot on the next hint
# (and the stream header contract already says gaps are never replayed).
SUBSCRIBER_QUEUE_MAXSIZE = 1000


# ─── typed event payloads (F-M2) ─────────────────────────────────────────────
# Pydantic mirrors of every envelope published today, injected into the OpenAPI
# components (see `register_sse_schemas`) so `pnpm codegen` types the frontend's
# SSE seam. The wire format is untouched — `make_event` still ships plain dicts;
# these models declare the LOAD-BEARING keys (the ones the frontend's
# invalidation bridge branches on), with `extra="allow"` carrying the per-phase
# extras (`error`, `result_ref`, `contact_id`, `quota`, …). Tests validate the
# real constructors against `SSE_ENVELOPE_ADAPTER` so model and reality can't
# drift silently.


class _EventPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class HeartbeatPayload(_EventPayload):
    seq: int
    ts: float


class OperationEventPayload(_EventPayload):
    """`operation_event` — runner state changes (queued → … → terminal)."""

    id: str
    kind: str
    state: Literal["queued", "running", "succeeded", "failed", "cancelled"]


class SchedulerEventPayload(_EventPayload):
    """`scheduler_event` — a scheduler decision (enqueued / skipped)."""

    schedule_id: str
    kind: str
    action: str


class NetworkerEventPayload(_EventPayload):
    """Referral-outreach progress (networker_ops / contact_sync / linkedin_op).

    Phases published today: synced, needs_company_confirm, candidate,
    discovered, sent, send_failed, auto_archived.
    """

    id: str  # the publishing operation's id
    phase: str


class ApplyProgressPayload(_EventPayload):
    """Applier live-updates (apply_op) — `event` is an `ApplyEventType` value
    (`apply.phase_changed`, …) plus the out-of-band `apply.waiting_for_packet`."""

    run_id: str
    operation_id: str
    event: str


class LinkedInEventPayload(_EventPayload):
    """LinkedIn session capture / search progress (linkedin_op).

    States published today: connecting, connected, disconnected, searching,
    search_done.
    """

    id: str  # the publishing operation's id
    state: str


class BrowserInstallPayload(_EventPayload):
    """One-time Playwright browser install progress (api/browser.py)."""

    state: str
    message: str | None = None


class HeartbeatEvent(BaseModel):
    type: Literal["heartbeat"]
    payload: HeartbeatPayload


class OperationEvent(BaseModel):
    type: Literal["operation"]
    payload: OperationEventPayload


class SchedulerEvent(BaseModel):
    type: Literal["scheduler"]
    payload: SchedulerEventPayload


class NetworkerEvent(BaseModel):
    type: Literal["networker"]
    payload: NetworkerEventPayload


class ApplyProgressEvent(BaseModel):
    type: Literal["apply"]
    payload: ApplyProgressPayload


class LinkedInEvent(BaseModel):
    type: Literal["linkedin"]
    payload: LinkedInEventPayload


class BrowserInstallEvent(BaseModel):
    type: Literal["browser_install"]
    payload: BrowserInstallPayload


SSEEnvelope = Annotated[
    HeartbeatEvent
    | OperationEvent
    | SchedulerEvent
    | NetworkerEvent
    | ApplyProgressEvent
    | LinkedInEvent
    | BrowserInstallEvent,
    Field(discriminator="type"),
]

SSE_ENVELOPE_ADAPTER: TypeAdapter[Any] = TypeAdapter(SSEEnvelope)


def register_sse_schemas(openapi_schema: dict[str, Any]) -> None:
    """Inject the SSE envelope union (+ `LlmKind`, F-L3) into an OpenAPI schema.

    `/api/events` is a `text/event-stream` route FastAPI can't type on its own,
    so `main.py` calls this on the generated schema — the frontend then gets
    `components["schemas"]["SSEEnvelope"]` / `["LlmKind"]` from `pnpm codegen`.
    Idempotent (the schema dict FastAPI hands back is cached).
    """
    components = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    if "SSEEnvelope" in components:
        return
    union_schema = SSE_ENVELOPE_ADAPTER.json_schema(
        ref_template="#/components/schemas/{model}"
    )
    components.update(union_schema.pop("$defs", {}))
    components["SSEEnvelope"] = union_schema
    # Lazy import: registry/__init__ pulls in modules that import this file.
    from .registry.engine_config import LLM_KINDS

    components["LlmKind"] = {
        "title": "LlmKind",
        "type": "string",
        "enum": list(LLM_KINDS),
        "description": "The routable LLM operation kinds "
        "(single source: registry/engine_config.LLM_KINDS).",
    }


def _strict_envelopes() -> bool:
    """Whether an invalid envelope should RAISE (dev + test) instead of being
    logged and shipped anyway (production — an invalid event must never crash
    the sidecar; the worst case is one missed UI invalidation, already better
    than a dead stream)."""
    return os.environ.get("FYJ_DEV") == "1" or "pytest" in sys.modules


def make_event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """A typed SSE envelope. Every event on the stream has this shape.

    Single choke point for the F-M2 drift-proofing: every publisher builds its
    envelope here, so each one is validated against the `SSEEnvelope`
    discriminated union the frontend's codegen types are emitted from. A key
    rename at a publish site now fails loudly in dev/test instead of silently
    killing UI invalidation; in production it is logged and still published.
    """
    event = {"type": event_type, "payload": payload}
    try:
        SSE_ENVELOPE_ADAPTER.validate_python(event)
    except ValidationError:
        if _strict_envelopes():
            raise
        logging.getLogger("fyj.sidecar").warning(
            "invalid SSE envelope published (type=%r): %r", event_type, event, exc_info=True
        )
    return event


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


def _deliver_drop_oldest(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
    """Enqueue for one subscriber; on a full queue drop the OLDEST event (F-L1).

    Runs on the bound event loop (or inline when none is bound), so the
    get/put pair can't interleave with the consumer mid-pair.
    """
    if queue.full():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:  # pragma: no cover — same-loop, but be safe
            pass
    queue.put_nowait(event)


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
                loop.call_soon_threadsafe(_deliver_drop_oldest, queue, event)
            else:
                _deliver_drop_oldest(queue, event)

    def _subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_MAXSIZE
        )
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
