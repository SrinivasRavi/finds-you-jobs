"""Schedule planners (ROADMAP A4).

`plan_score_new` expands the `score_new` schedule into one `score` operation per
unscored `(job, current master version)` pair — the runner's LLM concurrency
policy (≤ 2 in flight) still bounds how many run at once. It is idempotent:
already-scored jobs (cache hit — architecture section 8) and jobs with a `score`
operation still queued/running are skipped, so a second tick never
double-enqueues. An optional per-tick batch cap is read from
`UserPreferences.thresholds["score_new_batch"]` (0 = uncapped).
"""

from __future__ import annotations

from typing import Any

from ..db import Database
from ..db.models import SCORE_MAX_ATTEMPTS
from ..registry.persistence import scoring_mode

# A score op in flight at the current version; a job is never double-enqueued.
# `failed` was once in this set: an expired key failed every remaining job in
# seconds and none were ever re-planned. Retries are bounded by
# `Job.score_attempts`, which counts only failures that reached the provider.
_IN_FLIGHT = {"queued", "running"}


def plan_schedule(db: Database, kind: str) -> list[tuple[str, dict[str, Any]]]:
    """Expand one schedule kind into the concrete operations to enqueue.

    `score_new` fans out to one `score` op per unscored job; every other kind
    (e.g. `scan`) is a single op of its own kind. Shared by the scheduler tick
    (`main.py`) and the run-now HTTP trigger so both take the same path."""
    if kind == "score_new":
        return plan_score_new(db)
    return [(kind, {})]


def plan_score_new(db: Database, *, limit: int | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Plan `("score", snapshot)` for jobs that still need an AI score. Keyword mode
    plans nothing: the floor already scores every job. AI mode plans every active
    job that can still earn one: the floor doesn't count, in-flight ops aren't
    doubled, and jobs past `SCORE_MAX_ATTEMPTS` or with no description are skipped.
    An outage costs no job an attempt, so the backlog returns next tick."""
    with db.repos() as repos:
        if repos.profile.get_current() is None:
            return []

        prefs = repos.preferences.get_or_create()
        thresholds = prefs.thresholds or {}
        if scoring_mode(prefs) == "keyword":
            return []
        if limit is None:
            raw = thresholds.get("score_new_batch", 0)
            limit = int(raw or 0)

        # Jobs whose LLM score is already in flight, so an in-flight op is never
        # double-enqueued. Reads `Operation.job_id`, a real column, rather than
        # reaching into `input_snapshot`; the profile-version half of this filter
        # went with the version dimension itself (S-C31). Built BEFORE the job
        # read so the read can ask for enough rows to survive this filter.
        attempted = {
            op.job_id
            for op in repos.operations.list_by_kind_states("score", _IN_FLIGHT)
            if op.job_id is not None
        }

        # The "already AI-scored" exclusion is SQL (`llm_score IS NOT NULL`), so a
        # resume edit never re-spends tokens, and an install past 1,000 active jobs still
        # reaches the oldest unscored ones. `limit` 0 is uncapped, the default: spend is
        # the user's call. When capped, fetch extra to absorb `attempted`.
        fetch = limit + len(attempted) if limit else None
        jobs = repos.jobs.list_active_without_llm_score(
            limit=fetch, max_attempts=SCORE_MAX_ATTEMPTS
        )

        planned: list[tuple[str, dict[str, Any]]] = []
        for job in jobs:
            if job.id in attempted:
                continue
            planned.append(("score", {"job_id": job.id}))
            if limit and len(planned) >= limit:
                break
    return planned
