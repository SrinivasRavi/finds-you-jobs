"""Schedule planners (ROADMAP A4).

`plan_score_new` expands the `score_new` schedule into one `score` operation per
unscored `(job, current master version)` pair — the runner's LLM concurrency
policy (≤ 2 in flight) still bounds how many run at once. It is idempotent:
already-scored jobs (cache hit — architecture §8) and jobs with a `score`
operation still queued/running are skipped, so a second tick never
double-enqueues. An optional per-tick batch cap is read from
`UserPreferences.thresholds["score_new_batch"]` (0 = uncapped).
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..registry.persistence import SCORER_IMPL

_PENDING = {"queued", "running"}


def plan_schedule(db: Database, kind: str) -> list[tuple[str, dict[str, Any]]]:
    """Expand one schedule kind into the concrete operations to enqueue.

    `score_new` fans out to one `score` op per unscored job; every other kind
    (e.g. `scan`) is a single op of its own kind. Shared by the scheduler tick
    (`main.py`) and the run-now HTTP trigger so both take the same path."""
    if kind == "score_new":
        return plan_score_new(db)
    return [(kind, {})]


def plan_score_new(db: Database, *, limit: int | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Return `("score", snapshot)` for each unscored, non-pending active job."""
    with db.repos() as repos:
        profile = repos.profile.get_current()
        if profile is None:
            return []
        version = profile.version

        if limit is None:
            prefs = repos.preferences.get_or_create()
            raw = prefs.thresholds.get("score_new_batch", 0) if prefs.thresholds else 0
            limit = int(raw or 0)

        jobs = repos.jobs.list(feed_state="active", limit=1000)
        scored = repos.job_scores.scored_job_ids(version, SCORER_IMPL)
        pending_ops = repos.operations.list_by_kind_states("score", _PENDING)
        pending_job_ids = {
            op.input_snapshot.get("job_id")
            for op in pending_ops
            if isinstance(op.input_snapshot, dict)
        }

        planned: list[tuple[str, dict[str, Any]]] = []
        for job in jobs:
            if job.id in scored or job.id in pending_job_ids:
                continue
            planned.append(("score", {"job_id": job.id, "profile_version": version}))
            if limit and len(planned) >= limit:
                break
    return planned
