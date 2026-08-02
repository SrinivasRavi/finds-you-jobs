# voyager_py/worker.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code for the finds-you-jobs fork (GPL subtree). The operation layer: it ties
# the pacing/caps ledger (pacing.py) to the live browser actions (session.py,
# actions.py, discovery.py) and returns plain dicts the CLI serialises to JSON.
# Caps + backoff are ENFORCED here, inside the subprocess (ROADMAP §66,
# NFR-LI-01/02/03) — the MIT host never re-implements them. Enforcement covers
# READS as well as sends: discover/contact-sync/search-jobs are metered and
# refused during backoff (2026-07-30 — before that they built no Pacer at all).
"""The bounded operations: discover, send-connection, send-dm, status, quota,
contact-sync, login, and search-jobs (the read-only logged-in job search —
finds-you-jobs discovery-expansion #6). Every operation supports dry_run (no
browser, no network — plan only)."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from .errors import RateLimited, ReachedConnectionLimit, VoyagerError
from .url_utils import url_to_public_id
from .pacing import (
    MAX_JOBS_PER_SEARCH,
    Pacer,
    PacingProfile,
    resolve_profile,
)

logger = logging.getLogger("voyager_py.worker")


def _pacer(profile: PacingProfile | None, state_dir: str | None) -> Pacer:
    """Build the enforcing Pacer from the profile (membership × risk% ×
    overrides). None → the conservative defaults (free × 60%). The effective
    caps are computed HERE, in the package (NFR-LI-02)."""
    sd = Path(state_dir) if state_dir else None
    return Pacer(resolve_profile(profile), state_dir=sd)


@contextlib.contextmanager
def _paced_session(
    pacer: Pacer | None,
    *,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
):
    """The scaffold EVERY browser-touching operation shares: build the session,
    and on the way out ALWAYS persist the pacing ledger and then close the
    browser — in that order, whatever happened in between (a return, a refusal,
    a RateLimited, or an unexpected crash mid-send).

    Three of the past account-safety bugs were one op missing one piece of this
    scaffold (a read path that built no Pacer at all, a charge that was never
    saved, a browser left open), which is why it is one function now: the next
    piece gets added HERE, for every op at once.

    `pacer=None` is the `status` op — the one browser op that meters nothing.

    What deliberately stays in the ops: the caps/backoff gates (each has its own
    meters), and the `except RateLimited → envelope` translation, because the
    envelope is the wire contract with the host and its fields differ per op.
    That `except` must sit INSIDE this block: `pause_for_backoff` only records
    the pause in memory, so the `finally` save below is what persists it — catch
    it outside and the 24 h backoff is silently lost.

    The session import is function-local on purpose: importing `.session` pulls
    in Playwright, which the no-browser ops (quota / resume / session-status)
    must not pay for.
    """
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        yield session
    finally:
        if pacer is not None:
            pacer.save()
        session.close()


def quota(
    state_dir: str | None = None,
    *,
    profile: PacingProfile | None = None,
) -> dict:
    """Report the live remaining quota + backoff state (FR-NW-01/04). No browser."""
    pacer = _pacer(profile, state_dir)
    return {"op": "quota", "ok": True, "quota": pacer.remaining()}


def resume(
    state_dir: str | None = None,
    *,
    profile: PacingProfile | None = None,
) -> dict:
    """Clear the voyager-owned backoff pause (Settings → Networking manual resume,
    FR-NW-05 / NFR-LI-03). No browser, no network — just resets the ledger flag."""
    pacer = _pacer(profile, state_dir)
    pacer.resume()
    pacer.save()
    return {"op": "resume", "ok": True, "quota": pacer.remaining()}


def session_status(storage_state: str | None = None) -> dict:
    """LOCAL session validity — `li_at` presence + expiry from the saved
    storage-state file. **No browser, no network** (the host validates without
    hitting LinkedIn). Returns `status` ∈ valid | expired | never_set."""
    if not storage_state:
        return {"op": "session-status", "ok": True, "status": "never_set",
                "present": False, "has_auth_cookie": False}
    from .session import inspect_storage_state

    info = inspect_storage_state(storage_state)
    if not info["present"] or not info["has_auth_cookie"]:
        status = "never_set"
    elif info["expired"]:
        status = "expired"
    else:
        status = "valid"
    return {"op": "session-status", "ok": True, "status": status, **info}


def login(
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    *,
    login_url: str | None = None,
    timeout_s: float = 300.0,
) -> dict:
    """Open a **headed** browser, wait for the user to log in (the `li_at` cookie
    appears), and persist the storage-state. The password is never handled here.

    `login_url` overrides the target (a LOCAL fixture for plumbing tests — no
    linkedin.com traffic). Live login is a maintainer-only action."""
    if not storage_state:
        raise VoyagerError("login requires --storage-state (where to save the session)")
    from .session import LINKEDIN_LOGIN_URL, capture_login

    result = capture_login(
        storage_state,
        login_url=login_url or LINKEDIN_LOGIN_URL,
        timeout_s=timeout_s,
        user_data_dir=user_data_dir,
    )
    return {"op": "login", "ok": True, **result}


def resolve_company(
    keywords: str = "",
    *,
    url: str | None = None,
    limit: int = 5,
    prefer_domain: str | None = None,
    profile: PacingProfile | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Resolve a company → LinkedIn company entities (URN + meta).

    `url` (a pasted LinkedIn company URL) is the authoritative single-entity path
    — the user pins the exact company, no typeahead guessing. Otherwise typeahead
    on `keywords`; `prefer_domain` (the employer domain from the job URL) flags a
    website-matched candidate for the app's silent auto-pick. The host scopes
    People discovery by the returned `currentCompany` URN (the company-correctness
    fix) and confirms with the user when it can't resolve confidently. Zero LLM."""
    if not (keywords and keywords.strip()) and not (url and url.strip()):
        raise VoyagerError("resolve-company requires --name or --url")
    if dry_run:
        target = f"url {url!r}" if url else f"name {keywords!r}"
        return {
            "op": "resolve-company", "ok": True, "dry_run": True,
            "keywords": keywords, "url": url,
            "plan": f"would resolve company by {target} (limit {limit})"
                    + (f", domain-anchor on {prefer_domain!r}" if prefer_domain else ""),
            "companies": [],
        }
    # The last read path that built no Pacer at all (posture doc §4 fix 2):
    # refuse during backoff, and charge the CUL search budget for the keyword
    # typeahead (company search is CUL-counted). A pasted URL is a direct
    # single-entity fetch, not a search — backoff-gated but not CUL-charged.
    pacer = _pacer(profile, state_dir)
    charge_search = not (url and url.strip())
    if charge_search:
        allowed, reason = pacer.can_search()
    else:
        # URL path: only the backoff gate applies — a direct fetch spends no
        # search budget, but must still stop when LinkedIn said stop.
        allowed, reason = (False, pacer.paused_reason()) if pacer.is_paused() else (True, "")
    if not allowed:
        return {"op": "resolve-company", "ok": False, "keywords": keywords,
                "url": url, "error": "cap_or_backoff", "reason": reason,
                "count": 0, "companies": [], "quota": pacer.remaining()}

    from .company import resolve_company as _resolve

    with _paced_session(pacer, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        try:
            if charge_search:
                pacer.record_search()
            companies = _resolve(
                session, keywords, url=url, limit=limit, prefer_domain=prefer_domain
            )
            return {"op": "resolve-company", "ok": True, "keywords": keywords, "url": url,
                    "prefer_domain": prefer_domain, "count": len(companies),
                    "companies": companies, "quota": pacer.remaining()}
        except RateLimited as e:
            deadline = pacer.pause_for_backoff(str(e))
            return {"op": "resolve-company", "ok": False, "keywords": keywords, "url": url,
                    "error": "rate_limited", "reason": str(e), "paused_until": deadline,
                    "count": 0, "companies": [], "quota": pacer.remaining()}


def discover(
    company: str,
    limit: int = 10,
    page: int = 1,
    *,
    profile: PacingProfile | None = None,
    state_dir: str | None = None,
    company_urn: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Discover ≤ `limit` current employees of `company` (US-REF-01).

    `company_urn` (resolved + disambiguated by the host, see `resolve_company`)
    scopes the People search by LinkedIn's `currentCompany` facet — the
    current-employees-only fix. When absent, discovery resolves the name itself
    (best-effort top hit) so the CLI stays runnable standalone, but the host
    always passes the confirmed urn."""
    if not company:
        raise VoyagerError("discover requires a company")
    if dry_run:
        scope = f"currentCompany={company_urn}" if company_urn else f"keywords={company!r}"
        return {
            "op": "discover", "ok": True, "dry_run": True, "company": company,
            "company_urn": company_urn,
            "plan": f"would search LinkedIn People scoped by {scope}, shortlist ≤{limit} "
                    f"(page {page}), enrich each via Voyager, verify current employer, "
                    f"sort degree-first",
            "contacts": [],
        }
    # Reads are metered and backoff-gated too (NFR-LI-02). This used to build no
    # Pacer at all: after a rate-limit signal the app kept running People
    # searches and bulk profile enrichment — precisely the reads the restriction
    # ladder watches — while LinkedIn was already telling us to stop.
    #
    # One discover() costs 1 CUL-counted People search plus up to ~2 profile
    # views per candidate (the profile fetch, plus a degree fallback), so both
    # budgets are checked up front and charged for what the run actually did.
    pacer = _pacer(profile, state_dir)
    allowed, reason = pacer.can_search()
    if not allowed:
        return {"op": "discover", "ok": False, "company": company, "count": 0,
                "contacts": [], "error": "cap_or_backoff", "reason": reason,
                "quota": pacer.remaining()}
    allowed, reason = pacer.can_view_profile()
    if not allowed:
        return {"op": "discover", "ok": False, "company": company, "count": 0,
                "contacts": [], "error": "cap_or_backoff", "reason": reason,
                "quota": pacer.remaining()}

    from .discovery import discover_company_contacts

    with _paced_session(pacer, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        try:
            pacer.record_search()
            # Reserve, don't just gate: a boolean check with 1 view remaining would
            # happily enrich a whole page and overshoot the day budget by the batch
            # size. Clamp the enrichment count to what the budget can actually pay.
            remaining_views = pacer.usage("profile_views").get("day_remaining")
            if remaining_views is not None:
                limit = max(1, min(limit, int(remaining_views)))
            contacts = discover_company_contacts(
                session, company, limit=limit, page=page, company_urn=company_urn
            )
            # Charge one profile view per candidate we actually enriched. Filtered-out
            # candidates still cost a request upstream, so this under-counts slightly;
            # `discovery.py` is where a precise per-fetch charge would go.
            pacer.record_profile_view(count=len(contacts))
            return {"op": "discover", "ok": True, "company": company, "company_urn": company_urn,
                    "count": len(contacts), "contacts": contacts, "quota": pacer.remaining()}
        except RateLimited as e:
            deadline = pacer.pause_for_backoff(str(e))
            return {"op": "discover", "ok": False, "company": company, "count": 0,
                    "contacts": [], "error": "rate_limited", "reason": str(e),
                    "paused_until": deadline, "quota": pacer.remaining()}


def search_jobs(
    keywords: str,
    location: str = "",
    *,
    start: int = 0,
    profile: PacingProfile | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Logged-in LinkedIn job search (read-only) → ONE page of
    `MAX_JOBS_PER_SEARCH` (25, LinkedIn's own page size) plain job dicts from
    offset `start`. NEW for finds-you-jobs — the one-shot job-discovery entry
    point; never a background scan source.

    One page per call is the invariant (maintainer directive 2026-07-30): no
    host request can turn one click into a multi-hundred-row authenticated
    crawl. `start` (host-owned Fresh-search/Next-page cursor, 2026-08-01)
    offsets that single page — LinkedIn's pagination is stateless, so any
    offset is one independent request of the same size.

    **Deliberately charges no CUL search budget.** Job search on the Jobs page is
    exempt from LinkedIn's Commercial Use Limit (help `a564226`), and it is the
    product's highest-volume read — metering it against the People-search budget
    would throttle the primary use case for zero real risk. It IS backoff-gated,
    and each page request is charged against the self-imposed `job_search_pages`
    pages/hour meter (ours, not LinkedIn's); a spent hourly budget refuses
    BEFORE any LinkedIn traffic, and is not a backoff — the next hour frees it.

    Returns `exhausted`: LinkedIn's result list ends at (or before) the page
    after this one — a partial page, or `start + 25 >= total`."""
    start = max(0, int(start))
    if not keywords:
        raise VoyagerError("search_jobs requires keywords")
    if dry_run:
        return {
            "op": "search-jobs", "ok": True, "dry_run": True,
            "keywords": keywords, "location": location, "start": start,
            "plan": f"would run logged-in LinkedIn job search for {keywords!r}"
                    f"{f' in {location!r}' if location else ''}, one page of "
                    f"{MAX_JOBS_PER_SEARCH} from offset {start}",
            "jobs": [], "total": 0, "exhausted": False,
        }
    # Backoff gates reads too: when LinkedIn has already told us to stop, the
    # loudest read path in the app must not keep going.
    pacer = _pacer(profile, state_dir)
    if pacer.is_paused():
        return {"op": "search-jobs", "ok": False, "keywords": keywords,
                "location": location, "start": start, "count": 0, "total": 0,
                "jobs": [], "error": "cap_or_backoff",
                "reason": pacer.paused_reason(),
                "exhausted": False, "quota": pacer.remaining()}
    allowed, reason = pacer.can_search_jobs()
    if not allowed:
        return {"op": "search-jobs", "ok": False, "keywords": keywords,
                "location": location, "start": start, "count": 0, "total": 0,
                "jobs": [], "error": "rate_limited_hourly", "reason": reason,
                "exhausted": False, "quota": pacer.remaining()}

    from .client import PlaywrightLinkedinAPI

    # The `finally` inside `_paced_session` persists the page charge and/or the
    # backoff pause to the shared ledger (a no-op if neither happened — save()
    # early-returns) and closes the browser.
    with _paced_session(pacer, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        try:
            session.ensure_browser()
            client = PlaywrightLinkedinAPI(session)
            # Charge on ATTEMPT, before issuing the request — a page that reaches
            # LinkedIn but then 429s or fails to parse still consumed a request
            # (same safe-direction rule as the send ops).
            pacer.record_search_page()
            page = client.search_jobs(
                keywords, location, start=start, count=MAX_JOBS_PER_SEARCH
            )
            total = int(page.get("total", 0) or 0)
            jobs = list(page.get("jobs", []))
            # A short page is LinkedIn's own end-of-results signal; a known total
            # that the next offset would meet or pass says the same thing.
            exhausted = len(jobs) < MAX_JOBS_PER_SEARCH or (
                0 < total <= start + MAX_JOBS_PER_SEARCH
            )
            return {"op": "search-jobs", "ok": True, "keywords": keywords,
                    "location": location, "start": start, "count": len(jobs),
                    "total": total, "jobs": jobs, "exhausted": exhausted,
                    "next_start": start + MAX_JOBS_PER_SEARCH}
        except RateLimited as e:
            # LinkedIn told us to stop on the loudest authenticated read path. Enter
            # backoff exactly like `discover` — search_jobs used to let RateLimited
            # propagate uncaught, so a 429 entered NO backoff and every later
            # Fresh/Next search kept hammering the throttled endpoint.
            deadline = pacer.pause_for_backoff(str(e))
            return {"op": "search-jobs", "ok": False, "keywords": keywords,
                    "location": location, "start": start, "count": 0, "total": 0,
                    "jobs": [], "error": "rate_limited",
                    "reason": str(e), "paused_until": deadline, "exhausted": False,
                    "quota": pacer.remaining()}


def _normalize_public_id(value: str) -> str:
    """Accept a bare slug or a full /in/ URL for --profile (live-dogfood fix
    2026-07-08: the app passed full URLs, producing /in/<full-url> 404s)."""
    if "/" in value or value.startswith("http"):
        slug = url_to_public_id(value)
        if slug:
            return slug
    return value


def send_connection(
    public_identifier: str,
    *,
    note: str = "",
    profile: PacingProfile | None = None,
    state_dir: str | None = None,
    linkedin_plan: str = "free",
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Send a cold connection request (with the drafted `note` when given —
    FR-NW-03; note-less otherwise, US-REF-04).

    Caps + backoff are enforced HERE before any network call. On LinkedIn's own
    weekly-cap UI, the pacer enters backoff and the op returns rate_limited.

    `linkedin_plan` conditions the personalized-note budget: the ~5/month note
    allowance exists only on free accounts, so `free` (the conservative default)
    gates note-bearing sends on the notes meter while `premium` never does.
    A note-bearing send on a free plan whose allowance is out is REFUSED, not
    silently stripped — the note is the referral ask; the user decides whether
    to send note-less."""
    if not public_identifier:
        raise VoyagerError("send-connection requires a public_identifier")
    public_identifier = _normalize_public_id(public_identifier)
    pacer = _pacer(profile, state_dir)
    charge_note = bool(note) and linkedin_plan != "premium"
    allowed, reason = pacer.can_send_invite()
    if allowed and charge_note:
        allowed, note_reason = pacer.can_use_note()
        if not allowed:
            reason = (
                f"{note_reason} — the free-plan personalized-note allowance is out; "
                "send note-less (ask via DM after they accept) or set your LinkedIn "
                "membership in Settings if this account has a paid plan"
            )

    if dry_run:
        return {
            "op": "send-connection", "ok": True, "dry_run": True,
            "public_identifier": public_identifier, "would_send": allowed,
            "with_note": bool(note), "note_chars": len(note),
            "blocked_reason": reason, "quota": pacer.remaining(),
        }
    if not allowed:
        # Refused by our own caps/backoff before touching LinkedIn (NFR-LI-02).
        return {
            "op": "send-connection", "ok": False, "sent": False,
            "public_identifier": public_identifier, "error": "cap_or_backoff",
            "reason": reason, "quota": pacer.remaining(),
        }

    # Space this send from the previous one (NFR-LI-01). Runs AFTER the cap check
    # so a refused send never sleeps, and BEFORE the browser launch so the pause
    # is real wall-clock silence rather than a gap between two page loads. The
    # runner dispatches a batch as N separate single-flight `send` ops, so this
    # is what keeps a batch from going out at machine pace.
    waited_s = pacer.wait_before_send()

    # Charge on ATTEMPT, before the browser launches (posture doc §4 fix 4): a
    # send that reached LinkedIn but died in post-send verification must not go
    # uncounted, or the ledger drifts low in the unsafe direction. Proven
    # no-sends below refund the charge.
    pacer.record_invite()
    if charge_note:
        pacer.record_note()
    pacer.save()

    from .actions import send_connection_request

    # `_paced_session`'s finally persists whatever is pending even on an
    # unexpected error mid-send: an unproven send stays charged (unsafe-direction
    # drift is the one we refuse). save() is idempotent — the paths below that
    # return already ran it.
    with _paced_session(pacer, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        try:
            status, note_outcome = send_connection_request(
                session, public_identifier, note=note
            )
        except ReachedConnectionLimit as e:
            # LinkedIn's weekly-cap dialog appeared INSTEAD of the invite going
            # out — a proven no-send, so give the attempt charges back.
            pacer.refund("invites")
            if charge_note:
                pacer.refund("notes")
            deadline = pacer.pause_for_backoff(str(e))
            pacer.save()
            return {
                "op": "send-connection", "ok": False, "sent": False,
                "public_identifier": public_identifier, "error": "rate_limited",
                "reason": str(e), "paused_until": deadline, "quota": pacer.remaining(),
            }
        if charge_note and note_outcome != "with_note":
            # The invite went out but the note did not (Premium upsell / markup
            # churn) — refund the note charge; on the upsell, LinkedIn itself
            # said the allowance is exhausted, which beats our estimate, so mark
            # the meter observed-exhausted for the rest of the window.
            pacer.refund("notes")
            if note_outcome == "noteless_upsell":
                pacer.saturate("notes")
        pacer.save()
        return {
            "op": "send-connection", "ok": True, "sent": True,
            "public_identifier": public_identifier, "status": status,
            # Surfaced (not just logged) so a dropped note is visible to the
            # host: the referral ask rides in the note (posture doc §6).
            "note_outcome": note_outcome,
            # What we ACTUALLY slept before this send. Was `delay_hint_s` — a
            # re-jittered number nothing consumed, which made the pacing look
            # implemented when it was not.
            "waited_s": round(waited_s, 1), "quota": pacer.remaining(),
        }


def send_dm(
    public_identifier: str,
    message: str,
    *,
    profile: PacingProfile | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Send a warm 1st-degree referral-ask DM (US-REF-10). DMs have their own
    daily/weekly budget (a policy cap of ours — no corroborated LinkedIn DM
    limit exists), are blocked during backoff, and never decrement the invite
    counter."""
    if not public_identifier:
        raise VoyagerError("send-dm requires a public_identifier")
    public_identifier = _normalize_public_id(public_identifier)
    if not message:
        raise VoyagerError("send-dm requires a message")
    pacer = _pacer(profile, state_dir)
    allowed, reason = pacer.can_send_dm()

    if dry_run:
        return {
            "op": "send-dm", "ok": True, "dry_run": True,
            "public_identifier": public_identifier, "would_send": allowed,
            "blocked_reason": reason, "message_chars": len(message),
            "quota": pacer.remaining(),
        }
    if not allowed:
        return {
            # `cap_or_backoff`, matching the invite path: since DMs gained a
            # budget this refusal can be a cap as well as a backoff.
            "op": "send-dm", "ok": False, "sent": False,
            "public_identifier": public_identifier, "error": "cap_or_backoff",
            "reason": reason, "quota": pacer.remaining(),
        }

    # Same inter-send spacing as the invite path — one account, one clock
    # (NFR-LI-01).
    waited_s = pacer.wait_before_send()

    # Charge on ATTEMPT (posture doc §4 fix 4); a proven no-send refunds below.
    pacer.record_dm()
    pacer.save()

    from .actions import send_dm as _send_dm

    # An unexpected error mid-send keeps its attempt charge (unsafe-direction
    # drift is the one we refuse) — `_paced_session`'s finally still saves;
    # save() is idempotent on the paths below that already ran it.
    with _paced_session(pacer, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        try:
            sent = _send_dm(session, public_identifier, message)
            if not sent:
                # actions.send_dm returned a definite "did not send" (no thread /
                # no compose) — a proven no-send, so the attempt charge goes back.
                pacer.refund("dms")
            pacer.save()
            return {
                "op": "send-dm", "ok": bool(sent), "sent": bool(sent),
                "public_identifier": public_identifier,
                "waited_s": round(waited_s, 1), "quota": pacer.remaining(),
            }
        except RateLimited as e:
            # The throttle hit before the message could go out — refund, then back off.
            pacer.refund("dms")
            deadline = pacer.pause_for_backoff(str(e))
            pacer.save()
            return {
                "op": "send-dm", "ok": False, "sent": False,
                "public_identifier": public_identifier, "error": "rate_limited",
                "reason": str(e), "paused_until": deadline,
            }


def contact_sync(
    public_identifier: str,
    *,
    profile: PacingProfile | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Read-only contact-status probe (FR-NW-15): connection degree + the 1:1
    thread's last-message direction/timestamp. NEVER writes to LinkedIn.

    Each probe is an authenticated profile read, so it charges the profile-view
    budget and is refused during backoff. That matters more here than anywhere
    else: the host drives one of these per tracked contact in a batch, so an
    unmetered sync could exhaust a whole day's read budget before the user sent a
    single invite — and a dashboard would still have read "healthy".

    Driven by the user pressing Sync (or opening the Networking surface, once per
    15 min) — there is no scheduler any more. `dry_run` plans only."""
    if not public_identifier:
        raise VoyagerError("contact-sync requires a public_identifier")
    public_identifier = _normalize_public_id(public_identifier)
    if dry_run:
        return {
            "op": "contact-sync", "ok": True, "dry_run": True,
            "public_identifier": public_identifier,
            "plan": "would read connection degree + last-message direction/timestamp "
                    "(read-only; no send)",
            "degree": None, "is_first_degree": False,
            "last_message_direction": None, "last_message_at": None,
        }
    pacer = _pacer(profile, state_dir)
    allowed, reason = pacer.can_view_profile()
    if not allowed:
        return {"op": "contact-sync", "ok": False,
                "public_identifier": public_identifier,
                "error": "cap_or_backoff", "reason": reason,
                "degree": None, "is_first_degree": False,
                "last_message_direction": None, "last_message_at": None,
                "quota": pacer.remaining()}

    from .actions import get_contact_sync_state

    with _paced_session(pacer, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        try:
            pacer.record_profile_view()
            state = get_contact_sync_state(session, public_identifier)
            return {"op": "contact-sync", "ok": True,
                    "public_identifier": public_identifier, **state,
                    "quota": pacer.remaining()}
        except RateLimited as e:
            # A block on contact 1 used to be logged and swallowed, so the host kept
            # hammering contacts 2-20 in the same batch. Entering backoff here stops
            # the whole batch, because every subsequent probe is refused.
            deadline = pacer.pause_for_backoff(str(e))
            return {"op": "contact-sync", "ok": False,
                    "public_identifier": public_identifier,
                    "error": "rate_limited", "reason": str(e), "paused_until": deadline,
                    "degree": None, "is_first_degree": False,
                    "last_message_direction": None, "last_message_at": None,
                    "quota": pacer.remaining()}


def status(
    public_identifier: str,
    *,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Report a contact's connection status: connected / pending / qualified."""
    if not public_identifier:
        raise VoyagerError("status requires a public_identifier")
    public_identifier = _normalize_public_id(public_identifier)
    if dry_run:
        return {
            "op": "status", "ok": True, "dry_run": True,
            "public_identifier": public_identifier,
            "plan": "would resolve connection degree via Voyager (UI fallback)",
        }
    from .actions import get_connection_status

    # No pacer: `status` is the one browser op that meters nothing (it is driven
    # by the host's own already-metered flows), so the scaffold only owns the
    # browser teardown here.
    with _paced_session(None, storage_state=storage_state,
                        user_data_dir=user_data_dir, headed=headed) as session:
        state = get_connection_status(session, public_identifier)
        return {"op": "status", "ok": True,
                "public_identifier": public_identifier, "status": state}
