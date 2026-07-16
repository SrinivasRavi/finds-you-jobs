# voyager_py/discovery.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Adapted from OpenOutreach `linkedin/actions/search.py` + `linkedin/browser/nav.py`
# (extract_in_urls) @ a7a9101. Upstream searched People by keyword and then ran
# `discover_and_enrich` into its Django DB. This fork keeps the search + /in/-URL
# extraction verbatim in spirit but returns a plain list of contact dicts (no DB),
# enriches the top N via the forked Voyager client, and sorts degree-first — the
# finds-you-jobs discovery contract (US-REF-01/02, FR-NW-02).
"""Discover potential referrers at a target company: search LinkedIn People
scoped to the company, shortlist ~N, enrich, sort degree-first."""

from __future__ import annotations

import json
import logging
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse

from .client import PlaywrightLinkedinAPI
from .company import company_id_from_urn
from .errors import ProfileInaccessibleError
from .session import AccountSession, goto_page
from .url_utils import url_to_public_id

logger = logging.getLogger("voyager_py.discovery")

PROFILE_LINK_SELECTOR = 'a[href*="/in/"]'


def _people_search_url(company: str, page: int = 1, company_urn: str | None = None) -> str:
    """The People-search URL.

    With `company_urn` we scope by the **`currentCompany` facet** (a company-id
    filter) — LinkedIn returns current employees of exactly that entity, so the
    old free-text `keywords=<name>` name-collision bug (Robert Hopper the person
    vs Hopper the company) is gone by construction. Only when no urn is available
    do we fall back to the legacy keyword search (best-effort, standalone-CLI)."""
    cid = company_id_from_urn(company_urn)
    if cid:
        # currentCompany takes a JSON array of company ids, e.g. ["162479"].
        params = {"currentCompany": json.dumps([cid]), "origin": "FACETED_SEARCH"}
    else:
        params = {"keywords": company, "origin": "FACETED_SEARCH"}
    if page > 1:
        params["page"] = str(page)
    return f"https://www.linkedin.com/search/results/people/?{urlencode(params)}"


def _profile_matches_company(parsed: dict, company_urn: str | None) -> bool:
    """Enrich-time re-verify (belt-and-suspenders on the URN-scoped search).

    Discovery is scoped by the target company's `currentCompany` URN, so LinkedIn
    has already vouched that these are current employees. This second check only
    catches search-index lag: a person who JUST LEFT is dropped when their profile
    now shows a *different* company entity. Critically it does NOT do loose company
    NAME matching — matching `company_name` by substring is what let "zip" pull in
    "RR ZIP LIMITED", "Zip Industries", "zipzapzoop" (unrelated namesake companies).
    We trust the URN scope and drop only on a positive URN *mismatch*:

    - No target URN (standalone-CLI keyword mode) → nothing to verify → keep.
    - Profile's current-position URN present & differs from target → drop (moved).
    - Current employer unreadable (privacy/parse gap) → keep (the scoped search
      already surfaced them; we never fabricate a mismatch)."""
    want_cid = company_id_from_urn(company_urn)
    if not want_cid:
        return True
    got_cid = company_id_from_urn((parsed.get("current_position") or {}).get("company_urn"))
    if got_cid:
        return got_cid == want_cid
    return True


def _extract_in_urls(page) -> list[str]:
    """Verbatim behaviour of nav.extract_in_urls: unique, cleaned /in/ URLs."""
    seen: set[str] = set()
    urls: list[str] = []
    for link in page.locator(PROFILE_LINK_SELECTOR).all():
        href = link.get_attribute("href")
        if href and "/in/" in href:
            full_url = urljoin(page.url, href.strip())
            clean = urlparse(full_url)._replace(query="", fragment="").geturl()
            if not url_to_public_id(clean):
                continue
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)
    return urls


def _next_page_url(current_url: str, page_num: int) -> str:
    parsed = urlparse(current_url)
    params = parse_qs(parsed.query)
    params["page"] = [str(page_num)]
    return parsed._replace(query=urlencode(params, doseq=True)).geturl()


def discover_company_contacts(
    session: AccountSession,
    company: str,
    limit: int = 10,
    page: int = 1,
    company_urn: str | None = None,
) -> list[dict]:
    """Return up to `limit` enriched CURRENT employees of `company`, degree-first.

    Scopes the People search by `company_urn`'s `currentCompany` facet
    (current-employees-only), enriches each candidate via Voyager, and drops any
    whose current employer doesn't verify against the target (search-index lag /
    unreadable employer). Each contact: {public_identifier, url, full_name,
    headline, current_title, current_company, connection_degree, is_first_degree}.
    Inaccessible profiles are skipped (never fatal)."""
    session.ensure_browser()
    search_url = _people_search_url(company, page=1, company_urn=company_urn)
    goto_page(
        session,
        action=lambda: session.page.goto(search_url),
        expected_url_pattern="/search/results/people/",
        error_message="Failed to reach People search results",
    )
    if page > 1:
        goto_page(
            session,
            action=lambda: session.page.goto(_next_page_url(session.page.url, page)),
            expected_url_pattern="/search/results/",
            error_message="Pagination failed",
        )

    urls = _extract_in_urls(session.page)
    logger.info(
        "discovery: %d /in/ candidates for %r (urn=%s)", len(urls), company, company_urn
    )

    api = PlaywrightLinkedinAPI(session=session)
    contacts: list[dict] = []
    for url in urls:
        public_id = url_to_public_id(url)
        if not public_id:
            continue
        try:
            parsed, _raw = api.get_profile(public_identifier=public_id)
        except ProfileInaccessibleError:
            logger.debug("skip inaccessible profile %s", public_id)
            continue
        if not parsed:
            continue
        if not _profile_matches_company(parsed, company_urn):
            logger.info(
                "discovery: dropping %s — current employer entity ≠ target %s (moved)",
                public_id, company_urn,
            )
            continue
        current = parsed.get("current_position") or {}
        degree = parsed.get("connection_degree")
        if degree is None:
            # FullProfileWithEntities omits the relationship for some profiles
            # (verified live 2026-07-08: valilenk → null while stasg7 → 3). The
            # TOPCARD decoration still carries it — one extra bounded call.
            try:
                degree = api.get_connection_degree(public_id)
            except Exception:  # noqa: BLE001 — degree is best-effort, never fatal
                degree = None
        if degree is None:
            # Genuinely unknown after linked + included-scan + TOPCARD — leave
            # NULL (warmth defaults to cold) but log it so a systemic degree-parse
            # regression is visible in the flight recorder, not silent (FR-NW-02).
            logger.info("discovery: connection_degree unknown for %s", public_id)
        contacts.append(
            {
                "public_identifier": parsed.get("public_identifier") or public_id,
                "url": parsed.get("url") or unquote(url),
                "full_name": parsed.get("full_name"),
                "headline": parsed.get("headline"),
                "current_title": current.get("title"),
                "current_company": current.get("company_name"),
                "connection_degree": degree,
                "is_first_degree": degree == 1,
            }
        )
        if len(contacts) >= limit:
            break

    # Sort degree-first (1st → 2nd → 3rd → unknown), stable within degree
    # (relevance ordering from search is preserved). FR-NW-02.
    contacts.sort(key=lambda c: (c["connection_degree"] is None, c["connection_degree"] or 99))
    return contacts
