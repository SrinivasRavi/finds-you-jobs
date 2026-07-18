# voyager_py/worker.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code for the finds-you-jobs fork (GPL subtree). The operation layer: it ties
# the pacing/caps ledger (pacing.py) to the live browser actions (session.py,
# actions.py, discovery.py) and returns plain dicts the CLI serialises to JSON.
# Caps + backoff are ENFORCED here, inside the subprocess (ROADMAP §66,
# NFR-LI-01/02/03) — the MIT host never re-implements them.
"""The bounded operations: discover, send-connection, send-dm, status, quota,
contact-sync, login, and search-jobs (the read-only logged-in job search —
finds-you-jobs discovery-expansion #6). Every operation supports dry_run (no
browser, no network — plan only)."""

from __future__ import annotations

import logging
from pathlib import Path

from .errors import RateLimited, ReachedConnectionLimit, VoyagerError
from .url_utils import url_to_public_id
from .pacing import Pacer, resolve_tier, send_delay_seconds

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
    from .discovery import discover_company_contacts
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        contacts = discover_company_contacts(
            session, company, limit=limit, page=page, company_urn=company_urn
        )
        return {"op": "discover", "ok": True, "company": company, "company_urn": company_urn,
                "count": len(contacts), "contacts": contacts}
    finally:
        session.close()


def search_jobs(
    keywords: str,
    location: str = "",
    limit: int = 50,
    *,
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Logged-in LinkedIn job search (read-only) → up to `limit` normalized-ish
    plain job dicts. NEW for finds-you-jobs — the one-shot job-discovery entry
    point; never a background scan source. Read-only: no send, no caps decrement
    (caps govern outreach *sends*, not reads — same stance as `contact_sync`).

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
            "delay_hint_s": round(send_delay_seconds(), 1), "quota": pacer.remaining(),
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
            "public_identifier": public_identifier, "quota": pacer.remaining(),
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
    storage_state: str | None = None,
    user_data_dir: str | None = None,
    headed: bool = False,
    dry_run: bool = False,
) -> dict:
    """Read-only contact-status probe (FR-NW-15): connection degree + the 1:1
    thread's last-message direction/timestamp. NEVER writes to LinkedIn.

    The MIT host's `contact_sync` scheduler tick drives one of these per tracked
    contact (batched small, gentle) and maps the result onto the kanban
    transitions. `dry_run` plans only (no browser, no network)."""
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
    from .actions import get_contact_sync_state
    from .session import AccountSession

    session = AccountSession(
        storage_state_path=storage_state, headed=headed, user_data_dir=user_data_dir
    )
    try:
        state = get_contact_sync_state(session, public_identifier)
        return {"op": "contact-sync", "ok": True,
                "public_identifier": public_identifier, **state}
    finally:
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
