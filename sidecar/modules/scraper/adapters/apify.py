"""Apify BYO-key adapter family — actor-backed search sources.

Covers the boards a first-party adapter cleanly can't (decision record in
`docs/internal/discovery.md`): Indeed (Cloudflare-walled; the only direct API
needs Indeed's own lifted mobile-app credential), Naukri (reCAPTCHA-walled),
Seek — plus LinkedIn as an optional deeper-JD complement to the guest adapter.
The credential is the **user's own Apify account token** (free tier ≈ $5/mo of
credit, no card) — squarely inside the header/credential policy line
(`http.BROWSER_HEADERS` docstring): we never embed or lift anyone else's key.

Search shape (adapters/base.py): claims `board = "apify"` rows whose `actor`
names a supported actor. `search()` builds actor input from the same
`build_queries()` prefs as every search source, runs the actor synchronously
on the user's account (`run-sync-get-dataset-items` — blocks until the actor
finishes, hence the long per-call timeout), and normalizes dataset rows into
the shared funnel. Same filters, same dedup, same per-source diagnostics.

The token travels in an `Authorization: Bearer` header, NEVER a query param:
fetch errors quote the URL verbatim and land in persisted per-source
diagnostics. Complement, not substitute — first-party adapters stay primary
wherever a clean path exists (free, faster, key-less).

Input/output field shapes live-verified against each actor's published schema
on apify.com, 2026-07-18. An actor changing its schema degrades to "0 rows
parsed" + a per-source error, never a crash.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from ..config import SourceEntry
from ..htmltext import strip_html
from ..http import USER_AGENT, Fetcher
from ..searchquery import SearchQuery, build_queries, select_queries
from ..types import NormalizedJob, ScanPrefs, ScraperError

ID = "apify"
_RUN_BASE = "https://api.apify.com/v2/acts"

# One run-sync call = one actor invocation on the user's credit. Actors that
# take a single (keyword, location) pair are capped at MAX_RUNS invocations
# per scan; actors that accept a batch (LinkedIn's `urls`) get one run.
MAX_RUNS = 3
MAX_ITEMS = 100
# run-sync blocks until the actor finishes; Apify's own ceiling is 300 s.
RUN_TIMEOUT_S = 300


def detect(entry: SourceEntry) -> str:
    if entry.type and entry.type != ID:
        return ""
    if entry.board != ID:
        return ""
    return entry.actor or "unconfigured"


# ---------------------------------------------------------------------------
# Actor specs — input builder + dataset-row normalizer per supported actor.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ActorSpec:
    build_runs: Callable[[list[SearchQuery]], list[dict]]
    parse_item: Callable[[dict], NormalizedJob | None]


def _s(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _locations_list(raw: object) -> str:
    """Join a `locations` array of strings or `{label: …}` dicts."""
    if not isinstance(raw, list):
        return ""
    labels = []
    for item in raw:
        if isinstance(item, dict):
            label = _s(item.get("label"))
        else:
            label = _s(item)
        if label:
            labels.append(label)
    return ", ".join(labels)


# -- memo23/naukri-scraper ---------------------------------------------------


def _naukri_runs(queries: list[SearchQuery]) -> list[dict]:
    runs = []
    for q in select_queries(queries, MAX_RUNS):
        payload: dict = {
            "platform": "naukri",
            "searchQuery": q.keyword,
            "maximumJobs": MAX_ITEMS,
            "includeDescription": True,
            "cleanHtml": True,
        }
        if q.location:
            payload["location"] = q.location
        runs.append(payload)
    return runs


def _naukri_item(item: dict) -> NormalizedJob | None:
    url = _s(item.get("staticUrl"))
    title = _s(item.get("title"))
    if not url or not title:
        return None
    if url.startswith("/"):
        url = f"https://www.naukri.com{url}"
    company_detail = item.get("companyDetail")
    company = _s(company_detail.get("name")) if isinstance(company_detail, dict) else ""
    created = _s(item.get("createdDate"))
    return NormalizedJob(
        title=title,
        canonical_url=url,
        company=company or _s(item.get("staticCompanyName")),
        location=_locations_list(item.get("locations")),
        description=strip_html(_s(item.get("description"))),
        posted_at=created.replace(" ", "T") if created else "",
        source_adapter="naukri",
    )


# -- curious_coder/linkedin-jobs-scraper --------------------------------------


def _linkedin_runs(queries: list[SearchQuery]) -> list[dict]:
    # One run, batched: the actor takes a list of LinkedIn search URLs.
    urls = []
    for q in queries:
        url = f"https://www.linkedin.com/jobs/search/?keywords={quote(q.keyword)}"
        if q.location:
            url += f"&location={quote(q.location)}"
        urls.append(url)
    return [{"urls": urls, "count": MAX_ITEMS, "scrapeCompany": False}]


def _linkedin_item(item: dict) -> NormalizedJob | None:
    title = _s(item.get("title"))
    job_id = _s(item.get("id"))
    link = _s(item.get("link"))
    if not title or not (job_id or link):
        return None
    # Same stable form as the guest adapter → cross-source dedup collapses a
    # job found by both paths into one row.
    url = f"https://www.linkedin.com/jobs/view/{job_id}" if job_id else link
    salary = item.get("salaryInfo")
    return NormalizedJob(
        title=title,
        canonical_url=url,
        company=_s(item.get("companyName")),
        location=_s(item.get("location")),
        description=_s(item.get("descriptionText")),
        posted_at=_s(item.get("postedAt")),
        salary=" – ".join(_s(p) for p in salary if _s(p)) if isinstance(salary, list) else "",
        source_adapter="linkedin",
    )


# -- epicscrapers/seek-job-scraper --------------------------------------------


def _seek_runs(queries: list[SearchQuery]) -> list[dict]:
    runs = []
    for q in select_queries(queries, MAX_RUNS):
        payload: dict = {"keywords": q.keyword, "maxPages": 2}
        if q.location:
            payload["where"] = q.location
        runs.append(payload)
    return runs


def _seek_item(item: dict) -> NormalizedJob | None:
    title = _s(item.get("title"))
    job_id = _s(item.get("id")) or (str(item["id"]) if isinstance(item.get("id"), int) else "")
    if not title or not job_id:
        return None
    posted = _s(item.get("listingDate"))
    return NormalizedJob(
        title=title,
        canonical_url=f"https://www.seek.com.au/job/{job_id}",
        company=_s(item.get("companyName")) or _s(item.get("advertiserName")),
        location=_locations_list(item.get("locations")),
        description=_s(item.get("teaser")),
        posted_at=posted,
        salary=_s(item.get("salaryLabel")),
        source_adapter="seek",
    )


# -- misceres/indeed-scraper ---------------------------------------------------


def _indeed_runs(queries: list[SearchQuery]) -> list[dict]:
    # NOTE: the actor's `country` defaults to US; we pass the user's location
    # string as-is and let the actor resolve it. Users outside the US searching
    # a city name still get city-scoped results in most cases; a country knob
    # can ride the source entry later if the field data says it's needed.
    runs = []
    for q in select_queries(queries, MAX_RUNS):
        payload: dict = {
            "position": q.keyword,
            "maxItemsPerSearch": MAX_ITEMS,
            "saveOnlyUniqueItems": True,
        }
        if q.location:
            payload["location"] = q.location
        runs.append(payload)
    return runs


def _indeed_item(item: dict) -> NormalizedJob | None:
    title = _s(item.get("positionName"))
    url = _s(item.get("url"))
    if not title or not url:
        return None
    posted = _s(item.get("postedAt"))
    return NormalizedJob(
        title=title,
        canonical_url=url,
        company=_s(item.get("company")),
        location=_s(item.get("location")),
        description=_s(item.get("description")),
        # `postedAt` is relative text ("Today", "3 days ago") — not ISO; leave
        # empty so quality flags it rather than storing junk dates.
        posted_at=posted if posted[:4].isdigit() else "",
        salary=_s(item.get("salary")),
        source_adapter="indeed",
    )


ACTORS: dict[str, _ActorSpec] = {
    "memo23/naukri-scraper": _ActorSpec(_naukri_runs, _naukri_item),
    "curious_coder/linkedin-jobs-scraper": _ActorSpec(_linkedin_runs, _linkedin_item),
    "epicscrapers/seek-job-scraper": _ActorSpec(_seek_runs, _seek_item),
    "misceres/indeed-scraper": _ActorSpec(_indeed_runs, _indeed_item),
}

# The real board each actor scrapes. Rows are stamped with THIS as their
# `source_adapter` (maintainer directive 2026-07-18: the user sees "Naukri",
# never the "Apify" plumbing) — the board pill, deep search, and the Analytics
# Discovery tab all key off it. `apify` remains only the *adapter/toggle*
# identity (`board = "apify"` entries, `apify:<actor>` source keys). The
# LinkedIn actor deliberately stamps "linkedin" so its rows share one identity
# with the guest adapter + logged-in one-shot (they already share canonical
# URLs and dedup).
ACTOR_SOURCE_IDS: dict[str, str] = {
    "memo23/naukri-scraper": "naukri",
    "curious_coder/linkedin-jobs-scraper": "linkedin",
    "epicscrapers/seek-job-scraper": "seek",
    "misceres/indeed-scraper": "indeed",
}

# The entries seeded into portals_config when the user saves an Apify key —
# ordered by product value (decision record: Naukri + Indeed are the coverage
# first-party scraping can't reach; Seek next; LinkedIn last as a complement
# to the free guest adapter, hence seeded but most likely to be toggled off).
DEFAULT_ACTORS: tuple[str, ...] = (
    "memo23/naukri-scraper",
    "misceres/indeed-scraper",
    "epicscrapers/seek-job-scraper",
    "curious_coder/linkedin-jobs-scraper",
)


def search(entry: SourceEntry, prefs: ScanPrefs, fetcher: Fetcher) -> list[NormalizedJob]:
    token = prefs.credentials.get("apify", "")
    if not token:
        raise ScraperError(
            ID,
            "no Apify API key — add yours in Settings → Discovery sources "
            "(a free Apify account works), or untick this source",
        )
    spec = ACTORS.get(entry.actor)
    if spec is None:
        raise ScraperError(
            ID,
            f"unsupported Apify actor {entry.actor!r} — supported: "
            f"{', '.join(sorted(ACTORS))}",
        )
    queries = build_queries(prefs)
    if not queries:
        raise ScraperError(
            ID,
            "Apify search needs at least one role alias — set roles in "
            "onboarding/preferences (a keyword-less run would spend credit on noise)",
        )

    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"}
    jobs: list[NormalizedJob] = []
    errors: list[str] = []
    for payload in spec.build_runs(queries):
        url = f"{_RUN_BASE}/{entry.actor.replace('/', '~')}/run-sync-get-dataset-items?format=json"
        try:
            items = fetcher.post_json(url, payload, headers=headers, timeout_s=RUN_TIMEOUT_S)
        except ScraperError as e:
            # One failed run (quota, actor error, timeout) never kills the
            # others; the message lands verbatim in per-source diagnostics.
            errors.append(str(e))
            continue
        if not isinstance(items, list):
            errors.append(f"{entry.actor}: run returned no dataset (got {type(items).__name__})")
            continue
        parsed_any = False
        for item in items:
            if not isinstance(item, dict):
                continue
            job = spec.parse_item(item)
            if job is not None:
                jobs.append(job)
                parsed_any = True
        if items and not parsed_any:
            errors.append(
                f"{entry.actor}: {len(items)} dataset row(s), 0 parsed — actor schema changed?"
            )
    if not jobs and errors:
        raise ScraperError(ID, "; ".join(errors))
    return jobs
