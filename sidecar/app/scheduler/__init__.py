"""The hand-rolled scheduler (architecture §5.5).

A `schedules` table + a 60 s async tick that enqueues due kinds through the
runner. Catch-up at boot enqueues anything past-due (NFR-LONG-01); a
double-enqueue guard (`last_enqueued_operation_id`) skips a schedule whose
previous operation is still queued/running.
"""

from __future__ import annotations

from .scheduler import TICK_INTERVAL_SECONDS, Scheduler

__all__ = ["TICK_INTERVAL_SECONDS", "Scheduler"]
