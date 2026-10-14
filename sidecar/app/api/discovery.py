"""Discovery-source controls (Settings › Discover jobs).

Every source family is on by default; the user opts out per family or per entry
(an adapter's sub-sources). The opt-out list is `portals_config["disabled_sources"]`,
the document `scan()` reads, so a toggle applies on the next scan. Routes: GET/POST
/api/discovery/sources (the catalog, and flipping entries), and GET/POST/DELETE
/api/discovery/credentials (the users' own scraper keys, sealed with the app Fernet
key into `scraper:<id>` rows, NFR-SEC-01). Saving a key seeds its adapter's default
sources. No vendor is named here: each adapter declares its part (F-P6)."""

from __future__ import annotations

import asyncio
import re
from collections import Counter, OrderedDict
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sidecar.modules.scraper import adapters
from sidecar.modules.scraper.config import SourceEntry
from sidecar.modules.scraper.http import Fetcher
from sidecar.modules.scraper.types import ScraperError

from ..db.base import now_utc
from ..registry.persistence import SCRAPER_ENGINE_PREFIX
from ..security import get_app_key, mask_key, seal_secret
from . import dto
from .deps import data_dir as _data_dir
from .deps import db as _db

router = APIRouter()

# The users' own scraper keys, from the adapters that declare one.
_KEYED = {a.ID: a for a in adapters.ADAPTERS if hasattr(a, "CREDENTIAL")}
CREDENTIALS: dict[str, str] = {cid: a.CREDENTIAL for cid, a in _KEYED.items()}

# Adapters whose every entry is its own toggle (keyed by the entry's
# `SUB_SOURCE_FIELD`), counted in Analytics under the board it stamps.
_SUB_SOURCED = {a.ID: a for a in adapters.ADAPTERS if hasattr(a, "SUB_SOURCE_FIELD")}


def _portals(repos) -> dict:  # noqa: ANN001 — Repos
    """A fresh shallow copy of the stored `portals_config`, the 1 place that reads it
    (D-A1). Never mutate the stored document: its nested JSON is shared with the ORM
    row, so an in-place edit flushes nothing. Replace a `sources` row, never edit it."""
    return dict(repos.preferences.get_or_create().portals_config or {})


def _portals_for_update(repos) -> tuple[dict, Callable[[], None]]:  # noqa: ANN001 — Repos
    """`_portals` plus the write-back that persists whatever the caller left in
    the returned copy. Saving stays the caller's explicit call, so a mutation
    that turns out to be a no-op writes nothing."""
    portals = _portals(repos)

    def save() -> None:
        repos.preferences.update(portals_config=portals)

    return portals, save


def _entry_counts(portals_config: dict) -> Counter[str]:
    """How many of the user's [[sources]] rows each adapter family claims."""
    counts: Counter[str] = Counter()
    for raw in portals_config.get("sources", []):
        if not isinstance(raw, dict):
            continue
        entry = SourceEntry(
            url=str(raw.get("url", "")),
            board=str(raw.get("board", "")),
            type=str(raw.get("type", "")),
            company=str(raw.get("company", "")),
        )
        resolved = adapters.resolve(entry)
        if resolved is not None:
            counts[resolved[0].ID] += 1
    return counts


def _catalog(portals_config: dict) -> list[dto.DiscoverySourceDTO]:
    disabled = set(portals_config.get("disabled_sources", []))
    counts = _entry_counts(portals_config)
    rows = [
        dto.DiscoverySourceDTO(
            id=adapter_id,
            label=label,
            kind=kind,
            entries=counts.get(adapter_id, 0),
            enabled=adapter_id not in disabled,
        )
        for adapter_id, (label, kind) in adapters.CATALOG.items()
    ]
    # A row per sub-source entry: each toggles just that entry (full source
    # key), on top of the family master toggle.
    for raw in portals_config.get("sources", []):
        if not isinstance(raw, dict):
            continue
        adapter = _SUB_SOURCED.get(str(raw.get("board", "")))
        sub = str(raw.get(adapter.SUB_SOURCE_FIELD, "")) if adapter else ""
        if adapter is None or not sub:
            continue
        key = f"{adapter.ID}:{sub}"
        rows.append(
            dto.DiscoverySourceDTO(
                id=key,
                label=adapter.SUB_SOURCE_LABELS.get(sub, sub),
                kind=adapter.KIND,
                entries=1,
                enabled=adapter.ID not in disabled and key not in disabled,
            )
        )
    return rows


@router.get("/api/discovery/sources")
def list_discovery_sources(request: Request) -> list[dto.DiscoverySourceDTO]:
    with _db(request).repos() as repos:
        portals = _portals(repos)
    return _catalog(portals)


@router.post("/api/discovery/sources")
def toggle_discovery_source(
    request: Request, payload: dto.DiscoverySourceToggle
) -> list[dto.DiscoverySourceDTO]:
    ids = payload.ids if payload.ids is not None else ([payload.id] if payload.id else [])
    if not ids:
        raise HTTPException(status_code=422, detail="id or ids required")
    # A family id must exist in the shipped catalog; a full source key
    # ("greenhouse:acme", "apify:<actor>") is accepted as-is — its family
    # prefix is validated instead, so a typo can't silently disable nothing.
    # Validate ALL ids before flipping any (a section toggle is atomic).
    for source_id in ids:
        family = source_id.split(":", 1)[0]
        if family not in adapters.CATALOG:
            raise HTTPException(
                status_code=404, detail=f"unknown discovery source {source_id!r}"
            )
    with _db(request).repos() as repos:
        portals, save = _portals_for_update(repos)
        disabled = set(portals.get("disabled_sources", []))
        for source_id in ids:
            if payload.enabled:
                disabled.discard(source_id)
            else:
                disabled.add(source_id)
        portals["disabled_sources"] = sorted(disabled)
        save()
    return _catalog(portals)


# -- watchlist: watch a company's board (approved-plan #4) -------------------

# Board-root derivation for the hosted ATSes: a job URL's first path segment
# is the tenant, and the tenant root is a valid enumerate source. Workday
# keeps host + site. Anything else needs the user to paste the careers URL.
_TENANT_ROOT_HOSTS = (
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
    "jobs.lever.co",
    "jobs.ashbyhq.com",
    "apply.workable.com",
    "jobs.smartrecruiters.com",
)


def _board_root(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in _TENANT_ROOT_HOSTS and segments:
        return f"https://{host}/{segments[0]}"
    if ".myworkdayjobs.com" in host and segments:
        # {host}/{locale?}/{site}/... → keep up to the site segment.
        site = segments[1] if len(segments) > 1 and len(segments[0]) == 5 else segments[0]
        return f"https://{host}/{site}"
    return url


# Query parameters that are a search's filters or a tracking tag. A followed
# board must carry none: the scan applies the user's live preferences
# (watch-company.md section 3, rule 3). Anything else may be what makes the URL
# a feed, so it stays.
_FILTER_PARAMS = frozenset(
    {"location", "locations", "loc", "q", "query", "keyword", "keywords", "search",
     "department", "departments", "team", "teams", "category", "categories", "office",
     "offices", "country", "city", "remote", "type", "level", "gh_src", "lever-source",
     "source", "ref"}
)


def _strip_filters(url: str) -> str:
    """The URL without search filters and tracking tags."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(url)
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _FILTER_PARAMS and not k.lower().startswith("utm_")
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def _read_board(adapter: Any, source_url: str, company: str) -> tuple[int | None, list[str], str]:
    """Read a board once, as a scan reads that source: (openings, 3 titles, why it
    failed). Synchronous network I/O, so callers run it in a worker thread."""
    try:
        jobs = adapter.fetch(
            SourceEntry(url=source_url, company=company), Fetcher(timeout_s=_WATCH_READ_TIMEOUT_S)
        )
    except Exception as exc:  # noqa: BLE001 — any failure is reported, never fatal
        return None, [], str(exc) or type(exc).__name__
    return len(jobs), [j.title for j in jobs[:3] if j.title], ""


def _slugish(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _find_source_by_company(sources: list, company: str, source_adapter: str) -> dict | None:
    """The `[[sources]]` row already covering `company`, matched by name or tenant
    slug among rows the job's adapter family claims. Some ATSes post on the
    company's own domain, so the URL can't be walked back to a board, but the board
    that found the job is already a source (the Coupang and Roku watch bug)."""
    want = _slugish(company)
    if not want:
        return None
    for raw in sources:
        if not (isinstance(raw, dict) and raw.get("url")):
            continue
        resolved = adapters.resolve(SourceEntry(url=str(raw["url"])))
        if resolved is None or resolved[0].ID != source_adapter:
            continue
        row_company = _slugish(str(raw.get("company", "")))
        row_slug = _slugish(str(raw["url"]).rstrip("/").rsplit("/", 1)[-1])
        if want in (row_company, row_slug) or (row_company and want == row_company):
            return raw
    return None


# Board-URL templates, the last-resort watch fallback: guess the tenant slug
# from the company name and probe each candidate. Keeps a company-domain job
# watchable after its covering source was unwatched (watch, unwatch, rewatch
# once dead-ended in a 422).
_BOARD_URL_TEMPLATES: dict[str, tuple[str, ...]] = {
    a.ID: a.BOARD_URLS for a in adapters.ADAPTERS if hasattr(a, "BOARD_URLS")
}


def _guess_board_urls(job_adapter: str, company: str) -> list[str]:
    templates = _BOARD_URL_TEMPLATES.get(job_adapter, ())
    slugs: list[str] = []
    for candidate in (_slugish(company), _slugish(company.split()[0] if company else "")):
        if candidate and candidate not in slugs:
            slugs.append(candidate)
    return [t.format(s) for s in slugs for t in templates]


def _ledger_watch(repos, snapshot: dict, error: str | None) -> None:
    """Record the watch attempt in the operations ledger — Analytics → Logs
    shows the row (kind `watch_company`) with the verbatim failure, so a
    refused watch is diagnosable after the tooltip is gone (maintainer
    2026-07-22)."""
    op = repos.operations.create("watch_company", snapshot)
    op.state = "failed" if error else "succeeded"
    op.error = error
    op.started_at = op.finished_at = now_utc()


_WATCH_PROBE_TIMEOUT_S = 8
_WATCH_READ_TIMEOUT_S = 20

# Confirmed slug-guess probes, keyed "<adapter>:<company-slug>" → board URL.
# A rewatch after an unwatch re-ran the live probe every time (1-2 s of real
# toggle latency — maintainer 2026-07-22); a board that answered once this
# process is not re-probed. Bounded LRU: a long-lived process watching and
# unwatching many companies grew it without limit (S-C10).
_GUESS_CACHE_MAX = 512
_GUESS_CACHE: OrderedDict[str, str] = OrderedDict()


def _guess_cache_put(key: str, url: str) -> None:
    _GUESS_CACHE[key] = url
    _GUESS_CACHE.move_to_end(key)
    while len(_GUESS_CACHE) > _GUESS_CACHE_MAX:
        _GUESS_CACHE.popitem(last=False)


def _guess_cache_get(key: str) -> str | None:
    url = _GUESS_CACHE.get(key)
    if url is not None:
        _GUESS_CACHE.move_to_end(key)
    return url


@router.post("/api/discovery/watchlist")
async def watch_company(
    request: Request, payload: dto.WatchCompanyRequest
) -> dto.WatchCompanyResult:
    """Add a company board to `portals_config` so every future scan covers it.
    The watchlist IS the sources list — no second store, no special casing."""
    with _db(request).repos() as repos:
        url = (payload.url or "").strip()
        company = payload.company.strip()
        job_adapter = ""
        if not url and payload.job_id:
            job = repos.jobs.get(payload.job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job {payload.job_id!r} not found")
            url = job.canonical_url
            company = company or job.company
            job_adapter = job.source_adapter
        if not url:
            raise HTTPException(status_code=422, detail="url or job_id required")
        snapshot = {"url": url, "company": company, "job_id": payload.job_id or ""}
        sources = list(_portals(repos).get("sources", []))

    # Network probes run outside any DB session and off the event loop, like
    # `probe_url` in api/jobs.py. A synchronous probe here once blocked /healthz and
    # got a healthy sidecar restarted mid-session.
    source_url = _board_root(url)
    if source_url == url:
        source_url = _strip_filters(url)
    entry = SourceEntry(url=source_url, company=company)
    resolved = adapters.resolve(entry)
    if resolved is None and job_adapter:
        # A company-domain posting URL with no board root: the board that found the job
        # is already a source, so watch that. _GUESS_CACHE remembers each resolution, so a
        # rewatch after unwatch is a lookup, not a live probe.
        cache_key = f"{job_adapter}:{_slugish(company)}"
        covering = _find_source_by_company(sources, company, job_adapter)
        if covering is not None:
            source_url = str(covering["url"])
            resolved = adapters.resolve(SourceEntry(url=source_url))
            if resolved is not None:
                _guess_cache_put(cache_key, source_url)
        else:
            cached = _guess_cache_get(cache_key)
            if cached is not None:
                cand_resolved = adapters.resolve(SourceEntry(url=cached))
                if cand_resolved is not None:
                    source_url, resolved = cached, cand_resolved
            if resolved is None:
                # Nothing remembered: guess the tenant board from the company
                # name and keep the first candidate that actually opens.
                for cand in _guess_board_urls(job_adapter, company):
                    try:
                        await asyncio.to_thread(
                            Fetcher(timeout_s=_WATCH_PROBE_TIMEOUT_S).get_text, cand
                        )
                    except ScraperError:
                        continue
                    cand_resolved = adapters.resolve(SourceEntry(url=cand))
                    if cand_resolved is not None:
                        source_url, resolved = cand, cand_resolved
                        _guess_cache_put(cache_key, cand)
                        break
    fail: dict[str, Any] | None = None
    adapter_id = ""
    if resolved is None:
        # A code and its facts, so the screen writes the sentence in the user's
        # language; `message` is the English the ledger keeps (audit A8).
        vendors = [a.LABEL for a in adapters.ADAPTERS if a.KIND == "ats"]
        if _source_kind(job_adapter) == "search":
            fail = {
                "code": "search_source",
                "source": job_adapter,
                "vendors": vendors,
                "message": (
                    f"this job came from a search source ({job_adapter}), so there is "
                    "no company board to follow; paste the company's careers page instead"
                ),
            }
        else:
            # A followed row no adapter can read would scan nothing forever, so
            # this refuses (watch-company.md section 3, row 5).
            fail = {
                "code": "unrecognised_board",
                "vendors": vendors,
                "message": (
                    "no adapter recognizes this URL as a scannable board; paste a careers "
                    f"page on {', '.join(vendors)}, or an RSS feed"
                ),
            }
    openings: int | None = None
    titles: list[str] = []
    read_error = ""
    if resolved is not None:
        adapter_id = resolved[0].ID
        # Read the board once, the way a scan will: it proves the board exists,
        # the app can read it, and its openings are this company's. A board
        # that reads badly is followed anyway, with the reason and the link
        # shown (watch-company.md section 3).
        openings, titles, read_error = await asyncio.to_thread(
            _read_board, resolved[0], source_url, company
        )
    if fail is not None:
        # Recorded OUTSIDE the request session: raising through `repos()`
        # rolls its transaction back, which silently discarded the ledger row
        # (found 2026-07-22).
        with _db(request).repos() as ledger_repos:
            _ledger_watch(ledger_repos, snapshot, fail["message"])
        raise HTTPException(status_code=422, detail=fail)
    with _db(request).repos() as repos:
        portals, save = _portals_for_update(repos)
        sources = list(portals.get("sources", []))
        already = False
        for i, raw in enumerate(sources):
            if (
                isinstance(raw, dict)
                and str(raw.get("url", "")).rstrip("/") == source_url.rstrip("/")
            ):
                already = True
                # A registry-seeded row the user explicitly watches becomes a
                # managed roster entry (watched=True) — so the watch toggle
                # reflects it and unwatch can remove it. REPLACE the row dict,
                # never mutate it in place (see `_portals`).
                stamped = {
                    **raw, "watched": True, "openings": openings, "read_error": read_error
                }
                if company and not raw.get("company"):
                    stamped["company"] = company
                sources[i] = stamped
                portals["sources"] = sources
                save()
                break
        if not already:
            # `watched` marks the row as user-tracked so the roster view can
            # tell it apart from the seeded registry. Unknown keys are ignored
            # by the portals parser, so scans are unaffected.
            row: dict = {
                "url": source_url, "watched": True, "openings": openings,
                "read_error": read_error,
            }
            if company:
                row["company"] = company
            sources.append(row)
            portals["sources"] = sources
            save()
        _ledger_watch(repos, snapshot, None)
    return dto.WatchCompanyResult(
        added=not already,
        source_url=source_url,
        adapter=adapter_id,
        company=company,
        openings=openings,
        sample_titles=titles,
        read_error=read_error,
    )


@router.get("/api/geo/suggest")
def suggest_places(q: str = "", limit: int = 8) -> list[dto.PlaceSuggestionDTO]:
    """The location picker's suggestions (place-picker.md section 3). A plain
    `def`, so the first call's gazetteer load runs in the threadpool, off the
    event loop."""
    from sidecar.modules.geo.suggest import suggest

    return [
        dto.PlaceSuggestionDTO(label=s.label, name=s.name, level=s.level, id=s.id)
        for s in suggest(q, limit=max(1, min(limit, 20)))
    ]


@router.get("/api/geo/countries")
def list_countries() -> list[dto.CountryDTO]:
    """Every country in the shipped gazetteer, by name, for Applicant details' work
    authorizations. A plain `def`, so the first load runs off the event loop."""
    from sidecar.modules.geo.gazetteer import gazetteer

    names = gazetteer().country_name
    return [
        dto.CountryDTO(code=code, name=name)
        for code, name in sorted(names.items(), key=lambda kv: kv[1])
    ]


@router.get("/api/discovery/watchlist")
def list_watched_companies(request: Request) -> dto.WatchlistDTO:
    """The tracked-companies roster: user-added (`watched`) board rows from
    `portals_config.sources`. Rows added before the marker existed don't
    appear — they keep scanning; re-watching stamps them."""
    with _db(request).repos() as repos:
        sources = _portals(repos).get("sources", [])
    entries = []
    for raw in sources:
        if not (isinstance(raw, dict) and raw.get("watched") and raw.get("url")):
            continue
        resolved = adapters.resolve(SourceEntry(url=str(raw["url"])))
        entries.append(
            dto.WatchlistEntryDTO(
                url=str(raw["url"]),
                company=str(raw.get("company", "")),
                adapter=resolved[0].ID if resolved else "",
                openings=raw.get("openings") if isinstance(raw.get("openings"), int) else None,
                read_error=str(raw.get("read_error") or ""),
            )
        )
    return dto.WatchlistDTO(entries=entries)


@router.delete("/api/discovery/watchlist")
def unwatch_company(request: Request, url: str) -> dto.WatchRemoveResult:
    """Remove a tracked company board (by its source URL). Only `watched`
    rows are removable here — the seeded registry isn't editable from the
    roster; source families are toggled in Settings → Discovery sources."""
    target = url.rstrip("/")
    with _db(request).repos() as repos:
        portals, save = _portals_for_update(repos)
        sources = list(portals.get("sources", []))
        kept = [
            raw
            for raw in sources
            if not (
                isinstance(raw, dict)
                and raw.get("watched")
                and str(raw.get("url", "")).rstrip("/") == target
            )
        ]
        removed = len(kept) != len(sources)
        if removed:
            portals["sources"] = kept
            save()
    return dto.WatchRemoveResult(removed=removed)


# -- per-source efficacy analytics (Analytics → Discovery tab) ---------------

_RECENT_SCANS = 30

# Display identities for source ids that aren't adapter families in CATALOG:
# the real boards a sub-source stamps as `source_adapter` (maintainer directive
# 2026-07-18: show "Naukri", never the plumbing), and 1 LinkedIn identity on
# every path, since the paths share canonical URLs and dedup.
_ANALYTICS_LABELS: dict[str, tuple[str, str]] = {
    **{
        board: (a.SUB_SOURCE_LABELS.get(sub, board), a.KIND)
        for a in _SUB_SOURCED.values()
        for sub, board in a.SUB_SOURCE_IDS.items()
    },
    "linkedin": ("LinkedIn", "search"),
    "paste-url": ("Added by URL", "other"),
}


def _source_kind(source_adapter: str) -> str:
    """The kind of the source a job row was stamped with (search, ats, ...)."""
    return _ANALYTICS_LABELS.get(source_adapter, adapters.CATALOG.get(source_adapter, ("", "")))[1]


def _analytics_bucket_id(source_key: str) -> str:
    """Map a per-scan source key to its user-facing analytics identity: a
    sub-source key (`apify:<actor>`) buckets as the board it stamps; every
    other key buckets by its family prefix."""
    family, _, rest = source_key.partition(":")
    adapter = _SUB_SOURCED.get(family)
    if adapter is not None and rest:
        return adapter.SUB_SOURCE_IDS.get(rest, family)
    return family


@router.get("/api/discovery/analytics")
async def discovery_analytics(request: Request) -> dto.DiscoveryAnalyticsDTO:
    """Per-source analytics from existing records: jobs by `source_adapter`, scores,
    applications, and the last `_RECENT_SCANS` scans' per-source numbers. `scored`
    and `avg_score` skip unscorable jobs, as the board does. Off the event loop
    (S-C7): the fold walks up to 10,000 jobs."""

    def _assemble() -> dto.DiscoveryAnalyticsDTO:
        from sidecar.modules.scorer.deterministic import MIN_JD_CHARS

        with _db(request).repos() as repos:
            jobs = repos.jobs.list_by_states(["active", "expired", "removed"])
            saved_ids = repos.applications.job_ids()
            scans = repos.operations.list_by_kind_states("scan", {"succeeded"})

            per: dict[str, dict] = {}

            def _bucket(family: str) -> dict:
                return per.setdefault(
                    family,
                    {
                        "jobs": 0, "saved": 0, "scored": 0, "score_sum": 0.0,
                        "fetched": 0, "kept": 0, "http_calls": 0,
                        "latency_ms": 0, "errors": 0,
                    },
                )

            for job in jobs:
                b = _bucket(job.source_adapter or "unknown")
                # Rows found and rows saved count regardless of scorability —
                # the source really did return them, and the user really did
                # save one. Only the score stats below need the exclusion.
                b["jobs"] += 1
                if job.id in saved_ids:
                    b["saved"] += 1
                # A job with no description is `unscorable` (same MIN_JD_CHARS predicate as
                # `dto.job_dto`): its keyword 0 marks missing data, so counting it would drag a
                # source's average down for having no description.
                if len(job.description or "") < MIN_JD_CHARS:
                    continue
                # The rating the board shows.
                score = job.score
                if score is not None:
                    b["scored"] += 1
                    b["score_sum"] += float(score)

            scans = sorted(
                scans, key=lambda o: o.started_at or o.created_at, reverse=True
            )
            recent = scans[:_RECENT_SCANS]
            last_scan_at = None
            for op in recent:
                if last_scan_at is None:
                    last_scan_at = op.finished_at
                per_source = ((op.result_ref or {}).get("per_source")) or {}
                if not isinstance(per_source, dict):
                    continue
                for key, r in per_source.items():
                    if not isinstance(r, dict):
                        continue
                    b = _bucket(_analytics_bucket_id(str(key)))
                    b["fetched"] += int(r.get("fetched") or 0)
                    b["kept"] += int(r.get("kept") or 0)
                    b["http_calls"] += int(r.get("http_calls") or 0)
                    b["latency_ms"] += int(r.get("latency_ms") or 0)
                    b["errors"] += len(r.get("errors") or [])

        rows = []
        for family, b in per.items():
            label, kind = _ANALYTICS_LABELS.get(
                family, adapters.CATALOG.get(family, (family, "other"))
            )
            rows.append(
                dto.DiscoverySourceStatsDTO(
                    id=family,
                    label=label,
                    kind=kind,
                    jobs=b["jobs"],
                    saved=b["saved"],
                    scored=b["scored"],
                    avg_score=(b["score_sum"] / b["scored"]) if b["scored"] else None,
                    fetched=b["fetched"],
                    kept=b["kept"],
                    http_calls=b["http_calls"],
                    latency_ms=b["latency_ms"],
                    errors=b["errors"],
                )
            )
        rows.sort(key=lambda r: (-r.jobs, r.id))
        return dto.DiscoveryAnalyticsDTO(
            sources=rows, scans=len(recent), last_scan_at=last_scan_at
        )

    return await asyncio.to_thread(_assemble)


# -- the users' own scraper keys ---------------------------------------------


def _credentials(request: Request) -> list[dto.DiscoveryCredentialDTO]:
    with _db(request).repos() as repos:
        out = []
        for cid, label in CREDENTIALS.items():
            row = repos.engine_settings.get_by_engine(f"{SCRAPER_ENGINE_PREFIX}{cid}")
            has_key = bool(row is not None and row.key_encrypted)
            out.append(
                dto.DiscoveryCredentialDTO(
                    id=cid,
                    label=label,
                    has_key=has_key,
                    key_hint=row.key_ref if row is not None else None,
                )
            )
        return out


def _seed_sources(repos, adapter_id: str) -> None:  # noqa: ANN001 — Repos
    """First key save: add the adapter's `DEFAULT_SOURCES` the user doesn't
    have yet (an entry carrying every default field counts), so they exist and
    toggle without touching existing rows."""
    portals, save = _portals_for_update(repos)
    sources = list(portals.get("sources", []))
    missing = [
        default
        for default in _KEYED[adapter_id].DEFAULT_SOURCES
        if not any(
            isinstance(raw, dict) and all(str(raw.get(k, "")) == v for k, v in default.items())
            for raw in sources
        )
    ]
    if missing:
        portals["sources"] = sources + [dict(d) for d in missing]
        save()


@router.get("/api/discovery/credentials")
def list_discovery_credentials(request: Request) -> list[dto.DiscoveryCredentialDTO]:
    return _credentials(request)


@router.post("/api/discovery/credentials")
def save_discovery_credential(
    request: Request, payload: dto.DiscoveryCredentialSave
) -> list[dto.DiscoveryCredentialDTO]:
    if payload.id not in CREDENTIALS:
        raise HTTPException(status_code=404, detail=f"unknown credential {payload.id!r}")
    key = payload.key.strip()
    if not key:
        raise HTTPException(status_code=422, detail="key must not be empty")
    sealed = seal_secret(key, get_app_key(_data_dir(request)))
    engine_id = f"{SCRAPER_ENGINE_PREFIX}{payload.id}"
    with _db(request).repos() as repos:
        row = repos.engine_settings.get_by_engine(engine_id)
        fields = {"key_encrypted": sealed, "key_ref": mask_key(key), "enabled": True}
        if row is None:
            repos.engine_settings.create(engine_id, **fields)
        else:
            repos.engine_settings.update(row.id, **fields)
        _seed_sources(repos, payload.id)
    return _credentials(request)


@router.delete("/api/discovery/credentials/{credential_id}")
def delete_discovery_credential(
    request: Request, credential_id: str
) -> list[dto.DiscoveryCredentialDTO]:
    if credential_id not in CREDENTIALS:
        raise HTTPException(status_code=404, detail=f"unknown credential {credential_id!r}")
    with _db(request).repos() as repos:
        removed = repos.engine_settings.delete_by_engine(
            f"{SCRAPER_ENGINE_PREFIX}{credential_id}"
        )
    if not removed:
        raise HTTPException(status_code=404, detail=f"no stored {credential_id!r} key")
    # The seeded [[sources]] entries stay — the next scan reports a clear
    # missing-key error per source, and the user can untick them.
    return _credentials(request)
