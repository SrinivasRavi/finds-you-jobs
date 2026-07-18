# voyager_py/jobs.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW in finds-you-jobs — NOT forked from OpenOutreach (its OpenOutreach base is
# a networking/outreach tool with no job-search feature). This module is
# finds-you-jobs-authored code that lives in the GPL subtree because it builds on
# the GPL Voyager fetch-in-page client (client.py); as a derivative of that GPL
# code it is GPL-3.0-only. The LinkedIn `voyagerJobsDashJobCards` endpoint shape
# (decoration id, `q=jobSearch`, the `query=(…)` grammar, the normalized
# data/included response) was DERIVED by observing LinkedIn's own logged-in web
# client fire the request — no third-party code was copied. See provenance.md.
"""Parse LinkedIn's logged-in Voyager jobs-search response into plain job dicts.

The response is the `application/vnd.linkedin.normalized+json+2.1` shape: an
ordered `data.elements[]` of JobCard references plus an `included[]` pool of
entities. Each search result is a `JobPostingCard` carrying everything we need
inline — title, company (primaryDescription), location (secondaryDescription),
and the stable `jobPostingUrn`. We return plain dicts (NOT the scraper module's
NormalizedJob) so this GPL subtree never imports the AGPL app/module layers —
the host op maps these dicts into the shared discovery funnel.
"""

from __future__ import annotations

import re
from typing import Any

_JOB_ID_RE = re.compile(r"urn:li:fsd_jobPosting:(\d+)")
_CARD_TYPE = "com.linkedin.voyager.dash.jobs.JobPostingCard"


def _text(node: Any) -> str:
    """A voyager TextViewModel's `.text`, else ""."""
    if isinstance(node, dict):
        value = node.get("text")
        if isinstance(value, str):
            return value.strip()
    return ""


def _job_id(urn: str) -> str:
    m = _JOB_ID_RE.search(urn or "")
    return m.group(1) if m else ""


def parse_job_search_response(data: dict) -> dict:
    """`{"jobs": [ {id,url,title,company,location}, … ], "total": int}`.

    Order follows `data.elements` (LinkedIn's own relevance order). Cards with
    no id or title are skipped (nothing to act on). `total` is the search's
    reported result count (paging.total), for the host's per-source report.
    """
    if not isinstance(data, dict):
        return {"jobs": [], "total": 0}

    # Index the JobPostingCards by their entityUrn so we can resolve elements
    # in relevance order. Two card flavors can appear (JOBS_SEARCH + prefetch);
    # key on the full entityUrn and let the elements list pick the right ones.
    included = data.get("included") or []
    cards_by_urn: dict[str, dict] = {}
    for entity in included:
        if isinstance(entity, dict) and entity.get("$type") == _CARD_TYPE:
            urn = entity.get("entityUrn")
            if isinstance(urn, str):
                cards_by_urn[urn] = entity

    payload = data.get("data") or {}
    elements = payload.get("elements") if isinstance(payload, dict) else None
    ordered_cards: list[dict] = []
    if isinstance(elements, list):
        for el in elements:
            if not isinstance(el, dict):
                continue
            ref = (el.get("jobCardUnion") or {}).get("*jobPostingCard")
            card = cards_by_urn.get(ref) if isinstance(ref, str) else None
            if card is not None:
                ordered_cards.append(card)
    if not ordered_cards:
        # Fallback: no usable elements order — take the search cards as-is.
        ordered_cards = [c for u, c in cards_by_urn.items() if "JOBS_SEARCH" in u]

    jobs: list[dict] = []
    seen: set[str] = set()
    for card in ordered_cards:
        job_id = _job_id(str(card.get("jobPostingUrn") or ""))
        title = _text(card.get("title"))
        if not job_id or not title or job_id in seen:
            continue
        seen.add(job_id)
        jobs.append(
            {
                "id": job_id,
                "url": f"https://www.linkedin.com/jobs/view/{job_id}",
                "title": title,
                "company": _text(card.get("primaryDescription")),
                "location": _text(card.get("secondaryDescription")),
            }
        )

    total = 0
    paging = payload.get("paging") if isinstance(payload, dict) else None
    if isinstance(paging, dict) and isinstance(paging.get("total"), int):
        total = paging["total"]
    return {"jobs": jobs, "total": total}
