"""Scheduler tick (architecture section 5.5, NFR-LONG-01).

`tick_once` is the pure-ish unit of work (sync, testable): find due schedules,
skip any whose last operation is still pending (double-enqueue guard), enqueue
the rest through the runner, and advance `next_due_at`. `run_forever` wraps it
in a 60 s loop with a boot catch-up. Enqueuing goes through the *same* runner as
every other operation — no parallel path.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from ..db import Database
from ..db.base import now_utc
from ..db.models import OP_ACTIVE_STATES
from ..events import scheduler_event
from ..logging_setup import get_logger
from ..runner import OperationRunner

TICK_INTERVAL_SECONDS = 60.0

SnapshotBuilder = Callable[[str], dict[str, Any]]
# A planner maps one due schedule kind to the concrete operations to enqueue.
# The default is one op of the schedule's own kind; `score_new` fans out to a
# `score` op per unscored job (the runner's LLM concurrency still bounds them).
Planner = Callable[[str], list[tuple[str, dict[str, Any]]]]
PublishFn = Callable[[dict[str, Any]], None]


class Scheduler:
    def __init__(
        self,
        db: Database,
        runner: OperationRunner,
        *,
        snapshot_builder: SnapshotBuilder | None = None,
        planner: Planner | None = None,
        publish: PublishFn | None = None,
        interval_seconds: float = TICK_INTERVAL_SECONDS,
    ) -> None:
        self._db = db
        self._runner = runner
        self._build_snapshot = snapshot_builder or (lambda _kind: {})
        self._planner = planner or (lambda kind: [(kind, self._build_snapshot(kind))])
        self._publish_fn = publish
        self._interval = interval_seconds
        self._stopped = False
        self._log = get_logger()

    def tick_once(self, *, now: datetime | None = None) -> list[str]:
        """Enqueue every due schedule (guarded). Returns the operation ids created."""
        now = now or now_utc()
        with self._db.repos() as repos:
            due = [
                (s.id, s.kind, s.interval_minutes, s.last_enqueued_operation_id)
                for s in repos.schedules.list_due(now)
            ]

        enqueued: list[str] = []
        for schedule_id, kind, interval_minutes, last_op_id in due:
            if self._is_pending(last_op_id):
                self._log.info(
                    "scheduler: schedule %s (%s) skipped — prior op still pending",
                    schedule_id,
                    kind,
                )
                self._publish(schedule_id, kind, "skipped-pending")
                # Still advance next_due so it doesn't hot-loop every tick.
                self._advance(schedule_id, now, interval_minutes)
                continue

            planned = self._planner(kind)
            next_due = now + timedelta(minutes=interval_minutes)
            # One batch, one dispatch pass. Submitting in a loop pumped the queue
            # once per operation, and each pump re-read every queued row, so a
            # `score_new` fan-out cost quadratic work on the serving loop.
            operation_ids = self._runner.submit_many(planned)
            for (op_kind, _snapshot), operation_id in zip(planned, operation_ids, strict=True):
                last_op_id = operation_id
                self._log.info(
                    "scheduler: schedule %s (%s) enqueued %s op %s",
                    schedule_id, kind, op_kind, operation_id,
                )
                self._publish(schedule_id, kind, "enqueued", operation_id=operation_id)
                enqueued.append(operation_id)
            with self._db.repos() as repos:
                repos.schedules.mark_enqueued(
                    schedule_id, operation_id=last_op_id, next_due_at=next_due
                )
        return enqueued

    async def run_forever(self) -> None:
        """Boot catch-up (NFR-LONG-01) then tick every `interval` until stopped."""
        self._stopped = False
        self._log.info("scheduler: boot catch-up")
        # Every tick runs on a worker thread. This is a task on the serving loop
        # and the whole tick below it is synchronous DB work, so a slow one used
        # to block `/healthz`; the shell polls it on a 2 s timeout and restarts
        # the sidecar on a miss. The boot catch-up is the worst case, since it
        # plans the entire backlog at once.
        await asyncio.to_thread(self._safe_tick)
        while not self._stopped:
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            if self._stopped:
                break
            await asyncio.to_thread(self._safe_tick)

    def stop(self) -> None:
        self._stopped = True

    # -- internals ---------------------------------------------------------

    def _safe_tick(self) -> None:
        try:
            self.tick_once()
        except Exception:  # noqa: BLE001 — one bad tick must not kill the loop
            self._log.exception("scheduler: tick failed")

    def _is_pending(self, last_op_id: str | None) -> bool:
        if last_op_id is None:
            return False
        with self._db.repos() as repos:
            op = repos.operations.get(last_op_id)
        # Active means "the previous enqueue hasn't resolved yet" — the guard.
        return op is not None and op.state in OP_ACTIVE_STATES

    def _advance(self, schedule_id: str, now: datetime, interval_minutes: int) -> None:
        next_due = now + timedelta(minutes=interval_minutes)
        with self._db.repos() as repos:
            sched = repos.schedules.get(schedule_id)
            if sched is not None:
                sched.next_due_at = next_due

    def _publish(self, schedule_id: str, kind: str, action: str, **extra: Any) -> None:
        if self._publish_fn is None:
            return
        try:
            self._publish_fn(scheduler_event(schedule_id, kind, action, **extra))
        except Exception:  # noqa: BLE001
            self._log.exception("scheduler: failed to publish event")
