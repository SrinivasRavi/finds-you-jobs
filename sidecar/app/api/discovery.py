"""Discovery-source controls (Settings → Discovery sources).

A dedicated router (engines.py precedent — kept out of the contested
`routes.py`). Every adapter family the scraper ships is ON by default; the
user opts *out* per family (or per entry, for actor-granular sources like
Apify). The opt-out list lives in `UserPreferences.portals_config
["disabled_sources"]` — the same JSON document `scan()` already reads, so a
toggle takes effect on the next scan with no schema change and no restart.

- `GET /api/discovery/sources` — the full catalog: id, label, kind, how many
  of the user's `[[sources]]` entries resolve to it, enabled state.
- `POST /api/discovery/sources` — flip one family/entry on or off; returns
  the updated catalog.
- `GET/POST/DELETE /api/discovery/credentials` — the BYO scraper keys
  (Apify / Brave). Sealed with the app Fernet key into `scraper:<id>` rows in
  `engine_settings` (same NFR-SEC-01 discipline as BYOK LLM keys; invisible
  to the engine registry and the Settings engines list). Saving the Apify key
  also seeds the default actor `[[sources]]` entries so the sources appear in
  the catalog, toggleable per actor.
"""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, HTTPException, Request

from sidecar.modules.scraper import adapters
from sidecar.modules.scraper.adapters import apify
from sidecar.modules.scraper.config import SourceEntry

from ..db import Database
from ..registry.persistence import SCRAPER_ENGINE_PREFIX
from ..security import get_app_key, mask_key, seal_secret
from . import dto

router = APIRouter()

# The BYO scraper keys the product knows how to use. Brave rides the same
# store; its adapter lands with the meta-search commit.
CREDENTIALS: dict[str, str] = {"apify": "Apify", "brave": "Brave Search"}

# Friendly names for the seeded Apify actor sources (catalog rows).
ACTOR_LABELS: dict[str, str] = {
    "memo23/naukri-scraper": "Naukri (via Apify)",
    "misceres/indeed-scraper": "Indeed (via Apify)",
    "epicscrapers/seek-job-scraper": "Seek (via Apify)",
    "curious_coder/linkedin-jobs-scraper": "LinkedIn deep-JD (via Apify)",
}


def _db(request: Request) -> Database:
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="storage not initialized")
    return db


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
    # Actor-granular rows for the user's Apify entries: each one toggles just
    # that actor (full source key), on top of the family master toggle.
    apify_off = "apify" in disabled
    for raw in portals_config.get("sources", []):
        if isinstance(raw, dict) and str(raw.get("board", "")) == "apify":
            actor = str(raw.get("actor", ""))
            if not actor:
                continue
            key = f"apify:{actor}"
            rows.append(
                dto.DiscoverySourceDTO(
                    id=key,
                    label=ACTOR_LABELS.get(actor, actor),
                    kind="search",
                    entries=1,
                    enabled=not apify_off and key not in disabled,
                )
            )
    return rows


@router.get("/api/discovery/sources")
async def list_discovery_sources(request: Request) -> list[dto.DiscoverySourceDTO]:
    with _db(request).repos() as repos:
        prefs = repos.preferences.get_or_create()
        portals = dict(prefs.portals_config or {})
    return _catalog(portals)


@router.post("/api/discovery/sources")
async def toggle_discovery_source(
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
        prefs = repos.preferences.get_or_create()
        portals = dict(prefs.portals_config or {})
        disabled = set(portals.get("disabled_sources", []))
        for source_id in ids:
            if payload.enabled:
                disabled.discard(source_id)
            else:
                disabled.add(source_id)
        portals["disabled_sources"] = sorted(disabled)
        repos.preferences.update(portals_config=portals)
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


@router.post("/api/discovery/watchlist")
async def watch_company(
    request: Request, payload: dto.WatchCompanyRequest
) -> dto.WatchCompanyResult:
    """Add a company board to `portals_config` so every future scan covers it.
    The watchlist IS the sources list — no second store, no special casing."""
    with _db(request).repos() as repos:
        url = (payload.url or "").strip()
        company = payload.company.strip()
        if not url and payload.job_id:
            job = repos.jobs.get(payload.job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"job {payload.job_id!r} not found")
            url = job.canonical_url
            company = company or job.company
        if not url:
            raise HTTPException(status_code=422, detail="url or job_id required")
        source_url = _board_root(url)
        entry = SourceEntry(url=source_url, company=company)
        resolved = adapters.resolve(entry)
        if resolved is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "no adapter recognizes this URL as a scannable board — paste the "
                    "company's careers page on a supported ATS (Greenhouse, Lever, "
                    "Ashby, Workable, SmartRecruiters, Recruitee, Teamtailor, "
                    "Personio, Workday, BambooHR, Breezy) or an RSS feed"
                ),
            )
        adapter_id = resolved[0].ID
        prefs = repos.preferences.get_or_create()
        portals = dict(prefs.portals_config or {})
        sources = list(portals.get("sources", []))
        already = False
        for raw in sources:
            if (
                isinstance(raw, dict)
                and str(raw.get("url", "")).rstrip("/") == source_url.rstrip("/")
            ):
                already = True
                # A registry-seeded row the user explicitly watches becomes a
                # managed roster entry (watched=True) — so the watch toggle
                # reflects it and unwatch can remove it.
                if not raw.get("watched"):
                    raw["watched"] = True
                    if company and not raw.get("company"):
                        raw["company"] = company
                    portals["sources"] = sources
                    repos.preferences.update(portals_config=portals)
                break
        if not already:
            # `watched` marks the row as user-tracked so the roster view can
            # tell it apart from the seeded registry. Unknown keys are ignored
            # by the portals parser, so scans are unaffected.
            row: dict = {"url": source_url, "watched": True}
            if company:
                row["company"] = company
            sources.append(row)
            portals["sources"] = sources
            repos.preferences.update(portals_config=portals)
    return dto.WatchCompanyResult(
        added=not already, source_url=source_url, adapter=adapter_id, company=company
    )


@router.get("/api/discovery/watchlist")
async def list_watched_companies(request: Request) -> dto.WatchlistDTO:
    """The tracked-companies roster: user-added (`watched`) board rows from
    `portals_config.sources`. Rows added before the marker existed don't
    appear — they keep scanning; re-watching stamps them."""
    with _db(request).repos() as repos:
        prefs = repos.preferences.get_or_create()
        sources = (prefs.portals_config or {}).get("sources", [])
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
            )
        )
    return dto.WatchlistDTO(entries=entries)


@router.delete("/api/discovery/watchlist")
async def unwatch_company(request: Request, url: str) -> dto.WatchRemoveResult:
    """Remove a tracked company board (by its source URL). Only `watched`
    rows are removable here — the seeded registry isn't editable from the
    roster; source families are toggled in Settings → Discovery sources."""
    target = url.rstrip("/")
    with _db(request).repos() as repos:
        prefs = repos.preferences.get_or_create()
        portals = dict(prefs.portals_config or {})
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
            repos.preferences.update(portals_config=portals)
    return dto.WatchRemoveResult(removed=removed)


# -- per-source efficacy analytics (Analytics → Discovery tab) ---------------

_RECENT_SCANS = 30

# Display identities for source ids that aren't adapter families in CATALOG:
# the real boards behind the Apify actors (rows are stamped with these as
# `source_adapter` — maintainer directive 2026-07-18: show "Naukri", never the
# "Apify" plumbing), plus friendlier analytics labels for the search families.
_ANALYTICS_LABELS: dict[str, tuple[str, str]] = {
    "naukri": ("Naukri (via Apify)", "search"),
    "indeed": ("Indeed (via Apify)", "search"),
    "seek": ("Seek (via Apify)", "search"),
    # One LinkedIn identity regardless of path (guest / logged-in / Apify actor)
    # — the paths already share canonical URLs and dedup.
    "linkedin": ("LinkedIn", "search"),
    "paste-url": ("Added by URL", "other"),
}


def _analytics_bucket_id(source_key: str) -> str:
    """Map a per-scan source key to its user-facing analytics identity.
    `apify:<actor>` buckets as the actor's real board (naukri/indeed/seek/
    linkedin); every other key buckets by its family prefix."""
    family, _, rest = source_key.partition(":")
    if family == "apify" and rest:
        return apify.ACTOR_SOURCE_IDS.get(rest, "apify")
    return family


@router.get("/api/discovery/analytics")
async def discovery_analytics(request: Request) -> dto.DiscoveryAnalyticsDTO:
    """Aggregates existing records only (no migration): stored `jobs` ×
    `source_adapter`, scores, applications, and the last `_RECENT_SCANS`
    scans' `result_ref.per_source` fetch/keep/error/latency numbers."""
    with _db(request).repos() as repos:
        jobs = repos.jobs.list_by_states(["active", "expired", "removed"])
        saved_ids = repos.applications.job_ids()
        profile = repos.profile.get_current()
        pv = profile.version if profile is not None else 0
        scores = repos.job_scores.latest_for_jobs([j.id for j in jobs], pv)
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
            b["jobs"] += 1
            if job.id in saved_ids:
                b["saved"] += 1
            score = scores.get(job.id)
            if score is not None:
                b["scored"] += 1
                b["score_sum"] += float(score.score_0_100)

        scans = sorted(scans, key=lambda o: o.started_at or o.created_at, reverse=True)
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


# -- BYO scraper keys (Apify / Brave) ----------------------------------------


def _data_dir(request: Request):  # noqa: ANN202 — Path, mirrors engines.py
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir is None:
        raise HTTPException(status_code=503, detail="data dir not initialized")
    return data_dir


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


def _seed_apify_sources(repos) -> None:  # noqa: ANN001 — Repos
    """First Apify key save: add the default actor `[[sources]]` entries so
    the sources exist, per-actor toggleable, without touching existing rows."""
    prefs = repos.preferences.get_or_create()
    portals = dict(prefs.portals_config or {})
    sources = list(portals.get("sources", []))
    present = {
        str(raw.get("actor", ""))
        for raw in sources
        if isinstance(raw, dict) and str(raw.get("board", "")) == "apify"
    }
    added = False
    for actor in apify.DEFAULT_ACTORS:
        if actor not in present:
            sources.append({"board": "apify", "actor": actor})
            added = True
    if added:
        portals["sources"] = sources
        repos.preferences.update(portals_config=portals)


def _seed_brave_source(repos) -> None:  # noqa: ANN001 — Repos
    """First Brave key save: add the single `board = "brave"` source entry."""
    prefs = repos.preferences.get_or_create()
    portals = dict(prefs.portals_config or {})
    sources = list(portals.get("sources", []))
    if any(
        isinstance(raw, dict) and str(raw.get("board", "")) == "brave" for raw in sources
    ):
        return
    sources.append({"board": "brave"})
    portals["sources"] = sources
    repos.preferences.update(portals_config=portals)


@router.get("/api/discovery/credentials")
async def list_discovery_credentials(request: Request) -> list[dto.DiscoveryCredentialDTO]:
    return _credentials(request)


@router.post("/api/discovery/credentials")
async def save_discovery_credential(
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
        if payload.id == "apify":
            _seed_apify_sources(repos)
        elif payload.id == "brave":
            _seed_brave_source(repos)
    return _credentials(request)


@router.delete("/api/discovery/credentials/{credential_id}")
async def delete_discovery_credential(
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
    # "no Apify API key" per-source error, and the user can untick them.
    return _credentials(request)
