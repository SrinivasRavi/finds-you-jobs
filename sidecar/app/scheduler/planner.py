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

# A score op already attempted at the current version: queued/running (in
# flight) or failed (a failure is NOT auto-retried — it fell back to a grey
# keyword score; retry is manual via Analytics → Logs). Used to keep the AI
# planner from re-enqueuing either.
_ATTEMPTED = {"queued", "running", "failed"}


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
    - **llm** — plan an LLM score for every active job WITHOUT a current-version
      AI score and WITHOUT an already-attempted score op at this version. A
      keyword floor does NOT count as done (the LLM upgrades it); a failed
      attempt is NOT auto-retried; an in-flight op is not double-enqueued.
    """
    with db.repos() as repos:
        profile = repos.profile.get_current()
        if profile is None:
            return []
        version = profile.version

        prefs = repos.preferences.get_or_create()
        thresholds = prefs.thresholds or {}
        if str(thresholds.get("scoring_mode") or "llm") == "keyword":
            return []
        if limit is None:
            raw = thresholds.get("score_new_batch", 0)
            limit = int(raw or 0)

        jobs = repos.jobs.list(feed_state="active", limit=1000)
        scored = repos.job_scores.scored_job_ids(version, SCORER_IMPL)
        # Jobs whose LLM score is in flight or already failed at THIS version —
        # excluded so a failure isn't auto-retried every scan and an in-flight
        # op isn't double-enqueued.
        attempted: set[str] = set()
        for op in repos.operations.list_by_kind_states("score", _ATTEMPTED):
            snap = op.input_snapshot
            if isinstance(snap, dict) and snap.get("profile_version") == version:
                jid = snap.get("job_id")
                if jid is not None:
                    attempted.add(jid)

        planned: list[tuple[str, dict[str, Any]]] = []
        for job in jobs:
            if job.id in scored or job.id in attempted:
                continue
            planned.append(("score", {"job_id": job.id, "profile_version": version}))
            if limit and len(planned) >= limit:
                break
    return planned
