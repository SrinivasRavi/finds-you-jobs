"""The Operation Runner (architecture section 5.3).

Durable queue → thread-pool workers → persisted state transitions
(`queued → running → succeeded|failed|cancelled`) → the cost ledger → typed SSE
events. Module calls are blocking (subprocess/HTTP), so threads suffice; the
policy (not the pool size) is what bounds concurrency.

Failures land in the operation row + an event, error verbatim (NFR-SIDE-04).
Boot recovery re-enqueues `queued` and fails orphaned `running` (NFR-LONG-02).
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from threading import Event, RLock
from typing import Any

from sidecar.modules._shared.claude_engine import EngineError
from sidecar.modules._shared.completion_retry import CompletionCancelled

from ..db import Database
from ..events import operation_event
from ..logging_setup import get_logger
from ..observability import (
    ObservabilityHandle,
    operation_span,
    record_span_failure,
    record_span_success,
)
from ..registry import (
    CANCELLABLE_RUNNING_KINDS,
    EngineRegistry,
    OperationContext,
    OperationRegistry,
    default_operation_registry,
)
from .circuit import EngineCircuitBreaker
from .policy import (
    DEFAULT_POLICY,
    ConcurrencyPolicy,
    can_start,
    dispatch_priority,
    with_llm_limit,
)

RESTART_NOTE = (
    "operation was running when the sidecar restarted; marked failed on boot "
    "recovery (NFR-LONG-02)"
)

PublishFn = Callable[[dict[str, Any]], None]
OnSuccessFn = Callable[[str, str], None]  # (operation_id, kind) after a success

# P1 ledger retention: keep the 250 most-recent terminal operations (~5 pages of
# 50 in the Analytics ledger); older ones are pruned after each completion so the
# operations table stays bounded on a long-lived install (US-LOG-01 #2).
LEDGER_RETENTION = 250


class OperationRunner:
    """Owns the operations queue, the worker pool, and the ledger writes."""

    def __init__(
        self,
        db: Database,
        *,
        registry: OperationRegistry | None = None,
        engines: EngineRegistry | None = None,
        policy: ConcurrencyPolicy = DEFAULT_POLICY,
        publish: PublishFn | None = None,
        on_success: OnSuccessFn | None = None,
        observability: ObservabilityHandle | None = None,
        # Worker threads bound REAL parallelism; the per-group policy bounds
        # intended parallelism. Sized generously so a raised llm_concurrency
        # setting isn't silently capped — idle threads cost nothing. 32 is the
        # practical ceiling for the "Unlimited" setting.
        max_workers: int = 32,
        # Injectable for tests (short cooldowns); production uses the default.
        circuit: EngineCircuitBreaker | None = None,
    ) -> None:
        self._db = db
        self._registry = registry or default_operation_registry()
        self._engines = engines
        self._policy = policy
        self._publish_fn = publish
        # Live observability config (content-logging flag). None ⇒ spans still
        # emit (US-SYS-05) but never carry input content and, absent an app-level
        # logfire.configure, land nowhere (isolated runner unit tests).
        self._observability = observability
        # Post-success chain hook (e.g. scan → score fan-out). Public so the
        # app assembly can wire a closure over the runner itself (main.py).
        self.on_success = on_success
        self._max_workers = max_workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = RLock()
        self._running: dict[str, str] = {}  # operation_id -> kind (in-flight)
        # Per-running-op cooperative cancel tokens (F-M7): created at dispatch,
        # exposed to the entrypoint via ctx.cancelled, dropped on completion.
        self._cancels: dict[str, Event] = {}
        self._futures: set[Future[None]] = set()
        self._closing = False
        # Per-engine-name circuit breaker (F-H5): a provider that keeps
        # failing stops burning slot time on every queued op routed to it.
        self._circuit = circuit or EngineCircuitBreaker()
        self._log = get_logger()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Open the worker pool and run boot recovery, then pump the queue."""
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="fyj-op"
        )
        self._closing = False
        self.recover()

    def shutdown(self, *, drain_timeout: float | None = None) -> None:
        """Stop accepting dispatches; wait for in-flight workers to drain.

        `drain_timeout` mirrors the section 4.4 10 s shutdown window; None waits fully.
        The Tauri shell force-kills after its own window, so overshooting here is
        capped upstream — this just gives in-flight operations a clean chance.
        """
        with self._lock:
            self._closing = True
            executor = self._executor
            self._executor = None
            pending = set(self._futures)
        if executor is None:
            return
        if drain_timeout is not None and pending:
            wait(pending, timeout=drain_timeout)
        # cancel_futures drops still-queued dispatches; in-flight run to end.
        executor.shutdown(wait=drain_timeout is None, cancel_futures=True)

    # -- enqueue -----------------------------------------------------------

    def known_kinds(self) -> frozenset[str]:
        """The operation kinds this runner can dispatch (registry keys)."""
        return self._registry.kinds()

    def submit(self, kind: str, input_snapshot: dict[str, Any]) -> str:
        """Create a `queued` operation, publish, and pump. Returns the id now."""
        with self._db.repos() as repos:
            op = repos.operations.create(kind, input_snapshot)
            operation_id = op.id
        self._log.info("operation %s enqueued (kind=%s)", operation_id, kind)
        self._publish(operation_id, kind, "queued")
        self._pump()
        return operation_id

    def cancel(self, operation_id: str) -> bool:
        """Cancel an operation. A still-`queued` op (any kind) is cancelled
        immediately.

        A `running` op gets a cooperative cancel REQUEST (F-M7) — but only for
        the kinds that actually POLL the token (`CANCELLABLE_RUNNING_KINDS`,
        kept next to the entrypoint registry): the LLM modules check between
        retry attempts and mid-backoff. Other kinds never observe the token
        while running (`apply` checks once at dispatch; its real cancel is the
        apply-run route), so accepting the request would be a lie — they
        return False (honesty fix, 2026-07-25). A call already blocked inside
        an engine (subprocess / HTTP read) runs to its own timeout first — we
        never kill a live worker thread mid-syscall. Returns True when the
        cancel was applied (queued) or genuinely accepted (running + polling
        kind); False for unknown, already-terminal, or non-polling running ops."""
        with self._lock:
            with self._db.repos() as repos:
                op = repos.operations.get(operation_id)
                if op is None:
                    return False
                if op.state == "running":
                    if op.kind not in CANCELLABLE_RUNNING_KINDS:
                        return False
                    token = self._cancels.get(operation_id)
                    if token is None:
                        return False
                    token.set()
                    self._log.info("operation %s (%s) cancel requested", operation_id, op.kind)
                    return True
                if op.state != "queued":
                    return False
                kind = op.kind
                repos.operations.mark_cancelled(operation_id)
        self._publish(operation_id, kind, "cancelled")
        return True

    def set_llm_limit(self, limit: int) -> None:
        """Re-cap the llm group live (Settings → Scoring, 2026-07-17). Takes
        effect on the next pump — running ops are never interrupted."""
        with self._lock:
            self._policy = with_llm_limit(self._policy, limit)
        self._pump()

    # -- boot recovery -----------------------------------------------------

    def recover(self) -> None:
        """Boot: fail orphaned `running`, leave `queued` to be re-run (NFR-LONG-02)."""
        orphaned: list[tuple[str, str]] = []
        with self._lock:
            with self._db.repos() as repos:
                for op in repos.operations.list_by_state("running"):
                    orphaned.append((op.id, op.kind))
                    repos.operations.mark_failed(op.id, error=RESTART_NOTE)
                requeued = len(repos.operations.list_by_state("queued"))
        for op_id, kind in orphaned:
            self._log.warning("boot recovery: orphaned running op %s (%s) → failed", op_id, kind)
            self._publish(op_id, kind, "failed", error=RESTART_NOTE)
        if requeued:
            self._log.info("boot recovery: %d queued operation(s) re-enqueued", requeued)
        self._pump()

    # -- scheduling core ---------------------------------------------------

    def _pump(self) -> None:
        """Dispatch as many queued ops as the concurrency policy allows."""
        if self._closing:
            return
        to_start: list[tuple[str, str, dict[str, Any]]] = []
        with self._lock:
            if self._executor is None or self._closing:
                return
            with self._db.repos() as repos:
                queued = repos.operations.list_by_state("queued")
                # Interactive kinds jump the bulk fan-out (policy.py
                # DISPATCH_PRIORITY): an apply/tailor the user is watching must
                # never sit behind dozens of queued scores. Stable sort keeps
                # FIFO within a priority band.
                queued.sort(key=lambda op: dispatch_priority(op.kind))
                running_kinds = list(self._running.values())
                for op in queued:
                    if op.id in self._running:
                        continue
                    if can_start(op.kind, running_kinds, self._policy):
                        snapshot = dict(op.input_snapshot or {})
                        to_start.append((op.id, op.kind, snapshot))
                        running_kinds.append(op.kind)
                for op_id, kind, _snap in to_start:
                    repos.operations.mark_running(op_id)
                    self._running[op_id] = kind
                    self._cancels[op_id] = Event()
            executor = self._executor
        for op_id, kind, snapshot in to_start:
            self._log.info("operation %s (%s) → running", op_id, kind)
            self._publish(op_id, kind, "running")
            future = executor.submit(self._run, op_id, kind, snapshot)
            with self._lock:
                self._futures.add(future)
            future.add_done_callback(self._forget_future)

    def _run(self, operation_id: str, kind: str, snapshot: dict[str, Any]) -> None:
        """Worker body: call the entrypoint, persist outcome + usage, publish.

        The whole execution is wrapped in a per-operation Logfire span (US-SYS-05)
        carrying id / kind / engine / model / latency / cost / outcome. A failure
        lands in all three legs — the operations row, the span, and the SSE event
        (NFR-SIDE-04). The span is the *only* new artifact; row + event are the
        pre-existing legs, kept exactly as before.
        """
        content_logging = (
            self._observability.content_logging if self._observability is not None else False
        )
        with self._lock:
            cancel_token = self._cancels.get(operation_id)
        resolved = None
        # Half-open probe lease (F-H5 follow-up, 2026-07-25): when `check`
        # admits this op as the circuit's ONE probe, the lease must be settled
        # on EVERY terminal path or the circuit wedges open forever. It is
        # *transferred* by record_success / record_failure (set back to None
        # right after) and *abandoned* in the outer finally for every other
        # outcome — cancellation, non-EngineError failures, even a crash in
        # the handlers themselves.
        probe_engine: str | None = None
        try:
            with operation_span(
                operation_id,
                kind,
                input_snapshot=snapshot,
                content_logging=content_logging,
            ) as span:
                try:
                    resolved = (
                        self._engines.resolve(kind) if self._engines is not None else None
                    )
                    if resolved is not None:
                        # Circuit breaker (F-H5): a provider that keeps failing
                        # rejects fast here instead of burning an llm slot on
                        # retries/timeouts. Raises ProviderCircuitOpen → the
                        # generic failure path lands its message in the ledger.
                        # True ⇒ this op holds the half-open probe lease.
                        if self._circuit.check(resolved.name):
                            probe_engine = resolved.name
                    ctx = OperationContext(
                        kind=kind,
                        input_snapshot=snapshot,
                        engine=resolved,
                        db=self._db,
                        operation_id=operation_id,
                        publish=self._publish_fn,
                        cancelled=cancel_token.is_set if cancel_token is not None else None,
                    )
                    entrypoint = self._registry.resolve(kind)
                    outcome = entrypoint(ctx)
                except CompletionCancelled:
                    # The user's Stop landed at a cooperative checkpoint —
                    # an outcome, not an error: no failure log, no breaker
                    # feedback, the row + event say `cancelled` (F-M7).
                    message = "cancelled by the user"
                    try:
                        record_span_failure(span, message)
                    except Exception:  # noqa: BLE001 — the span is additive; row + event must land
                        self._log.exception(
                            "span recording failed for operation %s (%s)", operation_id, kind
                        )
                    with self._db.repos() as repos:
                        repos.operations.mark_cancelled(operation_id)
                    self._log.info("operation %s (%s) → cancelled", operation_id, kind)
                    self._publish(operation_id, kind, "cancelled")
                except Exception as exc:  # noqa: BLE001 — verbatim capture is the contract
                    message = f"{type(exc).__name__}: {exc}"
                    # Only real engine failures feed the breaker — module
                    # parse drift or our own bugs say nothing about the
                    # provider, and a ProviderCircuitOpen rejection must never
                    # feed the breaker that produced it (it isn't EngineError).
                    if resolved is not None and isinstance(exc, EngineError):
                        self._circuit.record_failure(resolved.name, str(exc))
                        probe_engine = None  # lease transferred (probe verdict: still down)
                    try:
                        record_span_failure(span, message, exc)
                    except Exception:  # noqa: BLE001 — the span is additive; row + event must land
                        self._log.exception(
                            "span recording failed for operation %s (%s)", operation_id, kind
                        )
                    with self._db.repos() as repos:
                        repos.operations.mark_failed(operation_id, error=message)
                    # Full traceback into the flight recorder so a failing op is
                    # debuggable from `logs/sidecar.log` alone — the maintainer
                    # should never re-derive a crash from a screenshot. The
                    # traceback is formatted INTO the message (not passed via
                    # `exc_info=`): Logfire instruments stdlib logging, and an
                    # exc_info record contaminates the global span provider in
                    # certain test orderings. Any voyager subprocess stderr tail
                    # already rides inside `message` (the driver appends it).
                    tb = "".join(traceback.format_exception(exc))
                    self._log.error(
                        "operation %s (%s) failed: %s\n%s",
                        operation_id,
                        kind,
                        message,
                        tb,
                    )
                    self._publish(operation_id, kind, "failed", error=message)
                else:
                    if resolved is not None:
                        self._circuit.record_success(resolved.name)
                        probe_engine = None  # lease transferred (probe verdict: healthy)
                    try:
                        record_span_success(span, outcome)
                    except Exception:  # noqa: BLE001 — the span is additive; state + chain must run
                        self._log.exception(
                            "span recording failed for operation %s (%s)", operation_id, kind
                        )
                    with self._db.repos() as repos:
                        repos.operations.mark_succeeded(
                            operation_id,
                            result_ref=outcome.result_ref,
                            usage=outcome.usage,
                            engine=outcome.engine,
                            model=outcome.model,
                        )
                    self._log.info("operation %s (%s) → succeeded", operation_id, kind)
                    self._publish(
                        operation_id,
                        kind,
                        "succeeded",
                        result_ref=outcome.result_ref,
                        usage=outcome.usage,
                    )
                    if self.on_success is not None:
                        try:
                            self.on_success(operation_id, kind)
                        except Exception:  # noqa: BLE001 — a chain failure must never fail the op
                            self._log.exception(
                                "on_success chain hook failed for operation %s (%s)",
                                operation_id,
                                kind,
                            )
        finally:
            if probe_engine is not None:
                # Every terminal path that did NOT settle the lease via
                # record_success/record_failure lands here: user cancellation
                # (CompletionCancelled), non-EngineError failures (parse
                # drift, pre-engine bugs), and exceptions escaping the outcome
                # handlers. The circuit stays open; a NEW probe is admitted.
                self._circuit.abandon_probe(probe_engine)
            with self._lock:
                self._running.pop(operation_id, None)
                self._cancels.pop(operation_id, None)
            # Ledger retention (US-LOG-01 #2): keep ~5 pages of terminal ops;
            # prune older so the DB stays bounded (in-flight rows never touched).
            # `prune_ledger` folds the pruned ops' usd/tokens into the lifetime
            # cost aggregate first, so all-time spend survives retention (FR-SET-07).
            try:
                with self._db.repos() as repos:
                    repos.prune_ledger(LEDGER_RETENTION)
            except Exception:  # noqa: BLE001 — retention must never fail an op
                self._log.exception("ledger retention trim failed")
            self._pump()

    def _forget_future(self, future: Future[None]) -> None:
        with self._lock:
            self._futures.discard(future)

    # -- events ------------------------------------------------------------

    def _publish(self, operation_id: str, kind: str, state: str, **extra: Any) -> None:
        if self._publish_fn is None:
            return
        try:
            self._publish_fn(operation_event(operation_id, kind, state, **extra))
        except Exception:  # noqa: BLE001 — a dead SSE client must never fail an op
            self._log.exception("failed to publish event for operation %s", operation_id)
