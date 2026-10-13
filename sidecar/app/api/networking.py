"""Networking routes: the contacts kanban, the find-referrals popup, the
LinkedIn session and the browser view.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Body, HTTPException, Request

from ..db.base import now_utc
from ..db.models import OP_ACTIVE_STATES
from ..logging_setup import get_logger
from ..registry import networker_ops as networker_ops
from ..registry.company_anchor import resolution_key
from ..registry.contact_sync_op import payload_with_status_meta
from ..registry.linkedin_op import (
    LOGIN_CONTROL,
    SEARCH_CURSOR_EXHAUSTED,
    SEARCH_CURSOR_EXPIRED,
    SEARCH_NO_CURSOR,
    SEARCH_NOT_CONNECTED,
)
from ..registry.networker_ops import linkedin_feature_flags, linkedin_storage_path
from . import dto
from .deps import db as _db
from .deps import found as _found
from .deps import runner as _runner

router = APIRouter()

# -- applications: networking tab -------------------------------------------

@router.get("/api/applications/{application_id}/networking")
def application_networking(
    request: Request, application_id: str
) -> list[dto.NetworkingContactDTO]:
    """The referral contacts linked to this role + their statuses — the detail
    modal's Networking tab (US-TR-03, shown only when LinkedIn is ON)."""
    with _db(request).repos() as repos:
        app = _found(repos.applications.get(application_id), "application", application_id)
        # Latest outreach per contact for this role.
        last_by_contact: dict[str, Any] = {}
        for log in repos.outreach_logs.list_for_job(app.job_id):
            last_by_contact[log.contact_id] = log  # list is created_at-ordered
        out: list[dto.NetworkingContactDTO] = []
        for assoc in repos.referral_candidates.list_for_job(app.job_id):
            contact = repos.contacts.get(assoc.contact_id)
            if contact is None:
                continue
            log = last_by_contact.get(contact.id)
            out.append(
                dto.NetworkingContactDTO(
                    contact_id=contact.id,
                    name=contact.name,
                    role=contact.current_role,
                    company=contact.current_company,
                    linkedin_url=contact.linkedin_url,
                    connection_status=contact.connection_status,
                    audience_tag=contact.audience_tag,
                    last_message=log.body_sent if log is not None else None,
                    last_message_at=(log.sent_at or log.created_at) if log is not None else None,
                    last_outcome=log.outcome if log is not None else None,
                )
            )
    return out




# -- networking: contacts kanban (US-NW-01/02/03/07) ------------------------

def _contact_dto(repos: Any, contact: Any) -> dto.ContactDTO:
    logs = repos.outreach_logs.list_for_contact(contact.id)
    return dto.contact_dto(contact, logs[-1] if logs else None)


def _contact_dtos(repos: Any, contacts: list[Any]) -> list[dto.ContactDTO]:
    """The same DTO as `_contact_dto`, for a whole roster, with ONE outreach-log
    query for the page instead of one per contact (S-C8, the F-H2 batch
    pattern). `test_hotpath_perf.py` pins both the flat query count and the
    parity with the per-row build the create/update routes still use."""
    latest = repos.outreach_logs.latest_for_contacts([c.id for c in contacts])
    return [dto.contact_dto(c, latest.get(c.id)) for c in contacts]


@router.get("/api/contacts")
def list_contacts(
    request: Request,
    company: str | None = None,
    include_candidates: bool = False,
    archived: bool = False,
) -> list[dto.ContactDTO]:
    """The networking kanban roster (US-NW-01). Excludes archived and, by
    default, `candidate` rows (discovered-but-not-reached — off the kanban).
    `archived=true` flips it to the "Deleted Contacts" recovery view: only the
    archived rows, so a user can restore a contact they removed.

    Bounded, batched, and off the event loop (S-C8): a plain `def` handler
    runs in Starlette's threadpool automatically. It used to read every
    contact row with no LIMIT and then run one outreach-log query per row, so a
    roster grown by discovery (one `candidate` per person found, at 10 people a
    company) cost 1 + N queries on the loop. Both filters now live in the repo
    query so the cap bounds the population the caller actually wants."""
    with _db(request).repos() as repos:
        contacts = repos.contacts.list(
            company=company,
            archived_only=archived,
            # The recovery view lists every deleted row, candidates
            # included: the candidate filter only ever ran on the kanban
            # branch, and dropping them here would hide a deleted contact
            # from the one view that can restore it.
            include_candidates=include_candidates or archived,
        )
        return _contact_dtos(repos, contacts)


@router.post("/api/contacts", status_code=201)
def create_contact(request: Request, payload: dto.ContactCreate) -> dto.ContactDTO:
    """Manual add-a-contact by URL/name (US-NW-02) — the rank-don't-gate escape
    hatch. Always available regardless of LinkedIn state. Dedups on linkedin_url.

    Re-adding a URL that belongs to an *archived* (deleted) contact restores it
    to the kanban rather than silently returning a still-hidden row — the same
    "put it back" semantics as un-trashing a job (2026-07-10 re-add fix). Its
    prior outreach history is preserved; only the requested live column is set."""
    with _db(request).repos() as repos:
        existing = repos.contacts.get_by_url(payload.linkedin_url)
        if existing is not None:
            if existing.archived_at is not None:
                existing = repos.contacts.update(
                    existing.id,
                    archived_at=None,
                    connection_status=payload.connection_status,
                    last_touched_at=now_utc(),
                    sent_at=(
                        now_utc()
                        if payload.connection_status == "sent" and existing.sent_at is None
                        else existing.sent_at
                    ),
                )
            return _contact_dto(repos, existing)
        contact = repos.contacts.create(
            payload.linkedin_url,
            name=payload.name,
            current_company=payload.current_company,
            current_role=payload.current_role,
            connection_status=payload.connection_status,
            audience_tag=payload.audience_tag,
            sent_at=now_utc() if payload.connection_status == "sent" else None,
        )
        return _contact_dto(repos, contact)


@router.patch("/api/contacts/{contact_id}")
def update_contact(
    request: Request, contact_id: str, payload: dto.ContactUpdate
) -> dto.ContactDTO:
    """Move a contact between kanban columns (US-NW-07) / archive / re-tag."""
    fields = payload.model_dump(exclude_none=True)
    if "archived" in fields:
        fields["archived_at"] = now_utc() if fields.pop("archived") else None
    with _db(request).repos() as repos:
        existing = _found(repos.contacts.get(contact_id), "contact", contact_id)
        # Manual wins (US-NW-12): a user-driven column move stamps the status as
        # `manual` so the contact-status sync engine won't immediately override it.
        if "connection_status" in fields:
            fields["profile_payload"] = payload_with_status_meta(existing, "manual", now_utc())
        contact = repos.contacts.update(contact_id, **fields)
        return _contact_dto(repos, contact)


# -- networking: find-referrals popup (US-NW-09 / US-REF-*) -----------------


def _require_networking_enabled(repos: Any) -> None:
    """Defense-in-depth server-side gate (audit P2-4): Referral Outreach is the
    experimental, account-risk automation — the Settings UI already gates it
    behind the toggle + ack, but a client that skips the UI (a stray call, a
    future non-web client) must not be able to trigger discovery or sends while
    the toggle is off. Mirrors `prefs.voyager_risk_marker_on`, the same flag the
    session/quota endpoints already read."""
    prefs = repos.preferences.get_or_create()
    if not bool(prefs.voyager_risk_marker_on):
        raise HTTPException(
            status_code=403,
            detail="Referral Outreach is disabled — enable it in Settings first.",
        )


def _require_any_linkedin_feature(repos: Any) -> None:
    """Gate for the shared-session lifecycle routes (connect / resume).

    These were ungated (posture doc section 4 #8): anything reaching the sidecar could
    open a real browser at linkedin.com, or clear the 24 h rate-limit backoff,
    with both features switched off. The session exists only to serve one of the
    two opt-ins, so at least one must be on."""
    referral_on, search_on = linkedin_feature_flags(repos)
    if not (referral_on or search_on):
        raise HTTPException(
            status_code=403,
            detail=(
                "No LinkedIn feature is enabled — turn on Referral Outreach or "
                "LinkedIn job search in Settings first."
            ),
        )


def _require_job_search_opt_in(repos: Any) -> None:
    """The logged-in job search has its OWN toggle and its OWN typed ack —
    first-class preference columns since 2026-08-02 (they used to live in the
    free-form `ui_state` blob, where a frontend key rename could silently flip
    a safety gate; posture doc section 4 #8 history)."""
    _, search_on = linkedin_feature_flags(repos)
    if not search_on:
        raise HTTPException(
            status_code=403,
            detail=(
                "LinkedIn job search is disabled — enable it in "
                "Settings → Discover jobs first."
            ),
        )
    if repos.preferences.get_or_create().linkedin_search_ack_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "LinkedIn job search needs its risk acknowledgement — re-enable "
                "it in Settings → Discover jobs to record it."
            ),
        )


@router.get("/api/jobs/{job_id}/referrals/candidates")
def list_referral_candidates(
    request: Request, job_id: str
) -> dto.ReferralCandidatesDTO:
    """The find-referrals popup candidate list for one role (US-NW-09). Contacts
    at the job's company + per-contact template drafts + already-reached derived
    from the OutreachLog. Run discover first to populate candidates."""
    with _db(request).repos() as repos:
        job = _found(repos.jobs.get(job_id), "job", job_id)
        company = job.company
        # Roster = contacts at the target company (raw ATS name AND the resolved
        # LinkedIn entity name — they differ, e.g. `hopper` vs `Hopper`) OR already
        # linked to this job. Matching only the raw `job.company` string exact-case
        # hid discovered rosters once the `or company` mask was removed (FR-NW-02).
        company_names = {company}
        resolved = repos.company_resolutions.get(
            resolution_key(job.canonical_url or "", job.source_adapter, company)
        )
        if resolved is not None and resolved.company_name:
            company_names.add(resolved.company_name)
        assoc_ids = {a.contact_id for a in repos.referral_candidates.list_for_job(job_id)}
        contacts = repos.contacts.list_for_referrals(
            company_names=company_names, contact_ids=assoc_ids
        )
        reached_ids = {
            log.contact_id
            for log in repos.outreach_logs.list_for_job(job_id)
            if log.outcome == "sent"
        }
        # Contacts with a send op live RIGHT NOW (queued or running) — the
        # popup's per-row Sending state must survive a close/reopen (2026-08-16:
        # it used to live only in modal state, so leaving and coming back showed
        # Connect again while the Messenger was still mid-send).
        in_flight_ids = sorted({
            str((op.input_snapshot or {}).get("contact_id") or "")
            for op in repos.operations.list_by_kind_states(
                "send", ("queued", "running")
            )
        } - {""})
        # Persisted selection (FR-NW-01): restores which contacts the user picked
        # so a reopened `pending` popup shows the selection, not just the roster.
        selected_ids = repos.referral_candidates.selected_contact_ids(job_id)
        candidates = [
            dto.referral_candidate_dto(
                c,
                already_reached=c.id in reached_ids,
                already_selected=c.id in selected_ids,
            )
            for c in contacts
        ]
        # Recover the last discover's outcome (2026-07-17) so a Save-triggered
        # background discover that needed company confirmation — or found
        # nobody — doesn't reopen as a blank start screen.
        discover_state = "never"
        company_confirm: list[dict[str, Any]] = []
        confirm_url_failed = False
        refusal_reason = ""
        discover_ops = repos.operations.list_for_job(
            "discover", {"succeeded"}, job_id
        )
        if discover_ops:
            latest = max(discover_ops, key=lambda op: op.created_at)
            ref = latest.result_ref or {}
            if ref.get("needs_company_confirm"):
                discover_state = "confirm"
                company_confirm = list(ref.get("candidates") or [])
                confirm_url_failed = bool(ref.get("url_failed"))
            elif ref.get("refused") and not candidates:
                # Voyager refused the read (cap/backoff) and no earlier roster
                # exists — honest state, not `empty` ("nobody works here").
                discover_state = "refused"
                refusal_reason = str(ref.get("reason") or "")
            elif candidates:
                discover_state = "found"
            else:
                discover_state = "empty"
        elif candidates:
            discover_state = "found"
    # 1st → 2nd → 3rd degree ordering (US-NW-09 sort).
    candidates.sort(key=lambda c: (c.degree if c.degree is not None else 99))
    return dto.ReferralCandidatesDTO(
        job_id=job_id, company=company, candidates=candidates,
        discover_state=discover_state, company_confirm=company_confirm,
        confirm_url_failed=confirm_url_failed,
        refusal_reason=refusal_reason,
        already_reached_count=len(reached_ids),
        in_flight_contact_ids=in_flight_ids,
    )


@router.post("/api/jobs/{job_id}/referrals/discover", status_code=202)
def discover_referrals(
    request: Request,
    job_id: str,
    payload: dto.DiscoverReferralsRequest | None = None,
) -> dto.OperationAccepted:
    """Kick off referral discovery for a job's company (US-REF-01 / FR-NW-02).
    Enqueues a `discover` op; live progress streams as `networker` SSE events for
    the popup. `limit` is how many candidates to pull — the "find 10 more" /
    `Load more` control bumps it (10 → 20 → …) so voyager returns a larger roster
    that merges into the shared pool.

    The op first resolves the company name to a LinkedIn company entity (URN) and
    scopes the People search by it — current-employees-only, no name collisions.
    When that resolution is ambiguous the op emits a `needs_company_confirm`
    event instead of discovering; the popup then re-calls this with the user's
    chosen `company_urn` (+ name/vanity/industry), which is cached and used."""
    payload = payload or dto.DiscoverReferralsRequest()
    is_confirm = bool(payload.company_urn or payload.company_url)
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
        job = _found(repos.jobs.get(job_id), "job", job_id)
        company = job.company
        # Single-flight the plain boot discover per job (NFR-LI account safety +
        # the confirm→ask-again loop fix): if an un-confirmed discover for this job
        # is already queued/running, reuse it rather than launching a second live
        # LinkedIn scan. A confirm (URN/URL) always runs — it supersedes the boot.
        if not is_confirm:
            for op in repos.operations.list_for_job(
                "discover", OP_ACTIVE_STATES, job_id
            ):
                snap = op.input_snapshot or {}
                if not (snap.get("company_urn") or snap.get("company_url")):
                    return dto.OperationAccepted(id=op.id, kind="discover", state=op.state)
    snapshot: dict[str, Any] = {
        "company": company, "job_id": job_id, "limit": max(1, payload.limit),
        "page": max(1, payload.page),
        # The explicit user-initiated marker the presence gate reads (invariant
        # 3): this discover was triggered by a human clicking, never a timer.
        "user_initiated": True,
    }
    if payload.company_urn:
        snapshot["company_urn"] = payload.company_urn
        snapshot["company_name"] = payload.company_name or company
        snapshot["company_vanity"] = payload.company_vanity or ""
        snapshot["company_industry"] = payload.company_industry or ""
    if payload.company_url:
        snapshot["company_url"] = payload.company_url
    operation_id = _runner(request).submit("discover", snapshot)
    return dto.OperationAccepted(id=operation_id, kind="discover", state="queued")


@router.post("/api/contacts/{contact_id}/draft", status_code=202)
def draft_referral(
    request: Request, contact_id: str, job_id: Annotated[str | None, Body(embed=True)] = None
) -> dto.OperationAccepted:
    """Grounded LLM rewrite of a contact's referral draft (US-REF-03 Regenerate).
    Enqueues a `draft` op; read the message from the operation's result_ref."""
    with _db(request).repos() as repos:
        _found(repos.contacts.get(contact_id), "contact", contact_id)
    operation_id = _runner(request).submit(
        "draft", {"contact_id": contact_id, "job_id": job_id}
    )
    return dto.OperationAccepted(id=operation_id, kind="draft", state="queued")


@router.post("/api/referrals/reach-out", status_code=202)
def reach_out(request: Request, payload: dto.ReachOutRequest) -> dto.ReachOutResult:
    """Batch reach-out (US-NW-09). Enqueues one single-flight `send` op per
    selected contact — each carrying its own per-audience message. The per-action
    confirmation lives in the UI; the send path runs only when the master toggle
    is on (the UI gates it, and `_require_networking_enabled` gates it server-side
    too — audit P2-4; a dry-run plans without touching LinkedIn)."""
    runner = _runner(request)
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
        for c in payload.contacts:
            _found(repos.contacts.get(c.contact_id), "contact", c.contact_id)
        # Persist the selection (FR-NW-01): mark every picked contact selected for
        # this role so a `pending` popup (partial / cap-stopped batch) restores who
        # was chosen on reopen. Un-picked contacts keep their prior flag — un-sent
        # picks from an earlier batch stay selected so the user can retry them.
        if payload.job_id:
            for c in payload.contacts:
                assoc = repos.referral_candidates.get(c.contact_id, payload.job_id)
                if assoc is not None:
                    assoc.selected = True
                else:
                    repos.referral_candidates.upsert(
                        c.contact_id, payload.job_id, selected=True
                    )
        # Idempotency guard (US-NW-09): a repeated "Send now" (double-click / retry)
        # must not enqueue a second real invite for a contact whose send for this
        # role is already queued or running. Skip those; the UI disables the button
        # and shows "Sending…" but this is the authoritative backstop.
        active_sends = repos.operations.list_for_job(
            "send", OP_ACTIVE_STATES, payload.job_id
        )
        inflight = {op.contact_id for op in active_sends}
    # One batch id ties every send of this reach-out together, so each send's
    # entrypoint can detect *batch settle* and move the card once (FR-NW-03).
    batch_id = uuid4().hex
    enqueued: list[str] = []
    skipped: list[str] = []
    for c in payload.contacts:
        if c.contact_id in inflight:
            skipped.append(c.contact_id)
            continue
        inflight.add(c.contact_id)  # guard against duplicates within one request too
        enqueued.append(
            runner.submit("send", {
                "contact_id": c.contact_id,
                "job_id": payload.job_id,
                "application_id": payload.application_id,
                "batch_id": batch_id,
                "message": c.message,
                "dry_run": payload.dry_run,
                # The user clicked "Send now": the marker the presence gate reads
                # (invariant 3), so a background caller can never start a send.
                "user_initiated": True,
            })
        )
    return dto.ReachOutResult(enqueued=enqueued, skipped_contact_ids=skipped)


@router.get("/api/referrals/quota")
def referrals_quota(request: Request) -> dto.QuotaDTO:
    """Rolling outreach quota for the popup counter (US-NW-09/10).

    Used-counts AND caps both come from the package's enforcing ledger
    (maintainer 2026-08-02, closing the "divergent ledgers" item): the popup
    can never show head-room the send path will refuse. `OutreachLog` stays the
    per-send product history; it is no longer recounted as a quota source.
    Zero LinkedIn traffic — a local file read, off the event loop (a plain
    `def` handler runs in Starlette's threadpool automatically)."""
    with _db(request).repos() as repos:
        session = repos.linkedin_session.get()
        prefs = repos.preferences.get_or_create()
        profile = networker_ops.resolve_pacing_profile(repos, session=session)
        connected = bool(prefs.voyager_risk_marker_on) and (
            session is not None and session.status == "valid"
        )
    quota = networker_ops.linkedin_quota_snapshot(profile)
    return dto.quota_dto(connected=connected, quota=quota)


@router.post("/api/networking/contact-sync", status_code=202)
def networking_contact_sync(request: Request) -> dto.ContactSyncAccepted:
    """Refresh LinkedIn contact statuses for the Networking kanban (US-NW-12 /
    FR-NW-15). **Manual-only**: the Sync button is the one caller (maintainer
    decision, 2026-08-15).

    No schedule, no on-open refresh, no background timer. Each press is an
    on-demand refresh the user asked for, no more LinkedIn traffic than them
    opening linkedin.com and looking at their invitations themselves. The old
    12 h schedule and the later throttled on-open refresh are both retired
    (`docs/internal/linkedin-addon.md` section 5, `docs/internal/status.md`).

    Already-running syncs are joined rather than duplicated, so a double click
    does not fan out.
    """
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
        session = repos.linkedin_session.get()
        if session is None or session.status != "valid":
            raise HTTPException(
                status_code=409,
                detail="No valid LinkedIn session — connect in Settings first.",
            )
        in_flight = repos.operations.any_in_flight("contact_sync")

    if in_flight:
        return dto.ContactSyncAccepted(state="already_running")

    operation_id = _runner(request).submit("contact_sync", {})
    return dto.ContactSyncAccepted(id=operation_id, state="queued")


def _linkedin_session_base(repos: Any) -> tuple[dto.LinkedInSessionDTO, Any]:
    """The session DTO minus `rate_limits`, plus the resolved pacing profile.

    Built INSIDE the caller's repos context (ORM rows detach on exit). The
    caller then fills `rate_limits` via
    `networker_ops.linkedin_caps_snapshot(profile)` — the snapshot reads the
    pacing-ledger file, which must not block the event loop (async-first
    rule); a plain `def` handler runs off it in Starlette's threadpool
    automatically."""
    session = repos.linkedin_session.get()
    prefs = repos.preferences.get_or_create()
    profile = networker_ops.resolve_pacing_profile(repos, session=session)
    base = dto.linkedin_session_dto(
        session, enabled=bool(prefs.voyager_risk_marker_on),
        cursor=repos.linkedin_search_cursor.get(),
    )
    # The "Synced Nm ago" stamp for the Networking header: when a sweep last
    # ACTUALLY PROBED (or cleanly checked an empty roster). A budget-refused
    # sweep succeeds as an operation with synced 0 + a stop reason, and must
    # not re-stamp "Synced just now" — its outcome is surfaced separately so
    # the user sees WHY nothing refreshed (the 2026-08-15 live confusion:
    # every press answered in 10 ms, stamped "just now", and synced nothing).
    recent = repos.operations.recent_succeeded_by_kind("contact_sync", limit=25)
    base.contact_sync_last_at = next(
        (
            op.finished_at
            for op in recent
            if _is_real_sweep(op.result_ref or {})
        ),
        None,
    )
    if recent:
        newest = recent[0]
        ref = newest.result_ref or {}
        if newest.finished_at is not None and "skipped" not in ref:
            base.contact_sync_last_outcome = dto.ContactSyncOutcomeDTO(
                at=newest.finished_at,
                synced=int(ref.get("synced") or 0),
                failed=int(ref.get("failed") or 0),
                stopped=str(ref.get("stopped") or ""),
                unprobed=int(ref.get("unprobed") or 0),
            )
    return base, profile


def _is_real_sweep(ref: dict[str, Any]) -> bool:
    """True when a succeeded contact-sync op actually swept: it probed at
    least one contact, or checked a roster with nothing stopping it. A
    gate-skipped no-op (`skipped`) or a refused-at-zero sweep is not a sync
    the "Synced Nm ago" stamp may claim."""
    if "skipped" in ref:
        return False
    if int(ref.get("synced") or 0) > 0:
        return True
    return not ref.get("stopped")


def _linkedin_session_response(request: Request) -> dto.LinkedInSessionDTO:
    with _db(request).repos() as repos:
        base, profile = _linkedin_session_base(repos)
    snapshot = networker_ops.linkedin_caps_snapshot(profile)
    base.rate_limits = dto.rate_limits_dto(snapshot)
    _overlay_backoff(base, snapshot)
    return base


def _overlay_backoff(base: dto.LinkedInSessionDTO, snapshot: dict[str, Any]) -> None:
    """Report the pacing ledger's pause, which is the thing that enforces it.

    The `linkedin_sessions.backing_off` row is a mirror, and only the send path
    ever wrote it, so a rate limit hit during discover or contact sync paused
    every meter while the header still read "LinkedIn connected" and Settings
    offered no Resume. Reading the ledger here covers every op, including ones
    not written yet. A never-connected session is left alone: a pause is a
    statement about the account's traffic, not an invitation to connect."""
    if not snapshot.get("paused") or base.status == "never_set":
        return
    base.status = "backing_off"
    paused_until = float(snapshot.get("paused_until") or 0.0)
    if paused_until:
        base.paused_until = datetime.fromtimestamp(paused_until, tz=UTC)
    base.paused_reason = (
        str(snapshot.get("paused_reason") or "") or base.paused_reason
        or "LinkedIn rate-limit backoff"
    )


@router.get("/api/linkedin/session")
def linkedin_session(request: Request) -> dto.LinkedInSessionDTO:
    """LinkedIn session + master-toggle state (US-NW-09 / US-SET-06 / FR-SET-03).
    Reads the persisted session (fast — local only); the popup send path
    unlocks only when enabled AND status == 'valid'."""
    return _linkedin_session_response(request)


@router.post("/api/linkedin/connect", status_code=202)
def linkedin_connect(
    request: Request, payload: dto.LinkedInConnectRequest | None = None
) -> dto.OperationAccepted:
    """Start the headed-login session capture (US-SET-06 as-built). Enqueues the
    exclusive `linkedin_login` op — a visible browser opens at LinkedIn's login
    page; the user logs in themselves (the password never touches finds-you-jobs).
    `login_url` (maintainer/tests only) overrides the target with a LOCAL fixture.

    Gated on at least one LinkedIn feature being enabled: this route *opens a
    real browser at linkedin.com*, and it accepted that request with both
    opt-ins off until 2026-08-01 (posture doc section 4 #8)."""
    with _db(request).repos() as repos:
        _require_any_linkedin_feature(repos)
    snap: dict[str, Any] = {}
    if payload is not None and payload.login_url:
        snap["login_url"] = payload.login_url
    if payload is not None and payload.timeout_s:
        snap["timeout_s"] = payload.timeout_s
    operation_id = _runner(request).submit("linkedin_login", snap)
    return dto.OperationAccepted(id=operation_id, kind="linkedin_login", state="queued")


# The one-page-of-25 invariant is owned by the package
# (`pacing.MAX_JOBS_PER_SEARCH`, enforced in `worker.search_jobs`) — the route
# carries no size knob at all (maintainer directive 2026-08-01: a smaller limit
# never made the request smaller, it only discarded received rows while implying
# a lighter footprint).


@router.post("/api/linkedin/search", status_code=202)
def linkedin_search(
    request: Request, payload: dto.LinkedInSearchRequest | None = None
) -> dto.OperationAccepted:
    """Run a one-shot logged-in LinkedIn job search (discovery-expansion #6).

    User-clicked only — never a scheduled scan (scheduled scans must never touch
    a logged-in session). Gated server-side on **this feature's own** toggle +
    typed ack and a connected session; otherwise a clear error, not a silent
    no-op. (It used to check the *Referral Outreach* toggle instead — the wrong
    consent, in both directions: enabling referrals alone unlocked searches the
    user never acknowledged, while a search-only user was refused. Posture doc
    section 4 #8.) Results land in the same discovery funnel as every other source.

    `mode` (2026-08-01): `fresh` runs page 0 from current prefs and resets the
    pagination cursor; `next` continues the last Fresh search's snapshot from
    each pair's own offset. Next is refused (409) when no continuable cursor
    exists — never run, expired past `SEARCH_CURSOR_TTL`, or every pair
    exhausted. The op re-checks the same precondition (self-gate)."""
    mode = payload.mode if payload is not None else "fresh"
    with _db(request).repos() as repos:
        _require_job_search_opt_in(repos)
        session = repos.linkedin_session.get()
        if session is None or session.status != "valid":
            raise HTTPException(status_code=409, detail=SEARCH_NOT_CONNECTED)
        profile = networker_ops.resolve_pacing_profile(repos, session=session)
        if mode == "next":
            state = dto.linkedin_search_cursor_dto(repos.linkedin_search_cursor.get())
            if state is None:
                raise HTTPException(status_code=409, detail=SEARCH_NO_CURSOR)
            if state.expired:
                raise HTTPException(status_code=409, detail=SEARCH_CURSOR_EXPIRED)
            if state.exhausted:
                raise HTTPException(status_code=409, detail=SEARCH_CURSOR_EXHAUSTED)
    # Self-imposed pages/hour throttle: refuse when the hourly budget is spent
    # (both modes — every search fetches a page). The worker enforces this too;
    # the route surfaces it as a clean 429 rather than a 0-result search. Ledger
    # file read → off the event loop (Starlette's threadpool).
    snapshot = networker_ops.linkedin_caps_snapshot(profile)
    if snapshot["job_search_hour_remaining"] <= 0:
        raise HTTPException(
            status_code=429,
            detail="LinkedIn job-search hourly limit reached — wait for it to "
                   "reset, or raise it in Settings › LinkedIn self-imposed rate limits.",
        )
    operation_id = _runner(request).submit("linkedin_search", {"mode": mode})
    return dto.OperationAccepted(id=operation_id, kind="linkedin_search", state="queued")


@router.post("/api/linkedin/disconnect")
def linkedin_disconnect(request: Request) -> dto.LinkedInSessionDTO:
    """Disconnect: cancel any in-flight login, clear the session row, and delete
    BOTH on-disk session stores — the sealed storage-state JSON and the
    persistent Chromium profile (US-SET-06 Disconnect). Before 2026-07-12 the
    profile dir survived, so a "disconnected" user's next login window opened
    already logged in. This is local deletion only — it never logs the user out
    of LinkedIn server-side (the UI says so)."""
    import shutil

    from ..registry.networker_ops import linkedin_profile_dir

    LOGIN_CONTROL.cancel_all()
    storage = linkedin_storage_path()
    try:
        storage.unlink(missing_ok=True)
    except OSError as exc:
        get_logger().warning("linkedin disconnect: could not delete session file: %s", exc)
    try:
        # Off the loop (async-first rule, via Starlette's threadpool): a
        # populated Chromium profile is hundreds of MB across thousands of
        # files — seconds of filesystem work that must not starve /healthz.
        shutil.rmtree(linkedin_profile_dir())
    except FileNotFoundError:
        pass
    except OSError as exc:
        get_logger().warning("linkedin disconnect: could not delete profile dir: %s", exc)
    with _db(request).repos() as repos:
        repos.linkedin_session.update(
            status="never_set", connected_as="", li_at_expires_at=None,
            last_validated_at=None, paused_until=None, paused_reason="",
        )
        # A pagination cursor without its session is meaningless — drop it.
        repos.linkedin_search_cursor.clear()
    return _linkedin_session_response(request)


@router.post("/api/linkedin/validate")
def linkedin_validate(request: Request) -> dto.LinkedInSessionDTO:
    """Re-check the saved session LOCALLY (li_at presence/expiry) — **never hits
    LinkedIn** (US-SET-06 Validate). Flips status to valid / expired / never_set
    and stamps `last_validated_at`."""
    with _db(request).repos() as repos:
        profile = networker_ops.resolve_pacing_profile(repos)
    # Off the loop (async-first rule, via Starlette's threadpool):
    # session_status is local-only, but the first call lazily imports
    # playwright.sync_api — up to ~1 s on a cold packaged build.
    driver = networker_ops.DRIVER_FACTORY(profile)
    try:
        info = driver.session_status()
    finally:
        driver.close()
    status = info.get("status", "never_set")
    with _db(request).repos() as repos:
        fields: dict[str, Any] = {"status": status, "last_validated_at": now_utc()}
        if status != "valid":
            fields["connected_as"] = ""
        repos.linkedin_session.update(**fields)
    return _linkedin_session_response(request)


@router.post("/api/linkedin/resume")
def linkedin_resume(request: Request) -> dto.LinkedInSessionDTO:
    """Clear the voyager-owned backoff pause (Settings → Networking manual resume,
    FR-NW-05 / US-REF-09). Resets the local pacing ledger and re-validates.

    Gated: this is the one route that *switches a safety mechanism off* — it
    clears the 24 h backoff LinkedIn's own throttle signal put us in — and it ran
    ungated until 2026-08-01 (posture doc section 4 #8)."""
    with _db(request).repos() as repos:
        _require_any_linkedin_feature(repos)
        profile = networker_ops.resolve_pacing_profile(repos)
    # Off the loop (async-first rule, via Starlette's threadpool): same
    # first-call playwright import cost as validate, plus the pacing-ledger
    # file writes.
    driver = networker_ops.DRIVER_FACTORY(profile)
    try:
        driver.resume()
        info = driver.session_status()
    finally:
        driver.close()
    status = info.get("status", "never_set")
    with _db(request).repos() as repos:
        repos.linkedin_session.update(
            status=status, paused_until=None, paused_reason="",
            last_validated_at=now_utc(),
        )
    return _linkedin_session_response(request)


@router.post("/api/linkedin/rate-limits")
def linkedin_set_rate_limits(
    request: Request, payload: dto.LinkedInRateLimitsRequest
) -> dto.LinkedInSessionDTO:
    """Set the self-imposed LinkedIn rate-limit profile (maintainer directive
    2026-08-01, replacing the New/Seasoned tier + Free/Premium plan selectors).

    The package owns the cap *values*; this stores only the user's choices:
    - **membership_type** and/or **risk_pct** — the basis. Changing either
      RESETS every per-meter override to the freshly computed default (the
      maintainer's "both reset" rule): the ceilings or the scale changed, so any
      old absolute pin is stale.
    - **override_key / override_value** — pin one `{meter}_{window}` cap to an
      absolute number (only when NOT also changing the basis).
    - **reset_overrides** — drop all pins back to the computed defaults.

    voyager still enforces the numbers (NFR-LI-02); this is the selection only."""
    from sidecar.packages.referral_outreach import MEMBERSHIPS, OVERRIDABLE, clamp_risk

    override_keys = {f"{m}_{w}" for m, w in OVERRIDABLE}
    changes_basis = payload.membership_type is not None or payload.risk_pct is not None
    with _db(request).repos() as repos:
        current = repos.linkedin_session.get_or_create()
        fields: dict[str, Any] = {}
        if changes_basis or payload.reset_overrides:
            if payload.membership_type is not None:
                if payload.membership_type not in MEMBERSHIPS:
                    raise HTTPException(
                        status_code=422,
                        detail=f"membership_type must be one of {sorted(MEMBERSHIPS)}",
                    )
                fields["membership_type"] = payload.membership_type
            if payload.risk_pct is not None:
                fields["risk_pct"] = clamp_risk(payload.risk_pct)
            # Both-reset rule: a basis change (or an explicit reset) clears every
            # override so the caps revert to the new (ceiling × risk%) default.
            fields["cap_overrides"] = {}
        elif payload.override_key is not None:
            if payload.override_key not in override_keys:
                raise HTTPException(
                    status_code=422,
                    detail=f"override_key must be one of {sorted(override_keys)}",
                )
            if payload.override_value is None or payload.override_value < 0:
                raise HTTPException(
                    status_code=422, detail="override_value must be a non-negative integer"
                )
            overrides = dict(current.cap_overrides or {})
            overrides[payload.override_key] = int(payload.override_value)
            fields["cap_overrides"] = overrides
        else:
            raise HTTPException(
                status_code=422,
                detail="provide membership_type/risk_pct, an override, or reset_overrides",
            )
        repos.linkedin_session.update(**fields)
    return _linkedin_session_response(request)


@router.post("/api/browser/view", status_code=202)
def view_browser_page(
    request: Request, payload: dto.BrowserViewRequest
) -> dto.OperationAccepted:
    """Queue a `view_page` operation that shows `url` on a watch-only broker
    surface (2026-08-16 — the queued successor to /api/browser/open's immediate
    navigation). The op waits its admission turn behind any browser-driving
    operation (policy rule 3), so a user's click can never corrupt a running
    op's page state — and a view of the already-shown page settles as a
    no-op (`skipped`). Gated on the Referral Outreach toggle, NOT on session
    validity: a never-connected session can still watch a page; validity gates
    the ops that act. Vendor-agnostic: url and slug are runtime arguments."""
    url = (payload.url or "").strip()
    if not url.startswith(("https://", "http://")):
        raise HTTPException(status_code=422, detail="url must be http(s)")
    with _db(request).repos() as repos:
        _require_networking_enabled(repos)
    if getattr(request.app.state, "browser", None) is None:
        raise HTTPException(status_code=503, detail="browser broker unavailable")
    snapshot: dict[str, Any] = {
        "url": url,
        "surface": payload.surface or "default",
        # The click IS the presence (invariant 3) — only this route sets it,
        # and the generic enqueue refuses the kind.
        "user_initiated": True,
    }
    if (
        isinstance(payload.width, int)
        and isinstance(payload.height, int)
        and isinstance(payload.dpr, (int, float))
    ):
        snapshot.update(width=payload.width, height=payload.height, dpr=payload.dpr)
    if payload.contact_id:
        snapshot["contact_id"] = payload.contact_id
    operation_id = _runner(request).submit("view_page", snapshot)
    return dto.OperationAccepted(id=operation_id, kind="view_page", state="queued")
