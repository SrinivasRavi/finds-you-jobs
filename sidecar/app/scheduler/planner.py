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

# A score op IN FLIGHT at the current version. Only these — a job is never
# double-enqueued while its score is queued or running.
#
# `failed` used to be in this set, and that was the bug. A fan-out that died on
# an expired key or exhausted tokens marked every remaining job failed within
# seconds (the circuit breaker rejects fast once open), and none of them were
# ever re-planned: topping the key up re-scored nothing, and the only way back
# was the per-row Retry in Analytics, once per job. Retries are bounded by
# `Job.score_attempts` now, which counts only failures that actually reached the
# provider, so a provider outage costs a job nothing.
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
    """Plan `("score", snapshot)` for jobs that still need an AI score.

    Scoring is always on (the old auto_score_on_scan opt-out is retired). The
    cost lever is Settings → Scoring's MODE:
    - **keyword** — nothing to plan here; the instant on-device keyword floor
      (`ensure_keyword_floor`, run on scan + boot) already scores every job.
    - **llm** — plan an LLM score for every active job that can still earn one.
      A keyword floor does NOT count as done (the LLM upgrades it); an in-flight
      op is not double-enqueued; a job that has failed `SCORE_MAX_ATTEMPTS` times
      against a live provider is dropped; and a job with no usable description
      is never offered at all (`list_active_without_llm_score`).

      A failed attempt IS re-planned, unlike before. The bound is
      `Job.score_attempts`, not the presence of a failed operation row, so a
      provider outage that failed 800 jobs in seconds costs none of them an
      attempt and all 800 come back on the next tick once the provider does.
    """
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

        # The "already AI-scored" exclusion happens in SQL (`jobs.llm_score IS
        # NOT NULL`): a resume edit never auto-spends tokens re-scoring a job
        # that has one. Paging active jobs and filtering afterwards meant an
        # install with more than 1,000 active jobs could never reach the oldest
        # unscored ones.
        #
        # `limit` 0 means uncapped, which is the default: enqueueing is cheap and
        # how much the user spends scoring is their call, not a cap we impose.
        # When it IS capped, ask for enough extra rows to absorb `attempted`, so
        # a run of in-flight jobs can't crowd out everything behind them.
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
