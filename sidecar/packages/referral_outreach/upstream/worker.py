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

import logging
import random
import time
from pathlib import Path

from .errors import RateLimited, ReachedConnectionLimit, VoyagerError
from .url_utils import url_to_public_id
from .pacing import PAGE_PAUSE_RANGE_S, Pacer, resolve_tier

logger = logging.getLogger("voyager_py.worker")


def _pacer(tier_name: str | None, state_dir: str | None) -> Pacer:
    sd = Path(state_dir) if state_dir else None
    return Pacer(resolve_tier(tier_name), state_dir=sd)


def quota(tier: str | None = None, state_dir: str | None = None) -> dict:
    """Report the live remaining quota + backoff state (FR-NW-01/04). No browser."""
    pacer = _pacer(tier, state_dir)
    return {"op": "quota", "ok": True, "quota": pacer.remaining()}


def resume(tier: str | None = None, state_dir: str | None = None) -> dict:
    """Clear the voyager-owned backoff pause (Settings → Networking manual resume,
    FR-NW-05 / NFR-LI-03). No browser, no network — just resets the ledger flag."""
    pacer = _pacer(tier, state_dir)
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
    from .company import resolve_company as _resolve
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        companies = _resolve(session, keywords, url=url, limit=limit, prefer_domain=prefer_domain)
        return {"op": "resolve-company", "ok": True, "keywords": keywords, "url": url,
                "prefer_domain": prefer_domain, "count": len(companies),
                "companies": companies}
    finally:
        session.close()


def discover(
    company: str,
    limit: int = 10,
    page: int = 1,
    *,
    tier: str | None = None,
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
    pacer = _pacer(tier, state_dir)
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
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        pacer.record_search()
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
    finally:
        pacer.save()
        session.close()


def search_jobs(
    keywords: str,
    location: str = "",
    limit: int = 50,
    *,
    tier: str | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Logged-in LinkedIn job search (read-only) → up to `limit` normalized-ish
    plain job dicts. NEW for finds-you-jobs — the one-shot job-discovery entry
    point; never a background scan source.

    **Deliberately charges no CUL search budget.** Job search on the Jobs page is
    exempt from LinkedIn's Commercial Use Limit (help `a564226`), and it is the
    product's highest-volume read — metering it against the People-search budget
    would throttle the primary use case for zero real risk. It IS backoff-gated
    and paced: it used to be neither, making one click the largest unpaced
    authenticated burst in the codebase (~120 requests across 12 launches).

    Paginates in pages of 25 (LinkedIn's page size) until `limit` or exhaustion.
    A page failure keeps what earlier pages returned (rank-don't-gate)."""
    if not keywords:
        raise VoyagerError("search_jobs requires keywords")
    if dry_run:
        return {
            "op": "search-jobs", "ok": True, "dry_run": True,
            "keywords": keywords, "location": location,
            "plan": f"would run logged-in LinkedIn job search for {keywords!r}"
                    f"{f' in {location!r}' if location else ''}, page through ≤{limit} results",
            "jobs": [], "total": 0,
        }
    # Backoff gates reads too: when LinkedIn has already told us to stop, the
    # loudest read path in the app must not keep going.
    pacer = _pacer(tier, state_dir)
    if pacer.is_paused():
        _, reason = pacer.check("profile_views")
        return {"op": "search-jobs", "ok": False, "keywords": keywords,
                "location": location, "count": 0, "total": 0, "jobs": [],
                "error": "cap_or_backoff", "reason": reason,
                "quota": pacer.remaining()}

    from .client import PlaywrightLinkedinAPI
    from .session import AccountSession

    _PAGE = 25
    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    jobs: list[dict] = []
    total = 0
    try:
        session.ensure_browser()
        client = PlaywrightLinkedinAPI(session)
        seen: set[str] = set()
        for start in range(0, max(limit, 1), _PAGE):
            if start:
                # Pace between pages. Reusing the send jitter band would be far
                # too slow for a read the user is waiting on, so this is a short
                # human-scale pause — the point is that consecutive pages are not
                # issued at machine speed.
                time.sleep(random.uniform(*PAGE_PAUSE_RANGE_S))
            page = client.search_jobs(keywords, location, start=start, count=_PAGE)
            total = page.get("total", total) or total
            batch = page.get("jobs", [])
            if not batch:
                break
            for job in batch:
                jid = job.get("id")
                if jid and jid not in seen:
                    seen.add(jid)
                    jobs.append(job)
            if len(jobs) >= limit:
                jobs = jobs[:limit]
                break
        return {"op": "search-jobs", "ok": True, "keywords": keywords,
                "location": location, "count": len(jobs), "total": total, "jobs": jobs}
    finally:
        session.close()


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
    tier: str | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Send a cold connection request (with the drafted `note` when given —
    FR-NW-03; note-less otherwise, US-REF-04).

    Caps + backoff are enforced HERE before any network call. On LinkedIn's own
    weekly-cap UI, the pacer enters backoff and the op returns rate_limited."""
    if not public_identifier:
        raise VoyagerError("send-connection requires a public_identifier")
    public_identifier = _normalize_public_id(public_identifier)
    pacer = _pacer(tier, state_dir)
    allowed, reason = pacer.can_send_invite()

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

    from .actions import send_connection_request
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        try:
            status = send_connection_request(session, public_identifier, note=note)
        except ReachedConnectionLimit as e:
            deadline = pacer.pause_for_backoff(str(e))
            pacer.save()
            return {
                "op": "send-connection", "ok": False, "sent": False,
                "public_identifier": public_identifier, "error": "rate_limited",
                "reason": str(e), "paused_until": deadline, "quota": pacer.remaining(),
            }
        pacer.record_invite()
        pacer.save()
        return {
            "op": "send-connection", "ok": True, "sent": True,
            "public_identifier": public_identifier, "status": status,
            # What we ACTUALLY slept before this send. Was `delay_hint_s` — a
            # re-jittered number nothing consumed, which made the pacing look
            # implemented when it was not.
            "waited_s": round(waited_s, 1), "quota": pacer.remaining(),
        }
    finally:
        session.close()


def send_dm(
    public_identifier: str,
    message: str,
    *,
    tier: str | None = None,
    state_dir: str | None = None,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Send a warm 1st-degree referral-ask DM (US-REF-10). DMs are uncapped but
    still blocked during backoff; they never decrement the invite counter."""
    if not public_identifier:
        raise VoyagerError("send-dm requires a public_identifier")
    public_identifier = _normalize_public_id(public_identifier)
    if not message:
        raise VoyagerError("send-dm requires a message")
    pacer = _pacer(tier, state_dir)
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
            "op": "send-dm", "ok": False, "sent": False,
            "public_identifier": public_identifier, "error": "backoff",
            "reason": reason, "quota": pacer.remaining(),
        }

    # Same inter-send spacing as the invite path — one account, one clock
    # (NFR-LI-01). DMs are uncapped but they are still outbound traffic.
    waited_s = pacer.wait_before_send()

    from .actions import send_dm as _send_dm
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        sent = _send_dm(session, public_identifier, message)
        if sent:
            pacer.record_dm()
            pacer.save()
        return {
            "op": "send-dm", "ok": bool(sent), "sent": bool(sent),
            "public_identifier": public_identifier,
            "waited_s": round(waited_s, 1), "quota": pacer.remaining(),
        }
    except RateLimited as e:
        deadline = pacer.pause_for_backoff(str(e))
        pacer.save()
        return {
            "op": "send-dm", "ok": False, "sent": False,
            "public_identifier": public_identifier, "error": "rate_limited",
            "reason": str(e), "paused_until": deadline,
        }
    finally:
        session.close()


def contact_sync(
    public_identifier: str,
    *,
    tier: str | None = None,
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
    pacer = _pacer(tier, state_dir)
    allowed, reason = pacer.can_view_profile()
    if not allowed:
        return {"op": "contact-sync", "ok": False,
                "public_identifier": public_identifier,
                "error": "cap_or_backoff", "reason": reason,
                "degree": None, "is_first_degree": False,
                "last_message_direction": None, "last_message_at": None,
                "quota": pacer.remaining()}

    from .actions import get_contact_sync_state
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
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
    finally:
        pacer.save()
        session.close()


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
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        state = get_connection_status(session, public_identifier)
        return {"op": "status", "ok": True,
                "public_identifier": public_identifier, "status": state}
    finally:
        session.close()
