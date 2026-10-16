"""The autopilot tick (audit-2026-10-14.md section 8.10).

AGPL-3.0-only. A scheduled op that reads the database and enqueues work as the
"app" caller, for the actions the user switched to autopilot:

- Apply: open jobs at or above the minimum score with no card get a card, the
  packet ops and an apply op, up to the daily cap.
- Outreach: each good card gets a `discover`, then a `draft` for the top K
  candidates whose channel is on, then a `send` per finished draft.

Every enqueued op still runs `autonomy.authorize` itself."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from ..db.base import now_utc
from ..db.database import Database
from ..db.models import OP_ACTIVE_STATES, OP_ALL_STATES
from ..priority import STATS_KEY, zband_priority
from .apply_op import finalize_run_failed, has_application_profile
from .operations import OperationContext, OperationOutcome

# The op runner, set at boot (`main.py`); tests set a recorder. An entrypoint
# has no runner of its own, and the autopilot's whole job is to enqueue.
RUNNER: Any = None

_DAY = timedelta(hours=24)
# LinkedIn reads per tick: a new card waits a tick or 2 for its people.
_DISCOVERS_PER_TICK = 2
_DISCOVER_LIMIT = 10
# Who to ask first: the hiring manager, then a peer, then a recruiter.
_AUDIENCE_ORDER = {"hm": 0, "peer": 1, "recruiter": 2, "leadership": 3}
# A send that ended without "sent" (a cap, a backoff, a doubt) is tried again
# after a day. The send path reads LinkedIn first after a doubt.
_RETRY_AFTER = _DAY


@dataclass(frozen=True)
class _Settings:
    apply: bool
    invite: bool
    dm: bool
    outreach_on: bool
    min_score: int
    per_job: int
    per_day: int
    thresholds: dict[str, Any]


def autopilot_entrypoint(ctx: OperationContext) -> OperationOutcome:
    if ctx.db is None:
        raise RuntimeError("autopilot requires a database context")
    if RUNNER is None:
        raise RuntimeError("autopilot needs the op runner")
    with ctx.db.repos() as repos:
        p = repos.preferences.get_or_create()
        cfg = _Settings(
            apply=p.autopilot_apply,
            invite=p.autopilot_invite,
            dm=p.autopilot_dm,
            outreach_on=p.voyager_risk_marker_on,
            min_score=p.autopilot_min_score,
            per_job=p.autopilot_contacts_per_job,
            per_day=p.autopilot_applies_per_day,
            thresholds=dict(p.thresholds or {}),
        )
    report: dict[str, Any] = {"applied": [], "discovered": [], "drafted": [], "sent": []}
    if cfg.apply:
        report["apply_skipped"] = _apply(ctx.db, cfg, report)
    if cfg.invite or cfg.dm:
        report["outreach_skipped"] = _outreach(ctx.db, cfg, report)
    return OperationOutcome(result_ref=report)


def _apply(db: Database, cfg: _Settings, report: dict[str, Any]) -> str:
    """Card, packet and apply op for each target. Returns why it stopped early."""
    from ..api.packet import auto_cover_default, auto_resume_default, enqueue_packet

    with db.repos() as repos:
        if repos.profile.get_current() is None:
            return "no master resume is saved"
        if not has_application_profile(repos):
            return "the applicant details are not confirmed yet"
        room = cfg.per_day - repos.operations.count_since("apply", "app", now_utc() - _DAY)
        if room <= 0:
            return f"the {cfg.per_day} applies for the last 24 hours are done"
        # Extra rows absorb the copies this tick skips.
        targets = [
            (j.id, j.canonical_url or "", j.score or 0)
            for j in repos.jobs.list_autopilot_targets(cfg.min_score, limit=room * 3)
        ]
    taken: set[str] = set()
    for job_id, url, score in targets:
        if len(report["applied"]) >= room:
            break
        with db.repos() as repos:
            if repos.duplicates.open_partners(job_id) & taken:
                continue
            priority = zband_priority(cfg.thresholds.get(STATS_KEY), score)
            app_id = repos.applications.create(
                job_id, column="saved", intent="apply", priority=priority
            ).id
        enqueue_packet(
            db,
            RUNNER,
            application_id=app_id,
            job_id=job_id,
            resume=auto_resume_default(cfg.thresholds),
            cover=auto_cover_default(cfg.thresholds),
            requested_by="app",
        )
        with db.repos() as repos:
            run_id = repos.apply_runs.create(
                app_id, source_url=url, status="queued", phase="queued"
            ).id
        try:
            RUNNER.submit("apply", {"application_id": app_id, "run_id": run_id}, requested_by="app")
        except Exception as exc:
            finalize_run_failed(db, run_id, f"enqueue failed: {type(exc).__name__}: {exc}")
            raise
        taken.add(job_id)
        report["applied"].append(job_id)
    return ""


def _outreach(db: Database, cfg: _Settings, report: dict[str, Any]) -> str:
    """1 step of the referral chain per good card. Returns why it did nothing."""
    if not cfg.outreach_on:
        return "Referral Outreach is off in Settings"
    with db.repos() as repos:
        targets = [
            (app.id, job.id, job.company)
            for app, job in repos.applications.list_outreach_targets(cfg.min_score)
        ]
    discovers_left = _DISCOVERS_PER_TICK
    for app_id, job_id, company in targets:
        with db.repos() as repos:
            if not repos.referral_candidates.list_for_job(job_id):
                if discovers_left and not repos.operations.list_for_job(
                    "discover", OP_ALL_STATES, job_id
                ):
                    discovers_left -= 1
                    _discover(job_id, company, report)
                continue
            _send_finished_drafts(repos, cfg, app_id, job_id, report)
            _draft_top_candidates(repos, cfg, job_id, report)
    return ""


def _discover(job_id: str, company: str, report: dict[str, Any]) -> None:
    RUNNER.submit(
        "discover",
        {"company": company, "job_id": job_id, "limit": _DISCOVER_LIMIT},
        requested_by="app",
    )
    report["discovered"].append(job_id)


def _channel_on(cfg: _Settings, is_first_degree: bool) -> bool:
    return cfg.dm if is_first_degree else cfg.invite


def _draft_top_candidates(repos: Any, cfg: _Settings, job_id: str, report: dict[str, Any]) -> None:
    """Pick up to K people per job, counting the ones already picked or reached."""
    cands = repos.referral_candidates.list_for_job(job_id)
    reached = {log.contact_id for log in repos.outreach_logs.list_for_job(job_id)}
    drafted = {
        op.contact_id for op in repos.operations.list_for_job("draft", OP_ALL_STATES, job_id)
    }
    engaged = {c.contact_id for c in cands if c.selected} | reached
    need = cfg.per_job - len(engaged)
    if need <= 0:
        return
    eligible = []
    for cand in cands:
        if cand.contact_id in engaged or cand.contact_id in drafted:
            continue
        contact = repos.contacts.get(cand.contact_id)
        if contact is None or contact.connection_status != "candidate":
            continue
        if not _channel_on(cfg, contact.is_first_degree):
            continue
        eligible.append((_AUDIENCE_ORDER.get(cand.audience_tag, 9), cand.added_at, cand))
    for _rank, _added, cand in sorted(eligible, key=lambda t: (t[0], t[1]))[:need]:
        cand.selected = True
        RUNNER.submit(
            "draft", {"contact_id": cand.contact_id, "job_id": job_id}, requested_by="app"
        )
        report["drafted"].append(cand.contact_id)


def _send_finished_drafts(
    repos: Any, cfg: _Settings, app_id: str, job_id: str, report: dict[str, Any]
) -> None:
    """Send each autopilot draft that finished, once, and again a day after a
    send that ended without "sent". A draft the user asked for is never sent."""
    sends = repos.operations.list_for_job("send", OP_ALL_STATES, job_id)
    active = {op.contact_id for op in sends if op.state in OP_ACTIVE_STATES}
    logs: dict[str, Any] = {}
    for log in sorted(repos.outreach_logs.list_for_job(job_id), key=lambda r: r.created_at):
        logs[log.contact_id] = log  # the newest wins
    tried = {op.contact_id for op in sends}
    batch_id = uuid4().hex
    for op in repos.operations.list_for_job("draft", {"succeeded"}, job_id):
        cid = op.contact_id
        message = str((op.result_ref or {}).get("message") or "")
        if op.requested_by != "app" or not message or cid in active:
            continue
        last = logs.get(cid)
        if last is not None and (
            last.outcome == "sent" or last.created_at > now_utc() - _RETRY_AFTER
        ):
            continue
        if last is None and cid in tried:
            continue  # a send ran and wrote no log yet: wait for it
        contact = repos.contacts.get(cid)
        if contact is None or not _channel_on(cfg, contact.is_first_degree):
            continue
        RUNNER.submit(
            "send",
            {
                "contact_id": cid,
                "job_id": job_id,
                "application_id": app_id,
                "batch_id": batch_id,
                "message": message,
            },
            requested_by="app",
        )
        report["sent"].append(cid)
