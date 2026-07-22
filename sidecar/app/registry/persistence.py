"""Operation-entrypoint persistence helpers.

The wrappers call the modules; this file makes them persist their results into
the DB the app owns (architecture §5.3/§5.6, database-design §3–§4):

- scan → `Job` rows, canonical-URL dedup (first-seen wins) + `Tombstone`
  suppression + a per-source report into `result_ref`;
- score → a cached `JobScore` row for `(job_id, profile_version, scorer_impl)`;
- tailor/cover → an `Artifact` row (lands with the applications-schema commit).

This is `app/` code — using `Repos` and importing `modules/` is exactly the
one-way rule's allowed direction; modules never import back.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from sidecar.modules.scraper.config import PortalsConfig, load_portals, parse_portals
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, ScanResult, ScraperError

from ..db import Database, Repos
from ..db.base import now_utc

SCORER_IMPL = "scorer-llm"

# The zero-LLM keyword scorer (Settings → Scoring "Keyword scoring" mode, and
# the grey fallback when an LLM score fails) — sidecar/modules/scorer/
# deterministic.py. Same JobScore table, distinct impl tag so the two are
# never confusable.
SCORER_IMPL_DETERMINISTIC = "scorer-deterministic"

# Trash TTL — a removed job is tombstoned this many days after it entered Trash
# (FR-JB-12 / FR-SYS-04: "permanently removed after 7 days").
TRASH_TTL_DAYS = 7

# Expired aging (FR-SYS-03): a feed job greys to `Expired` this many days after
# it entered the feed, and is hard-deleted (no tombstone) that many days after
# entering Expired. A still-live posting can re-enter on a later scrape.
EXPIRE_AFTER_DAYS = 14
EXPIRED_DELETE_AFTER_DAYS = 30


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def resolve_portals(snapshot: dict[str, Any], repos: Repos) -> str | PortalsConfig:
    """The portals config for a scan: the snapshot's, else the user's stored one
    (`UserPreferences.portals_config`, seeded at first run)."""
    from_snap = snapshot.get("portals_config")
    if from_snap is None:
        prefs = repos.preferences.get_or_create()
        from_snap = prefs.portals_config
    if isinstance(from_snap, dict):
        return parse_portals(from_snap, where="user portals config")
    # A path string (.toml/.json) — scan() loads it.
    return str(from_snap)


def resolve_scan_prefs(
    snapshot: dict[str, Any],
    repos: Repos | None = None,
    portals: str | PortalsConfig | None = None,
) -> ScanPrefs | None:
    """The scan's effective filters, in precedence order:

    1. An explicit `prefs` override in the operation snapshot (tests/CLI).
    2. The user's onboarding preferences (US-OB-03 — "so the cold-start scrape
       knows what to look for") merged over the config's own filter tables:
       role aliases → `title_allow`; locations → `location_allow` **and**
       `location_always_allow` (the multi-location rescue names the user's
       places, not the shipped defaults); freshness window → `max_age_days`;
       `hard_excludes.companies`/`.keywords` → `company_block`/`content_block`,
       **unioned** with the config's own block lists rather than replacing them
       — a personal exclude should never silently drop a registry's own
       curated blocks (job-finder-preferences design, docs/internal/discovery.md).
       Dimensions the user left empty keep the config's values.
    3. None — the config's own filters as-is (the seeded registry's tuned
       defaults; only reached when the user set no preferences at all).
    """
    prefs_data = snapshot.get("prefs")
    if isinstance(prefs_data, dict):
        return ScanPrefs(**prefs_data)
    if repos is None:
        return None
    row = repos.preferences.get_or_create()
    aliases = [str(a) for a in (row.role_aliases or []) if str(a).strip()]
    locations = [str(loc) for loc in (row.locations or []) if str(loc).strip()]
    freshness = row.freshness_days or 0
    hard_excludes = row.hard_excludes or {}
    company_block = [
        str(c) for c in (hard_excludes.get("companies") or []) if str(c).strip()
    ]
    content_block = [
        str(k) for k in (hard_excludes.get("keywords") or []) if str(k).strip()
    ]
    if (
        not aliases
        and not locations
        and freshness <= 0
        and not company_block
        and not content_block
    ):
        return None
    base = _config_prefs(portals)
    expanded = _expand_locations(locations)
    return replace(
        base,
        title_allow=aliases or base.title_allow,
        location_allow=expanded or base.location_allow,
        location_always_allow=expanded or base.location_always_allow,
        max_age_days=freshness if freshness > 0 else base.max_age_days,
        company_block=list(dict.fromkeys([*base.company_block, *company_block])),
        content_block=list(dict.fromkeys([*base.content_block, *content_block])),
    )


# Location synonyms — same place, different common spellings. Word-boundary
# matching treats them as unrelated tokens, so a user typing one form silently
# lost every role labeled with the other (2026-07-12 registry-yield analysis:
# "Bengaluru" dropped all "Bangalore"-labeled Lever/Ashby roles). Deliberately
# tiny: true renames only, never "nearby city" guesses.
_LOCATION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "bengaluru": ("bangalore",),
    "bangalore": ("bengaluru",),
    "gurgaon": ("gurugram",),
    "gurugram": ("gurgaon",),
    "mumbai": ("bombay",),
    "bombay": ("mumbai",),
    "kolkata": ("calcutta",),
    "calcutta": ("kolkata",),
    "chennai": ("madras",),
    "madras": ("chennai",),
}


def _expand_locations(locations: list[str]) -> list[str]:
    """The user's locations plus known same-place spellings (dedup, case-kept)."""
    out = list(locations)
    seen = {loc.strip().lower() for loc in locations}
    for loc in locations:
        for syn in _LOCATION_SYNONYMS.get(loc.strip().lower(), ()):
            if syn not in seen:
                out.append(syn)
                seen.add(syn)
    return out


def _config_prefs(portals: str | PortalsConfig | None) -> ScanPrefs:
    """The config's own filter tables — the merge base for user preferences."""
    if isinstance(portals, PortalsConfig):
        return portals.prefs
    if isinstance(portals, str):
        try:
            return load_portals(portals).prefs
        except ScraperError:
            return ScanPrefs()
    return ScanPrefs()


SCRAPER_CREDENTIAL_IDS = ("apify", "brave")
SCRAPER_ENGINE_PREFIX = "scraper:"


def load_scraper_credentials(repos: Repos) -> dict[str, str]:
    """Open the sealed BYO scraper keys (Apify/Brave) for THIS scan only.

    The keys live as `scraper:<id>` rows in `engine_settings` (same sealed-BLOB
    discipline as BYOK LLM keys — NFR-SEC-01) but are invisible to the engine
    registry (`PROVIDERS.get` skips unknown ids) and to the Settings engines
    list. The opened secrets go into the in-memory `ScanPrefs.credentials`
    only — never into the durable operation snapshot, a result_ref, or a log.

    Key resolution uses `resolve_data_dir()` (env/platform), matching the boot
    path; hermetic tests set `FYJ_SESSION_KEY` so no keychain is touched."""
    from ..db.database import resolve_data_dir
    from ..security import get_app_key, open_secret

    creds: dict[str, str] = {}
    app_key: str | None = None
    for cid in SCRAPER_CREDENTIAL_IDS:
        row = repos.engine_settings.get_by_engine(f"{SCRAPER_ENGINE_PREFIX}{cid}")
        if row is None or not row.key_encrypted or not row.enabled:
            continue
        if app_key is None:
            app_key = get_app_key(resolve_data_dir())
        try:
            creds[cid] = open_secret(row.key_encrypted, app_key)
        except Exception:  # noqa: BLE001 — a corrupt key must not kill the scan
            import logging

            logging.getLogger("fyj.sidecar").exception(
                "could not open sealed scraper credential %r", cid
            )
    return creds


def with_credentials(
    prefs: ScanPrefs | None,
    portals: str | PortalsConfig | None,
    creds: dict[str, str],
) -> ScanPrefs | None:
    """Attach opened scraper credentials to the effective scan prefs. When the
    user set no prefs at all (`prefs=None`), the config's own filter tables
    become the base so behavior stays identical apart from the credentials."""
    if not creds:
        return prefs
    base = prefs if prefs is not None else _config_prefs(portals)
    return replace(base, credentials=dict(creds))


# Brave free tier ≈ 2,000 queries/month. The ledger stops the scan spending
# past it (approved-plan commit 5); the counter lives in
# `UserPreferences.thresholds["brave_query_ledger"]` = {"month": "YYYY-MM",
# "used": N} and resets on month rollover.
BRAVE_MONTHLY_BUDGET = 2000
BRAVE_LEDGER_KEY = "brave_query_ledger"


def _brave_ledger(row: Any, now: datetime) -> dict[str, Any]:
    ledger = (row.thresholds or {}).get(BRAVE_LEDGER_KEY) or {}
    month = now.strftime("%Y-%m")
    if not isinstance(ledger, dict) or ledger.get("month") != month:
        return {"month": month, "used": 0}
    return {"month": month, "used": int(ledger.get("used", 0) or 0)}


def apply_brave_budget(
    prefs: ScanPrefs | None,
    portals: str | PortalsConfig | None,
    repos: Repos,
    *,
    now: datetime | None = None,
) -> ScanPrefs | None:
    """Disable the Brave source for THIS scan once the month's free-tier query
    budget is spent (in-memory only — the user's own toggle state is
    untouched, and next month it resumes by itself)."""
    now = now or now_utc()
    ledger = _brave_ledger(repos.preferences.get_or_create(), now)
    if ledger["used"] < BRAVE_MONTHLY_BUDGET:
        return prefs
    base = prefs if prefs is not None else _config_prefs(portals)
    if "brave" in base.disabled_sources:
        return prefs
    return replace(base, disabled_sources=[*base.disabled_sources, "brave"])


def record_brave_usage(
    db: Database | None, result_ref: dict[str, Any], *, now: datetime | None = None
) -> None:
    """Add this scan's Brave HTTP calls to the monthly ledger."""
    if db is None:
        return
    calls = sum(
        int(r.get("http_calls") or 0)
        for key, r in (result_ref.get("per_source") or {}).items()
        if key.startswith("brave:")
    )
    if not calls:
        return
    now = now or now_utc()
    with db.repos() as repos:
        row = repos.preferences.get_or_create()
        ledger = _brave_ledger(row, now)
        ledger["used"] += calls
        thresholds = dict(row.thresholds or {})
        thresholds[BRAVE_LEDGER_KEY] = ledger
        repos.preferences.update(thresholds=thresholds)


def persist_scan(db: Database | None, result: ScanResult) -> dict[str, Any]:
    """Persist scanned jobs (dedup + tombstone suppression) and build the
    per-source `result_ref`. Returns the result_ref payload."""
    persisted = deduped = tombstoned = 0
    if db is not None:
        with db.repos() as repos:
            for job in result.jobs:
                if repos.tombstones.exists(job.canonical_url):
                    tombstoned += 1
                    continue
                if repos.jobs.get_by_canonical_url(job.canonical_url) is not None:
                    deduped += 1  # first-seen wins (FR-SYS-01)
                    continue
                repos.jobs.create(**_job_columns(job))
                persisted += 1

    fetched = sum(r.fetched for r in result.per_source.values())
    kept = sum(r.kept for r in result.per_source.values())
    per_source = {
        key: {
            "fetched": r.fetched,
            "kept": r.kept,
            "http_calls": r.usage.internal_calls,
            "latency_ms": r.usage.latency_ms,
            "errors": list(r.errors),
        }
        for key, r in result.per_source.items()
    }
    return {
        "scan": {
            "found": len(result.jobs),
            "fetched": fetched,
            "kept": kept,
            "persisted": persisted,
            "deduped": deduped,
            "tombstoned": tombstoned,
            "sources": len(result.per_source),
            "errors": sum(len(r.errors) for r in result.per_source.values()),
        },
        "per_source": per_source,
    }


def _job_columns(job: NormalizedJob) -> dict[str, Any]:
    return {
        "canonical_url": job.canonical_url,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "description": job.description,
        "posted_at": job.posted_at or None,
        "salary": job.salary or None,
        "source_adapter": job.source_adapter or "unknown",
        "trust_score": job.trust_score,
        "trust_flags": list(job.trust_flags),
    }


def scan_usage(result: ScanResult) -> dict[str, Any]:
    """Aggregate the zero-LLM scan usage (HTTP calls + latency)."""
    http_calls = sum(r.usage.internal_calls for r in result.per_source.values())
    latency = sum((r.usage.latency_ms or 0) for r in result.per_source.values())
    return {
        "internal_calls": http_calls,
        "tokens_in": None,
        "tokens_out": None,
        "usd": None,
        "latency_ms": latency,
        "model": None,
    }


# ---------------------------------------------------------------------------
# score / tailor / cover — shared job+master loading
# ---------------------------------------------------------------------------


def load_job_and_master(
    repos: Repos, snapshot: dict[str, Any]
) -> tuple[str, str, int]:
    """Resolve `(job_text, master_md, profile_version)` for an LLM operation.

    Inline `master_md`/`job` in the snapshot win (direct-API callers/tests);
    otherwise both are loaded from the DB by `job_id` + the current master
    profile. Raises `LookupError` (surfaced verbatim on the operation row) when
    a required input is missing."""
    master_md = snapshot.get("master_md")
    profile_version = snapshot.get("profile_version")
    if master_md is None or profile_version is None:
        profile = repos.profile.get_current()
        if profile is None:
            raise LookupError("no master profile set — import a resume in onboarding first")
        master_md = master_md if master_md is not None else profile.resume_markdown
        profile_version = (
            profile_version if profile_version is not None else profile.version
        )

    job_text = snapshot.get("job")
    if job_text is None:
        job_id = snapshot.get("job_id")
        if job_id is None:
            raise LookupError("score/tailor/cover operation needs a job_id or inline job")
        job = repos.jobs.get(job_id)
        if job is None:
            raise LookupError(f"job {job_id!r} not found")
        job_text = compose_job_text(job)

    return job_text, master_md, int(profile_version)


# ---------------------------------------------------------------------------
# lifecycle (FR-SYS-03/04)
# ---------------------------------------------------------------------------


def evict_stale_trash(
    db: Database | None, *, now: datetime | None = None, ttl_days: int = TRASH_TTL_DAYS
) -> list[str]:
    """Tombstone + hard-delete every Trashed job past the TTL (US-SYS-04).

    "Let it go stale = not meant to be" — a job left in Trash past `ttl_days`
    is tombstoned (its `canonical_url` suppressed from future scrapes, per
    FR-SYS-04) and removed, exactly as if the user emptied Trash. Legacy rows
    with no `trashed_at` stamp are lazily backfilled and skipped this tick, so
    the clock only ever starts once. Returns the tombstoned job ids."""
    if db is None:
        return []
    now = now or now_utc()
    cutoff = now - timedelta(days=ttl_days)
    tombstoned: list[str] = []
    with db.repos() as repos:
        for job in repos.jobs.list(feed_state="removed", limit=10_000):
            meta = job.source_meta or {}
            stamp = meta.get("trashed_at")
            if stamp is None:
                # Trashed before the TTL bookkeeping existed — start its clock now.
                repos.jobs.update(job.id, source_meta={**meta, "trashed_at": now.isoformat()})
                continue
            try:
                trashed_at = datetime.fromisoformat(stamp)
            except (TypeError, ValueError):
                repos.jobs.update(job.id, source_meta={**meta, "trashed_at": now.isoformat()})
                continue
            if trashed_at <= cutoff:
                if not repos.tombstones.exists(job.canonical_url):
                    repos.tombstones.create(job.canonical_url, reason="trash_ttl")
                repos.jobs.delete(job.id)
                tombstoned.append(job.id)
    return tombstoned


def age_expired_jobs(
    db: Database | None,
    *,
    now: datetime | None = None,
    expire_after_days: int = EXPIRE_AFTER_DAYS,
    delete_after_days: int = EXPIRED_DELETE_AFTER_DAYS,
) -> dict[str, list[str]]:
    """TTL aging for feed jobs (FR-SYS-03), run by the daily maintenance tick.

    Two stages, both **skipping any job that has an Application** (Saving rescues
    it — the Saved/Tracker side is never auto-expired):

    1. **active → expired** once a job is older than `expire_after_days`. The age
       clock reads `source_meta["feed_since"]` when present (set by an explicit
       un-expire, which resets the timer), else `ingested_at`.
    2. **expired → hard-delete** once it has sat in Expired for `delete_after_days`
       (measured from `source_meta["expired_at"]`). The delete writes **no
       tombstone** — a later scrape may re-surface the same posting.

    Legacy Expired rows with no `expired_at` stamp are lazily backfilled and
    skipped this tick, so their 30-day clock only ever starts once. Returns
    `{"expired": [...ids], "deleted": [...ids]}`."""
    if db is None:
        return {"expired": [], "deleted": []}
    now = now or now_utc()
    expire_cutoff = now - timedelta(days=expire_after_days)
    delete_cutoff = now - timedelta(days=delete_after_days)
    expired: list[str] = []
    deleted: list[str] = []
    with db.repos() as repos:
        # The applications table lands with the tracker commit; until then no
        # job can be Saved, so nothing is rescued from aging.
        saved: set[str] = _saved_job_ids(repos)

        # Stage 2 first: hard-delete stale Expired rows (no tombstone).
        for job in repos.jobs.list_by_states(["expired"]):
            if job.id in saved:
                continue  # Saved rescues it — never auto-delete
            meta = job.source_meta or {}
            stamp = meta.get("expired_at")
            entered = _parse_iso(stamp)
            if entered is None:
                repos.jobs.update(job.id, source_meta={**meta, "expired_at": now.isoformat()})
                continue
            if entered <= delete_cutoff:
                repos.jobs.delete(job.id)  # no Tombstone — FR-SYS-03
                deleted.append(job.id)

        # Stage 1: grey out active jobs past the freshness window.
        for job in repos.jobs.list_by_states(["active"]):
            if job.id in saved:
                continue
            meta = job.source_meta or {}
            reference = _parse_iso(meta.get("feed_since")) or job.ingested_at
            if reference is not None and reference <= expire_cutoff:
                repos.jobs.set_expired(job.id, now=now)
                expired.append(job.id)
    return {"expired": expired, "deleted": deleted}


def _saved_job_ids(repos: Repos) -> set[str]:
    """Job ids rescued by a Saved application — never auto-expired/deleted."""
    return repos.applications.job_ids()


def purge_archived_applications(
    db: Database | None, *, retention_days: int, now: datetime | None = None
) -> list[str]:
    """Permanently delete archived tracker cards past the retention window
    (FR-SYS-06). Cascades the card's artifacts (ORM) + events; the underlying
    `Job` is untouched (it may re-surface in the feed). Returns the purged
    application ids."""
    if db is None:
        return []
    now = now or now_utc()
    cutoff = now - timedelta(days=retention_days)
    purged: list[str] = []
    with db.repos() as repos:
        for app in repos.applications.list_archived_before(cutoff):
            repos.application_events.delete_for_application(app.id)
            if repos.applications.delete(app.id):
                purged.append(app.id)
    return purged


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return value if isinstance(value, datetime) else None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def compose_job_text(job: Any) -> str:
    """Render a stored `Job` row as the JD markdown the modules consume."""
    header = f"# {job.title}"
    meta = " · ".join(p for p in (job.company, job.location) if p)
    body = job.description or ""
    parts = [header]
    if meta:
        parts.append(meta)
    if body:
        parts.append(body)
    return "\n\n".join(parts)
