"""Profile, settings, schedules and the prompts editor."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ..db.base import now_utc
from ..observability import reconfigure_observability
from ..observability.config import observability_config
from ..registry import networker_ops as networker_ops
from ..registry.engine_config import apply_routing
from ..registry.persistence import (
    scoring_mode,
)
from ..scheduler.planner import plan_schedule
from . import dto
from .deps import db as _db
from .deps import engines as _engines
from .deps import found as _found
from .deps import runner as _runner

router = APIRouter()

# -- profile ---------------------------------------------------------------


@router.get("/api/profile")
def get_profile(request: Request) -> dto.ProfileDTO | None:
    with _db(request).repos() as repos:
        profile = repos.profile.get_current()
        return dto.profile_dto(profile) if profile is not None else None


@router.post("/api/profile")
def upsert_profile(request: Request, payload: dto.ProfileUpsert) -> dto.ProfileDTO:
    with _db(request).repos() as repos:
        before = repos.profile.get_current()
        before_version = before.version if before is not None else None
        profile = repos.profile.upsert(payload.resume_markdown)
        # The first resume is all added text, so it counts as major too.
        major = profile.version != before_version and (
            before_version is None or profile.major_version == profile.version
        )
        result = dto.profile_dto(profile)
    # Always extract the application profile at master-save (FR-APP-01;
    # maintainer removed the toggle — it's one small cheap call and the record
    # is load-bearing for every form fill), unless AI is off (S-C60): then it
    # could only fail, and the next save after a provider is set up runs it.
    engines = _engines(request)
    if engines is None or engines.resolve("extract") is not None:
        _runner(request).submit("extract", {"profile_version": result.version})
    # A major edit rescores every Keyword score now, whatever the method setting:
    # it's free (in Starlette's threadpool). LLM, Jev and Laya scores only get the
    # rescore-due mark; a minor edit does nothing (score-storage.md 5.2).
    if major:
        from ..registry.operations import rescore_all_keyword

        rescore_all_keyword(_db(request))
    return result


@router.post("/api/profile/extract", status_code=202)
def extract_application_profile(request: Request) -> dto.OperationAccepted:
    """Manually (re-)extract the application profile from the current master
    (the Settings "Re-extract" button — FR-APP-01)."""
    with _db(request).repos() as repos:
        if repos.profile.get_current() is None:
            raise HTTPException(status_code=404, detail="no master profile to extract from")
    operation_id = _runner(request).submit("extract", {}, requested_by="user")
    return dto.OperationAccepted(id=operation_id, kind="extract", state="queued")


@router.patch("/api/profile/application-profile")
def patch_application_profile(
    request: Request, payload: dict[str, Any]
) -> dto.ProfileDTO:
    """Save Applicant details (Settings > Applying, or the dialog before the first
    apply). The payload replaces the stored record, stamped `source: edited` and
    `confirmed_at`: saving is the review Apply waits for (audit A5), and a later
    resume read never overwrites what was saved here."""
    from ..db.base import now_utc
    from ..registry.applicant import missing_required

    missing = missing_required(payload)
    if missing:
        raise HTTPException(
            status_code=422, detail=f"applicant details need: {', '.join(missing)}"
        )
    with _db(request).repos() as repos:
        if repos.profile.get_current() is None:
            raise HTTPException(status_code=404, detail="no master profile yet")
        record = {**payload, "source": "edited", "confirmed_at": now_utc().isoformat()}
        profile = repos.profile.set_application_profile(record)
        return dto.profile_dto(profile)


# -- settings --------------------------------------------------------------


def _scoring_methods(repos: Any) -> list[dto.ScoringMethodDTO]:
    """Every method, in menu order, with the one thing each still needs."""
    from ..registry.persistence import SCORING_MODES, decisions_key_present

    has_key = decisions_key_present(repos)
    needs: dict[str, str | None] = {
        "llm": None,
        "jev": None if has_key else "openrouter_key",
        # The pack downloader is not built yet, so this one is honestly not ready
        # on any install (scoring-build.md T10).
        "local": "model_pack",
        "keyword": None,
    }
    return [
        dto.ScoringMethodDTO(id=mode, ready=needs[mode] is None, needs=needs[mode])
        for mode in SCORING_MODES
    ]


def _settings_dto(repos: Any) -> dto.SettingsDTO:
    from ..registry.persistence import SCRAPER_ENGINE_PREFIX

    prefs = repos.preferences.get_or_create()
    engines = repos.engine_settings.list()
    return dto.SettingsDTO(
        scoring_methods=_scoring_methods(repos),
        preferences=dto.preferences_dto(prefs),
        # `scraper:*` rows are BYO scraper keys (Apify/Brave) riding the same
        # sealed store — never LLM engines; they surface via /api/discovery.
        engines=[
            dto.engine_setting_dto(e)
            for e in engines
            if not e.engine.startswith(SCRAPER_ENGINE_PREFIX)
        ],
    )


@router.get("/api/settings")
def get_settings(request: Request) -> dto.SettingsDTO:
    with _db(request).repos() as repos:
        return _settings_dto(repos)


# Background-scrape cadence ladder (US-OB-03 / US-SET-01) → scan-schedule
# interval. Threading happens HERE, server-side, so every writer (onboarding
# Finish, the job-finder-preferences modal, any future surface) enables the
# schedule for free — a collected cadence must actually enable the
# seeded-disabled scan schedule or a fresh install never background-scrapes.
_CADENCE_MINUTES: dict[str, int] = {
    "Every 6h": 360,
    "Every 12h": 720,
    "Every 24h": 1440,
    "Every 48h": 2880,
    "Every 72h": 4320,
}


def _thread_scan_cadence(repos: Any, ui_state: dict[str, Any] | None) -> None:
    """Enable and retime the `scan` schedule from a saved `scrape_cadence`.
    `next_due_at = now + interval`, never now: the writer just ran its own scan.
    `score_new` stays disabled: the scan-to-score chain already scores new jobs."""
    cadence = (ui_state or {}).get("scrape_cadence")
    minutes = _CADENCE_MINUTES.get(cadence) if isinstance(cadence, str) else None
    if minutes is None:
        return
    sched = next((s for s in repos.schedules.list_all() if s.kind == "scan"), None)
    if sched is None:
        return
    repos.schedules.update(
        sched.id,
        enabled=True,
        interval_minutes=minutes,
        next_due_at=now_utc() + timedelta(minutes=minutes),
    )


@router.post("/api/settings")
def update_settings(
    request: Request, payload: dto.PreferencesUpdate
) -> dto.SettingsDTO:
    fields = payload.model_dump(exclude_none=True)
    with _db(request).repos() as repos:
        before_mode = scoring_mode(repos.preferences.get_or_create())
        prefs = repos.preferences.update(**fields)
        routing = prefs.engine_routing
        ui_state = prefs.ui_state
        prefs_thresholds = dict(prefs.thresholds or {})
        if "ui_state" in fields:
            _thread_scan_cadence(repos, ui_state)
        result = _settings_dto(repos)
    # Switching to keyword mode scores the whole board here, about 0.5 ms per job,
    # so the change shows on the next fetch (a plain `def` runs in the threadpool).
    # Switching to AI enqueues nothing: the next tick picks up jobs with no LLM score.
    after_mode = str(prefs_thresholds.get("scoring_mode") or "llm")
    if after_mode == "keyword" and before_mode != "keyword":
        from ..registry.operations import backfill_keyword_scores

        backfill_keyword_scores(_db(request))
    # Re-apply the routing map so a Settings change takes effect immediately.
    engines = _engines(request)
    if engines is not None and "engine_routing" in fields:
        apply_routing(engines, routing)
    # LLM parallelism is re-capped live (2026-07-17): the runner swaps its
    # policy on the next pump; running ops are never interrupted.
    if "thresholds" in fields:
        from ..runner.policy import ai_queue_mode_from, llm_concurrency_from

        _runner(request).set_llm_limit(llm_concurrency_from(prefs_thresholds))
        _runner(request).set_queue_mode(ai_queue_mode_from(prefs_thresholds))
    # A6: an observability change (content logging / OTLP opt-in / retention) is
    # re-applied live — turning OTLP export ON adds the exporter, OFF removes it
    # entirely (no exporter at all — the no-network-by-default invariant).
    obs = getattr(request.app.state, "observability", None)
    if obs is not None and "ui_state" in fields:
        cfg = observability_config(ui_state)
        reconfigure_observability(
            obs,
            obs.span_db_path.parent,  # the data dir (where logfire.sqlite lives)
            content_logging=cfg.content_logging,
            otlp_enabled=cfg.otlp_enabled,
            otlp_endpoint=cfg.otlp_endpoint,
            otlp_headers=cfg.otlp_headers,
            retention_days=cfg.retention_days,
        )
    return result


@router.put("/api/settings")
def replace_settings(
    request: Request, payload: dto.PreferencesUpdate
) -> dto.SettingsDTO:
    """PUT is an alias of POST for the settings map (idempotent update)."""
    return update_settings(request, payload)


# -- schedules -------------------------------------------------------------


@router.get("/api/schedules")
def list_schedules(request: Request) -> list[dto.ScheduleDTO]:
    """The recurring-enqueue rules (scan / score_new). Seeded disabled (section 7 seed)."""
    with _db(request).repos() as repos:
        return [dto.schedule_dto(s) for s in repos.schedules.list_all()]


@router.patch("/api/schedules/{schedule_id}")
def update_schedule(
    request: Request, schedule_id: str, payload: dto.ScheduleUpdate
) -> dto.ScheduleDTO:
    """Enable/disable a schedule or change its cadence. Enabling a seeded-
    disabled schedule makes it due on the next tick (next_due_at → now)."""
    fields = payload.model_dump(exclude_none=True)
    with _db(request).repos() as repos:
        sched = _found(repos.schedules.get(schedule_id), "schedule", schedule_id)
        # Flip on → run promptly (the seeded next_due is far-future, section 7 seed).
        if fields.get("enabled") is True and not sched.enabled:
            fields["next_due_at"] = now_utc()
        updated = repos.schedules.update(schedule_id, **fields)
        return dto.schedule_dto(updated)


@router.post("/api/schedules/{schedule_id}/run", status_code=202)
def run_schedule(request: Request, schedule_id: str) -> dto.ScheduleRunResult:
    """Run a schedule now, regardless of enabled/due — the explicit user trigger
    (score_new fans out to a `score` op per unscored job; scan enqueues one scan).
    Idempotent for score_new: the planner skips already-scored + pending jobs."""
    db = _db(request)
    runner = _runner(request)
    with db.repos() as repos:
        sched = _found(repos.schedules.get(schedule_id), "schedule", schedule_id)
        kind = sched.kind
        interval_minutes = sched.interval_minutes

    planned = plan_schedule(db, kind)
    # Batched for the same reason the scheduler's own tick is: submitting in a
    # loop pumps the queue once per operation, and every pump used to re-read the
    # whole queue. "Run now" on a full board is the interactive version of it.
    enqueued = runner.submit_many(planned)

    next_due = now_utc() + timedelta(minutes=interval_minutes)
    with db.repos() as repos:
        repos.schedules.mark_enqueued(
            schedule_id,
            operation_id=enqueued[-1] if enqueued else None,
            next_due_at=next_due,
        )
        # The re-read is unreachable-None — same txn — but keeps the type honest.
        updated = _found(repos.schedules.get(schedule_id), "schedule", schedule_id)
        return dto.ScheduleRunResult(schedule=dto.schedule_dto(updated), enqueued=enqueued)


def _prompts_data_dir(request: Request) -> Path:
    return getattr(request.app.state, "data_dir", None) or Path()


@router.get("/api/settings/prompts")
def list_prompts(request: Request) -> list[dto.PromptDTO]:
    """Every editable prompt with its default + current override (US-SET-12)."""
    from ..prompt_overrides import list_prompts as _list

    return [dto.PromptDTO(**row) for row in _list(_prompts_data_dir(request))]


@router.put("/api/settings/prompts/{kind}")
def set_prompt(
    request: Request, kind: str, payload: dto.PromptUpdate
) -> dto.PromptDTO:
    """Save an override for `kind` (404 unknown kind, 422 empty markdown)."""
    from ..prompt_overrides import (
        PROMPT_KINDS,
        default_md,
        get_override,
        set_override,
    )

    spec = PROMPT_KINDS.get(kind)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown prompt kind {kind!r}")
    if not payload.markdown.strip():
        raise HTTPException(status_code=422, detail="prompt markdown cannot be empty")
    data_dir = _prompts_data_dir(request)
    set_override(kind, payload.markdown, data_dir)
    return dto.PromptDTO(
        kind=spec.kind, title=spec.title, op_kind=spec.operation_kind(),
        default_md=default_md(kind), override_md=get_override(kind, data_dir),
    )


# The kinds that need a job to run against.
_PROMPT_TEST_NEEDS_JOB = frozenset({"score", "tailor", "cover"})


@router.post("/api/settings/prompts/{kind}/test", status_code=202)
def test_prompt(
    request: Request, kind: str, payload: dto.PromptTestRequest
) -> dto.OperationAccepted:
    """Run a prompt draft once without committing it (url-less-jobs.md section 11).
    The draft rides the op's snapshot and the run writes nothing (no score, artifact
    or profile); its output comes back on the op's own result."""
    from ..prompt_overrides import PROMPT_KINDS

    spec = PROMPT_KINDS.get(kind)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown prompt kind {kind!r}")
    if not payload.markdown.strip():
        raise HTTPException(status_code=422, detail="prompt markdown cannot be empty")

    snap: dict[str, Any] = {"prompt_draft_md": payload.markdown, "prompt_test": True}
    if kind == "score":
        # Only LLM scoring reads this prompt. Without the override a board on
        # Jev or Keyword scoring ran that method and never sent the draft.
        snap["force_mode"] = "llm"
    if payload.guidance:
        snap["guidance"] = payload.guidance

    with _db(request).repos() as repos:
        if kind in _PROMPT_TEST_NEEDS_JOB:
            if payload.job_id:
                job = _found(repos.jobs.get(payload.job_id), "job", payload.job_id)
                from ..registry.persistence import compose_job_text

                snap["job"] = compose_job_text(job)
            elif payload.job_description and payload.job_description.strip():
                snap["job"] = payload.job_description
            else:
                raise HTTPException(
                    status_code=422,
                    detail="pick a job from the board or paste a job description",
                )
            # Deliberately NO job_id in the snapshot: that is what stops the
            # score path persisting onto the real job row.
            profile = repos.profile.get_current()
            if profile is None:
                raise HTTPException(status_code=400, detail="no master resume to run against")
            snap["master_md"] = profile.resume_markdown
            snap["profile_version"] = profile.version
        elif kind == "networker_draft":
            if not payload.contact_id:
                raise HTTPException(status_code=422, detail="pick a contact to draft for")
            _found(repos.contacts.get(payload.contact_id), "contact", payload.contact_id)
            snap["contact_id"] = payload.contact_id
            if payload.job_id:
                snap["job_id"] = payload.job_id
        else:
            if repos.profile.get_current() is None:
                raise HTTPException(status_code=400, detail="no master resume to run against")

    # A test run is the prompt's own operation, not a new kind, so cost, the LLM
    # cap, Stop and Retry come for free and its spend compares with a real run's
    # (url-less-jobs.md section 11.4).
    op_kind = spec.operation_kind()
    operation_id = _runner(request).submit(op_kind, snap, requested_by="user")
    return dto.OperationAccepted(id=operation_id, kind=op_kind, state="queued")


@router.delete("/api/settings/prompts/{kind}")
def reset_prompt(request: Request, kind: str) -> dto.PromptDTO:
    """Reset `kind` to its shipped default (delete the override file)."""
    from ..prompt_overrides import PROMPT_KINDS, default_md, reset

    spec = PROMPT_KINDS.get(kind)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown prompt kind {kind!r}")
    data_dir = _prompts_data_dir(request)
    reset(kind, data_dir)
    return dto.PromptDTO(
        kind=spec.kind, title=spec.title, op_kind=spec.operation_kind(),
        default_md=default_md(kind), override_md=None,
    )
