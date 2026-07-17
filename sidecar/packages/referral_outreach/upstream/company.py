# voyager_py/company.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code for the finds-you-jobs fork (GPL subtree; carried from the prior repository). Company-entity resolution:
# turn a company NAME into ranked LinkedIn company ENTITIES (each with its URN),
# so People discovery can scope by the `currentCompany` facet instead of a
# free-text name keyword (the L0→L2 fix — see docs/referral-outreach-discovery-
# design.md). Zero LLM; one typeahead HTTP call, plus an optional company-detail
# call per candidate ONLY when the caller passes a domain to anchor on.
"""Resolve a company name → LinkedIn company entities (URN + metadata).

The parsing is split into pure functions (`parse_typeahead_hits`,
`parse_company_website`) that are fixture-tested offline, behind a thin live
wrapper (`resolve_company`) that does the actual authenticated HTTP via the
in-page fetch client. The exact live JSON shapes are validated by the
`resolve-company` CLI probe against the maintainer's session; every parser is
deliberately defensive and **degrades to "no match"** (never crashes, never
guesses) when a field is missing — a miss just routes the app to user-confirm.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse

from .client import PlaywrightLinkedinAPI

logger = logging.getLogger("voyager_py.company")

# Company name → candidates is done by scraping the **company search results
# page** (`/search/results/companies/?keywords=…`) for `/company/<vanity>` links —
# the exact HTML-scrape mechanism `discovery.py` uses for people search, which is
# proven to work in the authenticated session. Each vanity is then resolved to its
# entity (URN + metadata) via the company-detail API below (the same call the
# reliable "paste a company URL" path uses). We deliberately do NOT depend on the
# Voyager typeahead JSON API — it proved flaky live (non-200s), leaving the picker
# empty. (`parse_typeahead_hits` is retained + tested for that endpoint shape but
# is not wired into the live path.)
_COMPANY_SEARCH_URL = "https://www.linkedin.com/search/results/companies/"
_COMPANY_LINK_SELECTOR = 'a[href*="/company/"]'

# universalName → company detail (URN, name, industry, logo, website).
_COMPANY_BY_UNIVERSAL_NAME = "https://www.linkedin.com/voyager/api/organization/companies"


# ---------------------------------------------------------------------------
# Pure helpers (fixture-tested; no I/O)
# ---------------------------------------------------------------------------


def company_id_from_urn(urn: str | None) -> str:
    """`urn:li:fsd_company:162479` / `urn:li:company:162479` → `162479`.

    Returns "" for anything that isn't a company urn (defensive — a bad urn must
    never become a garbage search facet)."""
    if not urn or not isinstance(urn, str):
        return ""
    tail = urn.rsplit(":", 1)[-1]
    return tail if tail.isdigit() else ""


def registrable_domain(url_or_host: str | None) -> str:
    """Best-effort registrable domain from a URL or bare host, lowercased.

    `https://www.Abnormal.ai/careers` → `abnormal.ai`; `careers.airbnb.com` →
    `airbnb.com`. Not a full public-suffix parse (we carry no PSL dep) — it takes
    the last two labels, which is right for the vast majority of employer domains
    and simply over/under-matches conservatively on multi-part TLDs (e.g.
    `bjak.my` stays `bjak.my`, `foo.co.uk` collapses to `co.uk`). A wrong guess
    only costs a user-confirm, never a wrong pick."""
    if not url_or_host:
        return ""
    raw = url_or_host.strip().lower()
    if "//" not in raw:
        raw = "//" + raw
    host = urlparse(raw).netloc or ""
    host = host.split("@")[-1].split(":")[0]  # strip creds + port
    if host.startswith("www."):
        host = host[4:]
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return ".".join(labels)
    return ".".join(labels[-2:])


def domains_match(a: str | None, b: str | None) -> bool:
    """True when two URLs/hosts share a registrable domain (both non-empty)."""
    da, db = registrable_domain(a), registrable_domain(b)
    return bool(da) and da == db


def _first(d: dict, *keys: str):
    """First present, truthy value among `keys` in `d` (defensive field pick)."""
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return None


def _text(value) -> str:
    """A Voyager text node may be a bare str or `{"text": "..."}`. → str."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return ""


def _vanity_from_nav(*candidates: str | None) -> str:
    """Pull the `/company/<vanity>` slug out of a navigation/action URL."""
    for c in candidates:
        if not c or "/company/" not in c:
            continue
        tail = c.split("/company/", 1)[1]
        vanity = tail.split("/", 1)[0].split("?", 1)[0]
        if vanity:
            return vanity
    return ""


def _hit_from_typeahead_element(el: dict) -> dict | None:
    """Map one typeahead `elements[]` entry onto a company-hit dict, or None.

    Handles both shapes seen on Voyager typeahead:
      A) `hitInfo` → `com.linkedin.voyager.typeahead.TypeaheadCompany` {id,name,industry,logo}
      B) dash-style {targetUrn/entityUrn, title/text, subtext, navigationUrl}
    """
    if not isinstance(el, dict):
        return None

    # Shape A: hitInfo union whose key contains "TypeaheadCompany".
    hit_info = el.get("hitInfo")
    if isinstance(hit_info, dict):
        for key, info in hit_info.items():
            if "Company" in key and isinstance(info, dict):
                cid = info.get("id")
                urn = info.get("entityUrn") or (f"urn:li:company:{cid}" if cid else "")
                name = _text(_first(info, "name") or "")
                if not (urn or name):
                    continue
                return {
                    "urn": urn or "",
                    "company_id": company_id_from_urn(urn) or (str(cid) if cid else ""),
                    "name": name,
                    "vanity": info.get("companyPublicIdentifier")
                    or _vanity_from_nav(info.get("navigationUrl")),
                    "industry": _text(_first(info, "industry") or ""),
                }

    # Shape B: dash element with a target/entity urn + title + subtext.
    urn = _first(el, "targetUrn", "entityUrn", "objectUrn") or ""
    name = _text(_first(el, "title", "text", "name"))
    if not (urn or name):
        return None
    nav = el.get("navigationUrl")
    action = el.get("actionTarget")
    vanity = _vanity_from_nav(
        nav if isinstance(nav, str) else _text(nav),
        action if isinstance(action, str) else None,
    )
    return {
        "urn": urn if isinstance(urn, str) else "",
        "company_id": company_id_from_urn(urn if isinstance(urn, str) else ""),
        "name": name,
        "vanity": vanity,
        "industry": _text(el.get("subtext")),
    }


def parse_typeahead_hits(payload: dict, *, limit: int = 5) -> list[dict]:
    """Ranked company hits from a typeahead response (order preserved).

    Only keeps hits that carry a usable company id (a URN we can scope People by)
    OR a vanity we can look the company up by — anything else is unusable and
    dropped. Never raises on a malformed payload; returns [] instead."""
    if not isinstance(payload, dict):
        return []
    elements = payload.get("elements")
    if not isinstance(elements, list):
        return []
    hits: list[dict] = []
    seen: set[str] = set()
    for el in elements:
        hit = _hit_from_typeahead_element(el)
        if not hit:
            continue
        if not hit.get("company_id") and not hit.get("vanity"):
            continue
        dedup_key = hit.get("company_id") or hit.get("vanity") or hit.get("name")
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        hit.setdefault("logo_url", "")
        hit.setdefault("website", "")
        hit.setdefault("domain_match", False)
        hits.append(hit)
        if len(hits) >= limit:
            break
    return hits


def _walk_strings(node, keys: tuple[str, ...]) -> str:
    """Depth-first search of a nested dict/list for the first truthy value under
    any of `keys`. Used to fish a website URL out of a company-detail payload
    whose exact nesting we don't want to hard-code (decoration-version churn)."""
    if isinstance(node, dict):
        for k in keys:
            v = node.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for v in node.values():
            found = _walk_strings(v, keys)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _walk_strings(v, keys)
            if found:
                return found
    return ""


def parse_company_website(payload: dict) -> str:
    """Best-effort website URL from a company-detail payload; "" when absent.

    Tries the well-known keys anywhere in the graph (`companyPageUrl` is the
    canonical one; others are fallbacks across decoration versions)."""
    if not isinstance(payload, dict):
        return ""
    return _walk_strings(
        payload, ("companyPageUrl", "websiteUrl", "website", "companyPageUrlV2")
    )


def vanity_from_company_url(url: str | None) -> str:
    """`https://www.linkedin.com/company/theziphq/` → `theziphq`; "" otherwise.

    Accepts the /company/ and /school/ forms and tolerates trailing paths/queries.
    This is the authoritative L2 anchor: a company URL the user pastes resolves to
    exactly one entity (no typeahead guessing)."""
    if not url or "/" not in url:
        return url.strip() if url and "/" not in url else ""
    lowered = url.strip()
    for marker in ("/company/", "/school/", "/showcase/"):
        if marker in lowered:
            tail = lowered.split(marker, 1)[1]
            return tail.split("/", 1)[0].split("?", 1)[0].strip()
    return ""


def _vector_image_url(vector: dict | None, target: int = 200) -> str:
    """A Voyager `vectorImage` (rootUrl + artifacts) → a displayable URL, "" if none.
    Picks the artifact nearest `target` px. Mirrors voyager.py's helper (kept local
    to avoid importing a private symbol across modules)."""
    if not isinstance(vector, dict):
        return ""
    root = vector.get("rootUrl")
    artifacts = vector.get("artifacts") or []
    if not root or not isinstance(artifacts, list) or not artifacts:
        return ""
    chosen = min(artifacts, key=lambda a: abs((a or {}).get("width", 0) - target))
    seg = (chosen or {}).get("fileIdentifyingUrlPathSegment", "")
    return f"{root}{seg}" if seg else ""


def _logo_url(el: dict) -> str:
    """Best-effort company logo URL from a company entity ("" when absent)."""
    logo = el.get("logo")
    if isinstance(logo, dict):
        # Common shapes: {"vectorImage": …} or {"image": {"...": {"vectorImage": …}}}.
        vi = logo.get("vectorImage")
        if vi:
            return _vector_image_url(vi)
        for v in logo.values():
            if isinstance(v, dict) and v.get("vectorImage"):
                return _vector_image_url(v["vectorImage"])
    return ""


def _walk_logo(node) -> str:
    """Depth-first search for the first `vectorImage` anywhere → a logo URL."""
    if isinstance(node, dict):
        vi = node.get("vectorImage")
        if isinstance(vi, dict):
            url = _vector_image_url(vi)
            if url:
                return url
        for v in node.values():
            found = _walk_logo(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _walk_logo(v)
            if found:
                return found
    return ""


def _company_entity_from_element(el: dict) -> dict | None:
    """Map one company-detail `elements[]`/entity onto a hit dict, or None."""
    if not isinstance(el, dict):
        return None
    urn = _first(el, "entityUrn", "objectUrn", "*company", "trackingUrn") or ""
    urn = urn if isinstance(urn, str) else ""
    name = _text(_first(el, "name", "localizedName") or "")
    if not (company_id_from_urn(urn) or name):
        return None
    industry = ""
    industries = el.get("companyIndustries") or el.get("industries")
    if isinstance(industries, list) and industries:
        first = industries[0]
        industry = _text(first.get("localizedName") if isinstance(first, dict) else first)
    return {
        "urn": urn,
        "company_id": company_id_from_urn(urn),
        "name": name,
        "vanity": el.get("universalName") or _vanity_from_nav(_text(el.get("url"))),
        "industry": industry,
        "logo_url": _logo_url(el),
        "website": parse_company_website(el),
        "domain_match": False,
    }


def _looks_like_company_entity(node: dict) -> bool:
    """A dict that is a company entity itself — has a universalName, or an
    entityUrn/objectUrn naming a `:company:`/`:fsd_company:` URN."""
    if not isinstance(node, dict):
        return False
    if isinstance(node.get("universalName"), str) and node["universalName"].strip():
        return True
    for key in ("entityUrn", "objectUrn", "trackingUrn"):
        urn = node.get(key)
        if isinstance(urn, str) and (":company:" in urn or ":fsd_company:" in urn):
            return True
    return False


def _company_name_from_payload(node) -> str:
    """The display name of the first COMPANY entity found anywhere in a
    company-detail payload.

    Deliberately narrower than a bare `_walk_strings(payload, ("name",
    "localizedName"))`: Voyager detail responses also carry *industry* entities
    whose `localizedName` (e.g. "Software Development") would win a blind
    depth-first walk — the 2026-07-12 live bug where every confirm-candidate was
    named "Software Development". Only a node that looks like a company entity
    may donate a name, and industry containers are never descended into.
    """
    if isinstance(node, dict):
        if _looks_like_company_entity(node):
            name = _text(_first(node, "name", "localizedName") or "")
            if name:
                return name
        for key, value in node.items():
            # Industry subtrees hold localizedName strings that are NOT names.
            if key in ("companyIndustries", "industries", "industry", "industryV2Taxonomy"):
                continue
            found = _company_name_from_payload(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _company_name_from_payload(value)
            if found:
                return found
    return ""


def _industry_from_payload(node) -> str:
    """The first industry localizedName found under an industry container
    anywhere in the payload — the inverse restriction of
    `_company_name_from_payload` (here ONLY industry subtrees may donate)."""
    if isinstance(node, dict):
        for key in ("companyIndustries", "industries"):
            container = node.get(key)
            if isinstance(container, list) and container:
                first = container[0]
                text = _text(first.get("localizedName") if isinstance(first, dict) else first)
                if text:
                    return text
        for value in node.values():
            found = _industry_from_payload(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _industry_from_payload(value)
            if found:
                return found
    return ""


def _humanize_vanity(vanity: str) -> str:
    """`instabase-inc` → `Instabase Inc` — a readable display name when the
    company-detail parse can't find the real one."""
    words = [w for w in vanity.replace("_", "-").split("-") if w]
    return " ".join(w[:1].upper() + w[1:] for w in words)


def parse_company_entity(payload: dict) -> dict | None:
    """The single company hit from a universalName company-detail response.

    Returns {urn, company_id, name, vanity, industry, logo_url, website,
    domain_match} for the first company entity carrying a usable company id, else
    None. Never raises. If the primary element has no name/logo, we scan the whole
    payload (name/logo can live on a nested `included` entity across decoration
    versions)."""
    if not isinstance(payload, dict):
        return None
    hit: dict | None = None
    elements = payload.get("elements")
    if isinstance(elements, list):
        for el in elements:
            cand = _company_entity_from_element(el)
            if cand and cand.get("company_id"):
                hit = cand
                break
    if hit is None:
        # Some decorations nest the company under `data`/`included` — scan those.
        for key in ("data", "included"):
            node = payload.get(key)
            candidates = node if isinstance(node, list) else [node] if isinstance(node, dict) else []
            for el in candidates:
                cand = _company_entity_from_element(el)
                if cand and cand.get("company_id"):
                    hit = cand
                    break
            if hit is not None:
                break
    if hit is None:
        return None
    # Belt-and-suspenders: if the primary element lacked a display name/logo, fish
    # them out of anywhere in the payload — but only from a node that IS a
    # company entity (a blind key-walk grabbed industry localizedNames live).
    if not hit.get("name"):
        hit["name"] = _company_name_from_payload(payload)
    if not hit.get("logo_url"):
        hit["logo_url"] = _walk_logo(payload)
    if not hit.get("industry"):
        hit["industry"] = _industry_from_payload(payload)
    return hit


# ---------------------------------------------------------------------------
# Live wrapper (thin; the parsing above carries the logic)
# ---------------------------------------------------------------------------


def _fetch_company_detail(api: PlaywrightLinkedinAPI, vanity: str) -> dict | None:
    """One company-detail call by universalName → the raw JSON, or None on miss."""
    if not vanity:
        return None
    try:
        params = {"q": "universalName", "universalName": vanity}
        res = api.get(f"{_COMPANY_BY_UNIVERSAL_NAME}?{urlencode(params)}")
        if not res.ok:
            logger.info("company-detail HTTP %s for %r", res.status, vanity)
            return None
        return res.json()
    except Exception as e:  # noqa: BLE001 — best-effort, never fatal
        logger.debug("company detail lookup failed for %r: %s", vanity, e)
        return None


def _dump_debug(session, label: str, payload) -> None:
    """Persist a raw LinkedIn response to `<storage-state-dir>/debug/` so a parser
    miss can be diagnosed from real data WITHOUT re-running live (the maintainer
    shares the file, we tighten the parser). Local-only; never uploaded; best-effort."""
    try:
        base_dir = getattr(session, "storage_state_path", None)
        base = (Path(base_dir).parent / "debug") if base_dir else (Path.cwd() / "linkedin-debug")
        base.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
        dest = base / f"typeahead-{safe}-{time.strftime('%Y%m%dT%H%M%S')}.json"
        dest.write_text(json.dumps(payload, indent=2, default=str)[:200_000], encoding="utf-8")
        logger.info("saved raw company-resolve response → %s", dest)
    except Exception as e:  # noqa: BLE001 — a capture failure must not mask the real flow
        logger.debug("debug dump failed: %s", e)


def resolve_company_by_vanity(session, vanity: str) -> dict | None:
    """Resolve a company's LinkedIn vanity (`theziphq`) → its entity (URN + meta).

    The authoritative L2 path: the user pasted the company's LinkedIn URL, so there
    is exactly one right answer and no typeahead guessing. One company-detail call."""
    if not vanity or not vanity.strip():
        return None
    session.ensure_browser()
    api = PlaywrightLinkedinAPI(session=session)
    payload = _fetch_company_detail(api, vanity.strip())
    if payload is None:
        return None
    hit = parse_company_entity(payload)
    if hit is None:
        _dump_debug(session, f"byvanity-{vanity}", payload)
        return None
    if not hit.get("vanity"):
        hit["vanity"] = vanity.strip()
    if not hit.get("name"):
        # Couldn't read the real display name — capture the shape + humanize the
        # vanity so the picker shows something meaningful instead of blank.
        _dump_debug(session, f"noname-{vanity}", payload)
        hit["name"] = _humanize_vanity(hit["vanity"])
    return hit


def _company_search_url(keywords: str) -> str:
    params = {"keywords": keywords.strip(), "origin": "SWITCH_SEARCH_VERTICAL"}
    return f"{_COMPANY_SEARCH_URL}?{urlencode(params)}"


def _extract_company_vanities(page, limit: int) -> list[str]:
    """Unique `/company/<vanity>` slugs from the company search results page, in
    document order (LinkedIn's relevance ranking). Mirrors discovery's
    `_extract_in_urls` for the /in/ people links."""
    seen: set[str] = set()
    vanities: list[str] = []
    for link in page.locator(_COMPANY_LINK_SELECTOR).all():
        href = link.get_attribute("href")
        if not href or "/company/" not in href:
            continue
        vanity = vanity_from_company_url(href)
        # Skip non-entity artefacts (search filter chips, /company/ with no slug).
        if not vanity or vanity in {"search", "results"} or vanity in seen:
            continue
        seen.add(vanity)
        vanities.append(vanity)
        if len(vanities) >= limit:
            break
    return vanities


def resolve_company(
    session,
    keywords: str = "",
    *,
    url: str | None = None,
    limit: int = 5,
    prefer_domain: str | None = None,
) -> list[dict]:
    """Resolve a company → up to `limit` ranked company entities.

    Two modes:
    - `url` given (a pasted LinkedIn company URL) → the authoritative single-entity
      resolution (vanity → company-detail → URN). This is how the user pins the
      exact company when auto-detection is unsure.
    - else **scrape the company search page** for `keywords` (the same HTML-scrape
      discovery uses for people — reliable in the authenticated session), then
      resolve each `/company/<vanity>` to its entity via the company-detail API.
      When `prefer_domain` is given, a website match flags `domain_match=True` (the
      app's silent-auto-pick signal).

    Each hit: {urn, company_id, name, vanity, industry, logo_url, website,
    domain_match}. Zero LLM. Never raises on shape; yields [] and dumps debug HTML
    when nothing is found, so the app falls to the SAFE user-confirm/paste path."""
    if url and url.strip():
        vanity = vanity_from_company_url(url)
        hit = resolve_company_by_vanity(session, vanity) if vanity else None
        return [hit] if hit else []
    if not keywords or not keywords.strip():
        return []

    from .session import goto_page

    session.ensure_browser()
    try:
        goto_page(
            session,
            action=lambda: session.page.goto(_company_search_url(keywords)),
            expected_url_pattern="/search/results/",
            error_message="Failed to reach company search results",
        )
        vanities = _extract_company_vanities(session.page, limit)
    except Exception as e:  # noqa: BLE001 — a scrape miss must not crash the op
        logger.info("company search scrape failed for %r: %s", keywords, e)
        vanities = []
    logger.info("company search: %d vanity candidate(s) for %r", len(vanities), keywords)
    if not vanities:
        _dump_debug(session, f"companysearch-{keywords}", {"keywords": keywords, "vanities": []})
        return []

    api = PlaywrightLinkedinAPI(session=session)
    want = registrable_domain(prefer_domain) if prefer_domain else ""
    hits: list[dict] = []
    for vanity in vanities:
        detail = _fetch_company_detail(api, vanity)
        hit = parse_company_entity(detail or {})
        if hit is None:
            # No usable entity from the detail call — still show the candidate
            # (the scrape found a real /company/<vanity>), just humanized.
            hit = {"urn": "", "company_id": "", "name": _humanize_vanity(vanity),
                   "vanity": vanity, "industry": "", "logo_url": "", "website": "",
                   "domain_match": False}
            if detail is not None:
                _dump_debug(session, f"noentity-{vanity}", detail)
        else:
            if not hit.get("vanity"):
                hit["vanity"] = vanity
            if not hit.get("name"):
                _dump_debug(session, f"noname-{vanity}", detail or {})
                hit["name"] = _humanize_vanity(vanity)
        if want and hit.get("website") and domains_match(hit["website"], want):
            hit["domain_match"] = True
        hits.append(hit)
    return hits
