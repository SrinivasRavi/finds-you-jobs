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
    DEFAULT_DISPATCH_PRIORITY,
    DEFAULT_POLICY,
    DISPATCH_PRIORITY,
    ConcurrencyPolicy,
    can_start,
    fit_to_pool,
    with_llm_limit,
    with_queue_mode,
)

RESTART_NOTE = (
    "operation was running when the sidecar restarted; marked failed on boot "
    "recovery (NFR-LONG-02)"
)

PublishFn = Callable[[dict[str, Any]], None]
OnSuccessFn = Callable[[str, str], None]  # (operation_id, kind) after a success

# Nothing deletes an operation (maintainer, 2026-08-24: "never delete
# anything"); the retention code that pruned to 250 rows is gone. A user-visible
# deletion policy is S-C22, which records what a rebuild owes, including clearing
# the FK references other rows hold (foreign_keys=ON makes a naive delete raise).

# How many queued rows one dispatch pass reads. The concurrency policy can never
# have more than roughly 10 operations in flight (llm 4, every other group 1), so
# this is 20x the ceiling: the pump always sees far more candidates than it could
# start, and a deep queue costs a bounded read instead of a full-table one.
DISPATCH_SCAN_LIMIT = 200


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
        # intended parallelism. Scoring gets what the other lanes' caps leave
        # (`fit_to_pool`, audit A19), so this is sized for every lane at its cap
        # plus the top Parallel AI calls (20). Idle threads cost nothing.
        max_workers: int = 64,
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
        # operation_id -> (kind, requested_by), in flight; the lane needs both.
        self._running: dict[str, tuple[str, str]] = {}
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
        """Stop dispatching and wait for in-flight workers. `drain_timeout` mirrors the
        10 s window (section 4.4); None waits fully. The shell force-kills after its own
        window anyway."""
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

    def submit(
        self, kind: str, input_snapshot: dict[str, Any], *, requested_by: str = "app"
    ) -> str:
        """Create a `queued` operation, publish, and pump. Returns the id now.
        `requested_by="user"` when a click created it and someone is waiting."""
        with self._db.repos() as repos:
            op = repos.operations.create(kind, input_snapshot, requested_by=requested_by)
            operation_id = op.id
        self._log.info("operation %s enqueued (kind=%s)", operation_id, kind)
        self._publish(operation_id, kind, "queued")
        self._pump()
        return operation_id

    def submit_many(self, items: list[tuple[str, dict[str, Any]]]) -> list[str]:
        """Enqueue a batch and pump once; returns the ids in order. `submit` in a loop
        was quadratic (3.21 s of blocked loop for 1,000 ops); 1 transaction and 1 pump
        remove the multiplier."""
        if not items:
            return []
        created: list[tuple[str, str]] = []
        with self._db.repos() as repos:
            for kind, snapshot in items:
                created.append((repos.operations.create(kind, snapshot).id, kind))
        for operation_id, kind in created:
            self._log.info("operation %s enqueued (kind=%s)", operation_id, kind)
            self._publish(operation_id, kind, "queued")
        self._pump()
        return [operation_id for operation_id, _ in created]

    def cancel(self, operation_id: str) -> bool:
        """Cancel an op: a queued one at once; a running one only as a cooperative
        request for kinds that poll (`CANCELLABLE_RUNNING_KINDS`), since accepting it
        for others would be a lie. A call blocked in an engine runs to its own timeout.
        True when applied or accepted; False for unknown, terminal or non-polling."""
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

    def set_queue_mode(self, mode: str) -> None:
        """Switch the AI queue choice live (queueing.md section 5); running ops finish."""
        with self._lock:
            self._policy = with_queue_mode(self._policy, mode)
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
                # Interactive kinds jump the bulk fan-out (policy.py DISPATCH_PRIORITY), so an
                # apply the user is watching never waits behind queued scores. Ordered and capped
                # in SQL; reading every queued row made enqueueing quadratic.
                queued = repos.operations.list_queued_for_dispatch(
                    priority_by_kind=DISPATCH_PRIORITY,
                    default_priority=DEFAULT_DISPATCH_PRIORITY,
                    limit=DISPATCH_SCAN_LIMIT,
                )
                running = list(self._running.values())
                policy = fit_to_pool(self._policy, self._max_workers, self._registry.kinds())
                for op in queued:
                    if op.id in self._running:
                        continue
                    # Marked running means it has a thread, never a wait in the pool's queue.
                    if len(running) >= self._max_workers:
                        break
                    if can_start(op.kind, running, policy, op.requested_by):
                        snapshot = dict(op.input_snapshot or {})
                        to_start.append((op.id, op.kind, snapshot))
                        running.append((op.kind, op.requested_by))
                        self._running[op.id] = (op.kind, op.requested_by)
                for op_id, _kind, _snap in to_start:
                    repos.operations.mark_running(op_id)
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
        """Worker body: call the entrypoint, persist outcome and usage, publish, all
        inside a per-op Logfire span (US-SYS-05). A failure lands in the row, the span
        and the SSE event (NFR-SIDE-04)."""
        content_logging = (
            self._observability.content_logging if self._observability is not None else False
        )
        with self._lock:
            cancel_token = self._cancels.get(operation_id)
        resolved = None
        service: str | None = None  # the provider this op calls; the breaker's key
        # Half-open probe lease (F-H5): when `check` admits this op as the circuit's 1
        # probe, the lease must settle on every path or the circuit wedges open.
        # record_success and record_failure transfer it; the outer finally abandons it for
        # everything else, a crash in the handlers included.
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
                        service = self._registry.service_for(
                            kind, snapshot, self._db, resolved.name
                        )
                    if service is not None:
                        # Circuit breaker (F-H5), keyed by the service the op calls,
                        # which Jev scoring makes OpenRouter whatever `score` routes
                        # to. Raises ProviderCircuitOpen; True ⇒ this op holds the
                        # half-open probe lease.
                        if self._circuit.check(service):
                            probe_engine = service
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
                    if probe_engine is not None:
                        self._circuit.abandon_probe(probe_engine)
                        probe_engine = None  # lease abandoned (no verdict)
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
                    # Only real engine failures feed the breaker: parse drift or our own bugs say
                    # nothing about the provider, and a ProviderCircuitOpen rejection mustn't feed
                    # its own breaker. Charged to the service that failed: Jev scoring calls
                    # OpenRouter whatever the kind routes to, and its spent key mustn't pause the
                    # Claude CLI.
                    failed = (
                        (getattr(exc, "provider", None) or service)
                        if isinstance(exc, EngineError)
                        else None
                    )
                    if failed is not None:
                        self._circuit.record_failure(failed, str(exc))
                        if probe_engine is not None and probe_engine != failed:
                            self._circuit.abandon_probe(probe_engine)
                        probe_engine = None  # lease settled
                    elif probe_engine is not None:
                        self._circuit.abandon_probe(probe_engine)
                        probe_engine = None  # lease abandoned (no verdict)
                    try:
                        record_span_failure(span, message, exc)
                    except Exception:  # noqa: BLE001 — the span is additive; row + event must land
                        self._log.exception(
                            "span recording failed for operation %s (%s)", operation_id, kind
                        )
                    with self._db.repos() as repos:
                        repos.operations.mark_failed(operation_id, error=message)
                    # Full traceback into the flight recorder, so a failing op is debuggable from
                    # logs/sidecar.log alone. Formatted into the message, not `exc_info=`: an
                    # exc_info record contaminates Logfire's span provider in some test orders. A
                    # voyager stderr tail already rides inside `message`.
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
                    if service is not None:
                        self._circuit.record_success(service)
                        probe_engine = None  # lease transferred (probe verdict: healthy)
                    if outcome.error:
                        # The entrypoint finished and reports the user's request didn't happen (an
                        # apply run that ended `blocked`): a failed op that keeps its spent usage,
                        # hence not raised. The engine answered, so the breaker stays as recorded.
                        try:
                            record_span_failure(span, outcome.error)
                        except Exception:  # noqa: BLE001 — the span is additive; the row must land
                            self._log.exception(
                                "span recording failed for operation %s (%s)", operation_id, kind
                            )
                        with self._db.repos() as repos:
                            repos.operations.mark_failed(
                                operation_id,
                                error=outcome.error,
                                usage=outcome.usage,
                                engine=outcome.engine,
                                model=outcome.model,
                            )
                        self._log.info(
                            "operation %s (%s) → failed: %s", operation_id, kind, outcome.error
                        )
                        self._publish(operation_id, kind, "failed", error=outcome.error)
                        return
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
                # Backstop only: every ordinary terminal path settles the
                # lease itself, BEFORE the row goes terminal, so a caller that
                # sees `succeeded`/`failed`/`cancelled` and submits again is
                # never rejected by a lease this op still holds. What reaches
                # here is an exception escaping the outcome handlers.
                self._circuit.abandon_probe(probe_engine)
            with self._lock:
                self._running.pop(operation_id, None)
                self._cancels.pop(operation_id, None)
            # No ledger retention here any more — see the note by
            # DISPATCH_SCAN_LIMIT. A completed operation is kept forever.
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
