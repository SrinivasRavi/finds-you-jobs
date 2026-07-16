"""Networking operation entrypoints (Track N3).

Wires the N2 Networker silo (`sidecar.modules.networker`) into the operation
runner as three bounded ops (US-REF-01/03/04, FR-REF-*):

- `discover` — zero-LLM; delegates to the voyager subprocess driver, then
  upserts each candidate as a `Contact` row (status `candidate`, off the kanban
  until reached) + a per-job `ContactJobAssoc`. Streams `networker` SSE events
  for the find-referrals popup's live list.
- `draft`   — the one LLM op; routed engine (like tailor/cover). Grounds one
  per-audience referral draft in the master profile. Returned in `result_ref`
  (a transient preview — not persisted as an artifact).
- `send`    — zero-LLM; routes warm→DM / cold→connection-note through the
  voyager driver. Persists an `OutreachLog` (verbatim voyager error per
  NFR-SIDE-04), flips the contact onto the kanban, and moves a Saved card to
  Seeking Referral on the first real send (US-NW-09 batch-settle guard).

**Direct in-process.** This is `app/` importing `modules/` (the allowed one-way
direction, §5.2). The GPLv3 OpenOutreach-derived worker is reached through the
silo's `DirectVoyagerDriver`, which calls it in-process — the subprocess firewall
the prior MIT-era repository used is retired (AGPL host; §2).

**Test seam.** The voyager driver is built through `DRIVER_FACTORY` so tests
inject a fake driver (zero live LinkedIn traffic — the wire stays cold; the
maintainer dogfoods the live send path). The LLM engine for `draft` comes from
`ctx.engine` (routed), so tests pass a FakeEngine the same way as tailor/cover.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sidecar.modules.networker import Contact as NetContact
from sidecar.modules.networker import discover as net_discover
from sidecar.modules.networker import draft as net_draft
from sidecar.modules.networker import resolve as net_resolve
from sidecar.modules.networker import send as net_send
from sidecar.modules.networker.driver import DirectVoyagerDriver, VoyagerDriver
from sidecar.modules.networker.types import Audience, NetworkerError, Warmth

from ..db.base import now_utc
from ..db.database import resolve_data_dir
from ..events import make_event
from .company_anchor import employer_domain, resolution_key
from .engines import EngineNotConfiguredError
from .operations import OperationContext, OperationOutcome

if TYPE_CHECKING:
    from ..db import Repos
    from ..db.models import Contact as ContactRow

# ---------------------------------------------------------------------------
# Driver factory seam (tests override this; production builds the real one).
# ---------------------------------------------------------------------------

DriverFactory = Callable[[str | None], VoyagerDriver]


def linkedin_data_dir() -> Path:
    """`<app-data>/linkedin/` — home of the saved session + the pacing ledger.
    Never the repo (the storage-state file is a secret-at-rest, §NFR-SEC-01)."""
    return resolve_data_dir() / "linkedin"


def linkedin_storage_path() -> Path:
    """The Playwright storage-state file the headed-login flow writes (N4)."""
    return linkedin_data_dir() / "storage_state.json"


def linkedin_profile_dir() -> Path:
    """`<app-data>/linkedin/profile/` — the PERSISTENT Chromium user-data-dir.
    Cookies live here across sessions; the user can reopen this profile and log
    out to end the app's session (2026-07-09). Secret-at-rest like the JSON."""
    return linkedin_data_dir() / "profile"


def _default_driver_factory(tier: str | None) -> VoyagerDriver:
    """Build the production subprocess driver from the app-data dir.

    `storage_state` is the saved-cookie file the LinkedIn-connect flow writes
    (the path is always passed — `login` *creates* it, and discover/send fail
    cleanly with a typed "no session" error when it is absent); `state_dir` is the
    voyager pacing ledger. Both live under the data dir. The session-store
    encryption key rides in the child env (NFR-SEC-01) so voyager seals/reads
    the storage-state file — env, never argv. This is only ever *run* live by
    the maintainer — automated tests inject a fake driver (wire stays cold)."""
    from ..security import SESSION_KEY_ENV, get_session_key

    data = linkedin_data_dir()
    return DirectVoyagerDriver(
        storage_state=str(data / "storage_state.json"),
        user_data_dir=str(data / "profile"),
        state_dir=str(data / "state"),
        tier=tier,
        env={SESSION_KEY_ENV: get_session_key(resolve_data_dir())},
    )


DRIVER_FACTORY: DriverFactory = _default_driver_factory


def _resolve_tier(repos: Repos) -> str | None:
    session = repos.linkedin_session.get()
    return session.account_tier if session is not None else None


# ---------------------------------------------------------------------------
# ORM Contact ↔ silo Contact mapping
# ---------------------------------------------------------------------------


def _public_id_from_url(url: str) -> str:
    """The /in/<slug> public identifier from a LinkedIn profile URL.

    Live-dogfood fix 2026-07-08: this used to pass the FULL URL as the
    public_identifier, so voyager navigated to
    `linkedin.com/in/https://www.linkedin.com/in/<slug>/` — a genuine 404 —
    and every send failed. (Clean-room parse; no GPL import — NFR-LIC-01.)"""
    from urllib.parse import unquote, urlparse

    parts = urlparse(url.strip()).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "in" and parts[1]:
        return unquote(parts[1])
    return url  # already a bare slug (or unparseable — voyager will error verbatim)


def _net_contact_from_row(row: ContactRow) -> NetContact:
    """A silo Contact (framework-free) from a persisted row, for draft/send."""
    return NetContact(
        public_identifier=_public_id_from_url(row.linkedin_url),
        full_name=row.name,
        headline=row.headline,
        current_title=row.current_role,
        current_company=row.current_company,
        url=row.linkedin_url,
        connection_degree=row.connection_degree,
        is_first_degree=row.is_first_degree,
        audience=Audience(row.audience_tag) if row.audience_tag else Audience.OTHER,
        warmth=Warmth(row.warmth) if row.warmth else Warmth.COLD,
    )


def _job_text_for(repos: Repos, job_id: str | None) -> str:
    if not job_id:
        return ""
    job = repos.jobs.get(job_id)
    return job.description if job is not None else ""


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def _candidate_dto(c) -> dict:
    """One CompanyCandidate → the JSON shape the confirm popup renders."""
    return {
        "urn": c.urn, "company_id": c.company_id, "name": c.name,
        "vanity": c.vanity, "industry": c.industry, "logo_url": c.logo_url,
        "website": c.website, "domain_match": c.domain_match,
    }


def _cache_resolution(ctx: OperationContext, key: str, c, source: str) -> None:
    if not key:
        return
    assert ctx.db is not None
    with ctx.db.repos() as repos:
        repos.company_resolutions.upsert(
            key, company_name=getattr(c, "name", "") or "", company_urn=c.urn,
            company_vanity=getattr(c, "vanity", ""), industry=getattr(c, "industry", ""),
            source=source,
        )


def _resolve_company_urn(
    ctx: OperationContext,
    *,
    company: str,
    canonical_url: str,
    source_adapter: str,
    tier: str | None,
    company_url: str | None,
    dry_run: bool,
) -> tuple[str | None, str, list[dict], dict]:
    """Resolve `company` → the LinkedIn company URN to scope discovery by (FR-NW-02).

    Precedence: a pasted LinkedIn company URL (authoritative) → a prior cached
    choice → a fresh typeahead where ONLY a domain-website match auto-picks →
    otherwise the candidate list (possibly empty) is returned for the user to
    confirm or paste. **There is no name-keyword fallback**: if we can't resolve a
    real entity, the caller does NOT discover — it asks the user. This is the fix
    for the "zip" failure, where a 0-hit typeahead silently reverted to a keyword
    search and returned employees of unrelated namesake companies.

    Returns (company_urn | None, resolved_name, confirm_candidates, usage). A
    `company_urn is None` return ALWAYS means "ask the user" (never "discover
    anyway"); `confirm_candidates` may be empty (→ the popup offers paste-only)."""
    assert ctx.db is not None  # caller guarantees a DB context
    key = resolution_key(canonical_url, source_adapter, company)

    # 1) Authoritative: the user pasted the company's LinkedIn URL.
    if company_url:
        driver = DRIVER_FACTORY(tier)
        result = net_resolve("", driver=driver, url=company_url, dry_run=dry_run)
        if result.candidates:
            c = result.candidates[0]
            _cache_resolution(ctx, key, c, "user")
            return c.urn, (c.name or company), [], asdict(result.usage)
        # The pasted URL didn't resolve to a company — re-ask (no discovery).
        return None, company, [], asdict(result.usage)

    # 2) Cached choice for this employer.
    with ctx.db.repos() as repos:
        cached = repos.company_resolutions.get(key)
    if cached is not None and cached.company_urn:
        return cached.company_urn, (cached.company_name or company), [], {}

    # 3) Fresh typeahead — ONLY a domain-website match is confident enough to auto-pick.
    domain = employer_domain(canonical_url)
    driver = DRIVER_FACTORY(tier)
    result = net_resolve(
        company, driver=driver, prefer_domain=domain or None, limit=5, dry_run=dry_run
    )
    usage = asdict(result.usage)
    domain_hit = next((c for c in result.candidates if c.domain_match), None)
    if domain_hit is not None:
        _cache_resolution(ctx, key, domain_hit, "domain")
        return domain_hit.urn, (domain_hit.name or company), [], usage
    # Anything else (ambiguous, single, or zero) → confirm/paste. Never keyword-search.
    return None, company, [_candidate_dto(c) for c in result.candidates], usage


def discover_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Resolve the target company to a LinkedIn entity, then discover ≤ limit of
    its CURRENT employees and upsert them as candidate rows (US-REF-01 / FR-NW-02).

    A `company_urn` in the snapshot is the user's confirmed pick (from the
    company-confirm popup) — it's cached and used directly. Otherwise resolution
    runs: an unambiguous result discovers immediately; an ambiguous one returns
    `needs_company_confirm` (nothing discovered) so the popup can ask the user."""
    snap = ctx.input_snapshot
    company = snap.get("company", "")
    job_id = snap.get("job_id")
    limit = int(snap.get("limit", 10))
    page = int(snap.get("page", 1))
    dry_run = bool(snap.get("dry_run", False))
    chosen_urn = snap.get("company_urn")
    company_url = snap.get("company_url")  # a pasted LinkedIn company URL (authoritative)
    if ctx.db is None:
        raise RuntimeError("discover operation requires a database context")

    with ctx.db.repos() as repos:
        tier = _resolve_tier(repos)
        job = repos.jobs.get(job_id) if job_id else None
        job_text = job.description if job is not None else ""
        canonical_url = job.canonical_url if job is not None else ""
        source_adapter = job.source_adapter if job is not None else ""

    resolve_usage: dict = {}
    if chosen_urn:
        # User picked a specific candidate in the popup — cache + use it directly.
        company_urn: str | None = chosen_urn
        resolved_name = snap.get("company_name") or company
        key = resolution_key(canonical_url, source_adapter, company)
        if key:
            with ctx.db.repos() as repos:
                repos.company_resolutions.upsert(
                    key, company_name=resolved_name, company_urn=chosen_urn,
                    company_vanity=snap.get("company_vanity", ""),
                    industry=snap.get("company_industry", ""), source="user",
                )
    else:
        company_urn, resolved_name, confirm_cands, resolve_usage = _resolve_company_urn(
            ctx, company=company, canonical_url=canonical_url,
            source_adapter=source_adapter, tier=tier, company_url=company_url, dry_run=dry_run,
        )
        if company_urn is None:
            # We could NOT confidently resolve the target company. Do NOT discover
            # (the old keyword fallback returned namesake-company employees — the
            # "zip" bug). Ask the user to pick a candidate or paste the company's
            # LinkedIn URL. `candidates` may be empty → the popup offers paste-only;
            # `url_failed` flags a pasted URL that didn't resolve.
            if ctx.publish is not None:
                ctx.publish(make_event("networker", {
                    "id": ctx.operation_id, "phase": "needs_company_confirm",
                    "company": company, "job_id": job_id, "candidates": confirm_cands,
                    "url_failed": bool(company_url),
                }))
            return OperationOutcome(
                result_ref={"needs_company_confirm": True, "company": company,
                            "job_id": job_id, "candidates": confirm_cands,
                            "url_failed": bool(company_url), "count": 0},
                usage=resolve_usage,
            )

    driver = DRIVER_FACTORY(tier)
    result = net_discover(
        company, job_text, driver=driver, limit=limit, dry_run=dry_run,
        company_urn=company_urn, page=page,
    )

    contact_ids: list[str] = []
    with ctx.db.repos() as repos:
        for c in result.contacts:
            if not c.public_identifier:
                continue
            row = repos.contacts.upsert_by_url(
                c.url or c.public_identifier,
                name=c.full_name,
                current_role=c.current_title,
                # Discovery is now scoped + re-verified to the target entity, so a
                # surviving contact genuinely works there; fall back to the
                # resolved (verified) company name, never the raw search string —
                # the `or company` mask that mislabeled unknown employers is gone.
                current_company=c.current_company or resolved_name,
                headline=c.headline,
                connection_degree=c.connection_degree,
                is_first_degree=c.is_first_degree,
                audience_tag=c.audience.value,
                warmth=c.warmth.value,
            )
            # A brand-new discovery lands as `candidate` (the Contact default —
            # off the kanban until reached); an already-known contact keeps its
            # live status because upsert_by_url never overwrites connection_status.
            if job_id:
                repos.contact_job_assocs.upsert(
                    row.id, job_id, audience_tag=c.audience.value, status="pending"
                )
            contact_ids.append(row.id)
            if ctx.publish is not None:
                ctx.publish(make_event("networker", {
                    "id": ctx.operation_id, "phase": "candidate",
                    "company": company, "job_id": job_id, "contact_id": row.id,
                }))

    if ctx.publish is not None:
        ctx.publish(make_event("networker", {
            "id": ctx.operation_id, "phase": "discovered",
            "company": company, "job_id": job_id, "count": len(contact_ids),
        }))
    # discover usage counts both the resolve typeahead (when it ran) + the search.
    usage = asdict(result.usage)
    if resolve_usage.get("internal_calls"):
        usage["internal_calls"] = usage.get("internal_calls", 0) + resolve_usage["internal_calls"]
    return OperationOutcome(
        result_ref={"company": company, "job_id": job_id, "company_urn": company_urn,
                    "contact_ids": contact_ids, "count": len(contact_ids)},
        usage=usage,
    )


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------


def draft_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Draft one grounded referral-ask for a contact (US-REF-03). Routed engine."""
    if ctx.engine is None:
        raise EngineNotConfiguredError(ctx.kind)
    snap = ctx.input_snapshot
    contact_id = snap["contact_id"]
    job_id = snap.get("job_id")
    guidance = snap.get("guidance", "")
    if ctx.db is None:
        raise RuntimeError("draft operation requires a database context")

    with ctx.db.repos() as repos:
        row = repos.contacts.get(contact_id)
        if row is None:
            raise LookupError(f"contact {contact_id!r} not found")
        net_contact = _net_contact_from_row(row)
        job_text = _job_text_for(repos, job_id)
        profile = repos.profile.get_current()
        master_md = profile.resume_markdown if profile is not None else ""

    from ..prompt_overrides import get_override

    result = net_draft(
        net_contact, job_text, guidance=guidance,
        master_md=master_md, engine=ctx.engine.engine,
        skill_md=get_override("networker_draft"),
    )
    return OperationOutcome(
        result_ref={
            "contact_id": contact_id, "job_id": job_id,
            "message": result.message, "channel": result.channel.value,
            "warmth": result.warmth.value, "audience": result.audience.value,
            "char_count": result.char_count, "notes": list(result.notes),
        },
        usage=asdict(result.usage),
        engine=ctx.engine.name,
        model=result.usage.model or ctx.engine.model,
    )


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


def _is_rate_limited(result: Any) -> bool:
    """Voyager reported a rate-limit / restriction pause on this send (FR-NW-05).
    Either the explicit `rate_limited` error, or the live quota flagged paused."""
    if getattr(result, "error", "") == "rate_limited":
        return True
    quota = getattr(result, "quota", None) or {}
    return bool(quota.get("paused"))


_SEND_ACTIVE = frozenset({"queued", "running"})
_SEND_ALL = frozenset({"queued", "running", "succeeded", "failed", "cancelled"})


def _maybe_move_on_batch_settle(
    repos: Repos,
    *,
    batch_id: str | None,
    current_op_id: str | None,
    application_id: str | None,
    job_id: str | None,
) -> None:
    """Advance a Saved card → Seeking Referral once, at *batch settle* (FR-NW-03).

    The move fires only when every send op of the batch is terminal — so the
    *last* send to finish triggers it, never the first — and only if ≥1 send in
    the batch actually landed and the card is still in Saved (never dragging an
    Applied+/frozen card backward). A lone send (no `batch_id`) is its own settled
    batch, preserving the single-send move. The current op is still `running`
    while this executes, so it is excluded from the "siblings still in flight"
    check and its just-written OutreachLog carries its outcome."""
    if not application_id or not job_id:
        return
    member_ids: set[str] = {current_op_id} if current_op_id else set()
    if batch_id:
        siblings = [
            op
            for op in repos.operations.list_by_kind_states("send", set(_SEND_ALL))
            if (op.input_snapshot or {}).get("batch_id") == batch_id
            and op.id != current_op_id
        ]
        if any(op.state in _SEND_ACTIVE for op in siblings):
            return  # batch has not settled yet — a sibling is still queued/running
        member_ids |= {op.id for op in siblings}
    sent_in_batch = any(
        log.outcome == "sent" and log.operation_id in member_ids
        for log in repos.outreach_logs.list_for_job(job_id)
    )
    if not sent_in_batch:
        return
    app = repos.applications.get(application_id)
    if app is not None and app.column == "saved":
        repos.applications.update(application_id, column="seeking_referral")


def send_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Send one referral-ask via the voyager driver; persist the audit (US-REF-04)."""
    snap = ctx.input_snapshot
    contact_id = snap["contact_id"]
    job_id = snap.get("job_id")
    application_id = snap.get("application_id")
    batch_id = snap.get("batch_id")
    message = snap.get("message", "")
    dry_run = bool(snap.get("dry_run", False))
    if ctx.db is None:
        raise RuntimeError("send operation requires a database context")

    with ctx.db.repos() as repos:
        row = repos.contacts.get(contact_id)
        if row is None:
            raise LookupError(f"contact {contact_id!r} not found")
        net_contact = _net_contact_from_row(row)
        tier = _resolve_tier(repos)
        is_first_degree = row.is_first_degree
        audience_tag = row.audience_tag
    driver = DRIVER_FACTORY(tier)

    try:
        result = net_send(message, net_contact, driver=driver, tier=tier, dry_run=dry_run)
    except NetworkerError as exc:
        # A hard voyager failure (stale selector, subprocess crash, unparseable
        # JSON) used to skip the OutreachLog write entirely — the "6 failed sends,
        # outreach_logs empty" dogfood bug. The audit row is a hard requirement for
        # EVERY attempt incl. failures (FR-REF OutreachLog / NFR-SIDE-04), so we
        # persist the verbatim failure here, emit the send_failed event, then
        # re-raise so the operation is still marked failed (traceback → recorder).
        channel = "dm" if is_first_degree else "connection_note"
        detail = str(exc)
        now = now_utc()
        with ctx.db.repos() as repos:
            repos.outreach_logs.create(
                contact_id, job_id=job_id, channel=channel, body_sent=message,
                outcome="failed", outcome_detail=detail, operation_id=ctx.operation_id,
                batch_id=batch_id,
            )
            # A hard crash on the *last* batch member still settles the batch —
            # move the card iff an earlier sibling landed (FR-NW-03).
            if not dry_run:
                _maybe_move_on_batch_settle(
                    repos, batch_id=batch_id, current_op_id=ctx.operation_id,
                    application_id=application_id, job_id=job_id,
                )
        if ctx.publish is not None:
            ctx.publish(make_event("networker", {
                "id": ctx.operation_id, "phase": "send_failed",
                "contact_id": contact_id, "job_id": job_id,
                "sent": False, "reason": detail, "quota": None,
            }))
        raise

    outcome_str = "sent" if result.sent else ("pending" if dry_run else "failed")
    outcome_detail = result.error or result.reason or ""
    now = now_utc()
    with ctx.db.repos() as repos:
        log = repos.outreach_logs.create(
            contact_id,
            job_id=job_id,
            channel=result.channel.value,
            body_sent=message,
            outcome=outcome_str,
            outcome_detail=outcome_detail,
            operation_id=ctx.operation_id,
            batch_id=batch_id,
            sent_at=now if result.sent else None,
        )
        log_id = log.id
        if result.sent and not dry_run:
            # Flip onto the kanban. A 1st-degree contact is already connected —
            # a DM lands them in Accepted; a cold connect-note lands them in Sent.
            if is_first_degree:
                repos.contacts.update(
                    contact_id, connection_status="accepted",
                    sent_at=now, accepted_at=now,
                )
            else:
                repos.contacts.update(
                    contact_id, connection_status="sent", sent_at=now,
                )
            if job_id:
                repos.contact_job_assocs.upsert(
                    contact_id, job_id, audience_tag=audience_tag, status="pending"
                )
        # Batch-settle card move (FR-NW-03): advance Saved → Seeking Referral once,
        # when the whole reach-out batch has settled with ≥1 sent — not on the
        # first individual send. Runs even for a not-sent send (the last one to
        # finish may be a cap-stop) so the move still fires iff an earlier sibling
        # landed. Skipped for dry-runs (no `sent` log exists to satisfy the guard).
        if not dry_run:
            _maybe_move_on_batch_settle(
                repos, batch_id=batch_id, current_op_id=ctx.operation_id,
                application_id=application_id, job_id=job_id,
            )
        # Backoff surfacing (FR-NW-05 / NFR-LI-03): when voyager reports a
        # rate-limit pause, persist it on the session so the pill flips to
        # "Backing off" and Settings → Networking shows the manual-resume button.
        # The pause itself is voyager-owned (the pacing ledger); we only mirror it.
        if _is_rate_limited(result):
            quota = result.quota or {}
            paused_until = quota.get("paused_until") or 0
            repos.linkedin_session.update(
                status="backing_off",
                paused_until=(
                    datetime.fromtimestamp(paused_until, tz=UTC) if paused_until else now
                ),
                paused_reason=(result.reason or result.error or "LinkedIn rate-limit backoff"),
            )

    if ctx.publish is not None:
        ctx.publish(make_event("networker", {
            "id": ctx.operation_id, "phase": "sent" if result.sent else "send_failed",
            "contact_id": contact_id, "job_id": job_id,
            "sent": result.sent, "reason": outcome_detail, "quota": result.quota,
        }))
    return OperationOutcome(
        result_ref={
            "contact_id": contact_id, "job_id": job_id, "outreach_log_id": log_id,
            "sent": result.sent, "channel": result.channel.value,
            "status": result.status, "reason": result.reason, "error": result.error,
            "quota": result.quota,
        },
        usage=asdict(result.usage),
    )


def networker_entrypoints() -> dict[str, Any]:
    """The three N3 kinds → their entrypoints (registered in operations.py)."""
    return {
        "discover": discover_entrypoint,
        "draft": draft_entrypoint,
        "send": send_entrypoint,
    }
