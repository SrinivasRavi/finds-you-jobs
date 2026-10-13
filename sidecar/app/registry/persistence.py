"""Operation-entrypoint persistence helpers.

The wrappers call the modules; this persists their results into the app's DB
(architecture sections 5.3 and 5.6): scan writes Job rows (first-seen dedup,
tombstone suppression, a per-source report), score writes the job's score
columns, tailor and cover fill their Artifact."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from sidecar.modules.geo.resolve import chip_label, chip_name, resolve_location
from sidecar.modules.scraper.config import PortalsConfig, load_portals, parse_portals
from sidecar.modules.scraper.fingerprint import simhash64, to_signed64
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, ScanResult, ScraperError

from ..db import Database, Repos
from ..db.base import now_utc
from ..db.models import Job
from ..lifecycle import LIFECYCLE_DEFAULTS

SCORER_IMPL = "scorer-llm"

# The zero-LLM keyword scorer (Settings → Scoring "Keyword scoring" mode, and
# the grey fallback when an LLM score fails) — sidecar/modules/scorer/
# deterministic.py. Same JobScore table, distinct impl tag so the two are
# never confusable.
SCORER_IMPL_DETERMINISTIC = "scorer-deterministic"


# Every scoring method the user can pick, in menu order, richest explanation
# first (`scoring.md` section 7.1). `local` is listed because the menu never
# hides a method for a missing prerequisite (`talk.md` D44); its pack download is
# not built yet, so picking it falls back to the keyword floor and says so.
SCORING_MODES: tuple[str, ...] = ("llm", "jev", "local", "keyword")
DEFAULT_SCORING_MODE = "llm"


def scoring_mode(prefs: Any) -> str:
    """The Settings → Scoring method. The ONE reader of
    `thresholds["scoring_mode"]`, so the default and the key can never drift
    apart, and an unrecognized stored value reads as the default rather than
    breaking dispatch."""
    stored = str((getattr(prefs, "thresholds", None) or {}).get("scoring_mode") or "")
    return stored if stored in SCORING_MODES else DEFAULT_SCORING_MODE


def scoring_weights(prefs: Any) -> tuple[dict[str, float], int]:
    """The user's axis weights and location penalty, or the empty table, the 1
    reader of `score_weight_preset` and `score_weights`. Empty means each method's
    own weights, which is `balanced` (scoring.md 7.4). Custom sliders outrank the
    preset."""
    from sidecar.modules.scorer.weights import (
        LOCATION_MISS_PENALTY,
        PRESET_LOCATION_PENALTY,
        PRESETS,
        WEIGHTED_AXES,
    )

    thresholds = getattr(prefs, "thresholds", None) or {}
    preset = str(thresholds.get("score_weight_preset") or "balanced")
    if preset not in PRESETS:
        preset = "balanced"
    stored_penalty = thresholds.get("score_location_penalty")
    penalty = (
        int(stored_penalty)
        if isinstance(stored_penalty, int | float)
        else PRESET_LOCATION_PENALTY.get(preset, LOCATION_MISS_PENALTY)
    )

    custom = thresholds.get("score_weights")
    if isinstance(custom, dict):
        table = {
            axis: float(custom[axis])
            for axis in WEIGHTED_AXES
            if isinstance(custom.get(axis), int | float)
        }
        if table and any(v > 0 for v in table.values()):
            return table, penalty
    return dict(PRESETS[preset]), penalty


# The user-configurable windows below are DERIVED from `LIFECYCLE_DEFAULTS`
# (D-A15) — they used to be re-typed here, so a Settings default change could
# silently disagree with the fallback these functions use. Single home:
# `app/lifecycle.py`.

# Trash TTL — a removed job is tombstoned this many days after it entered Trash
# (FR-JB-12 / FR-SYS-04: "permanently removed after 7 days").
TRASH_TTL_DAYS = LIFECYCLE_DEFAULTS["trashed_jobs_purge_days"]

# Expired aging (FR-SYS-03): a feed job greys to `Expired` this many days after
# it entered the feed, and is hard-deleted (no tombstone) that many days after
# entering Expired. A still-live posting can re-enter on a later scrape. The
# delete window has no lifecycle key — it is not user-configurable.
EXPIRE_AFTER_DAYS = LIFECYCLE_DEFAULTS["expire_listing_days"]
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
    """The scan's effective filters: an explicit snapshot `prefs` override; else the
    onboarding preferences merged over the config's tables (roles, locations,
    freshness, and excludes unioned with the config's own blocks; empty dimensions
    keep the config); else None, the config as-is."""
    prefs_data = snapshot.get("prefs")
    if isinstance(prefs_data, dict):
        return ScanPrefs(**prefs_data)
    if repos is None:
        return None
    row = repos.preferences.get_or_create()
    aliases = [str(a) for a in (row.role_aliases or []) if str(a).strip()]
    locations = [chip_name(loc) for loc in (row.locations or []) if chip_name(loc).strip()]
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
        _with_place_match(base, row.locations or []),
        title_allow=aliases or base.title_allow,
        location_allow=expanded or base.location_allow,
        location_always_allow=expanded or base.location_always_allow,
        max_age_days=freshness if freshness > 0 else base.max_age_days,
        company_block=list(dict.fromkeys([*base.company_block, *company_block])),
        content_block=list(dict.fromkeys([*base.content_block, *content_block])),
    )


def _with_place_match(base: ScanPrefs, chips: list[Any]) -> ScanPrefs:
    """Keep a posting whose place lies inside a wanted one, which a word match can't
    see: "Pune, India" for a Maharashtra chip, "Milpitas, CA" for the Bay Area (audit
    A7). Only a MATCH keeps it; anything else falls through to the word filter."""
    from sidecar.modules.geo.resolve import Verdict, match, resolve_location, resolve_wanted

    wanted = resolve_wanted(chips)
    if not (wanted.city_ids or wanted.admin1_keys or wanted.countries):
        return base
    return replace(
        base, location_match=lambda text: match(resolve_location(text), wanted)[0] is Verdict.MATCH
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
    """Open the sealed Apify and Brave keys for this scan only. Stored as
    `scraper:<id>` rows (NFR-SEC-01), invisible to the engine registry; opened keys
    go to `ScanPrefs.credentials`, never a snapshot, result or log."""
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
    new_job_ids: list[str] = []
    if db is not None:
        with db.repos() as repos:
            for job in result.jobs:
                if repos.tombstones.exists(job.canonical_url):
                    tombstoned += 1
                    continue
                if repos.jobs.get_by_canonical_url(job.canonical_url) is not None:
                    deduped += 1  # first-seen wins (FR-SYS-01)
                    continue
                new_job_ids.append(repos.jobs.create(**_job_columns(job)).id)
                persisted += 1
            # The duplicate notice (url-less-jobs.md 9.6) for every row this scan
            # wrote, against the whole active board including each other. Without
            # it a scanned row was never compared, so pasted-Monday,
            # scanned-Wednesday could never be noticed.
            if new_job_ids:
                rows = repos.jobs.fingerprint_rows()
                for job_id in new_job_ids:
                    written = repos.jobs.get(job_id)
                    if written is not None:
                        flag_near_duplicate(repos, written, rows=rows)

    fetched = sum(r.fetched for r in result.per_source.values())
    kept = sum(r.kept for r in result.per_source.values())
    per_source = {
        key: {
            "fetched": r.fetched,
            "kept": r.kept,
            "http_calls": r.usage.internal_calls,
            "latency_ms": r.usage.latency_ms,
            "errors": list(r.errors),
            # The URLs discarded for having no scorable description (D23). They
            # are recorded rather than counted so a source that silently
            # produces husks can be opened and looked at.
            "dropped_no_description": list(r.dropped_no_description),
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
            # The ids this scan actually inserted — the board flags the latest
            # succeeded scan's set as "NEW" (maintainer 2026-07-23).
            "new_job_ids": new_job_ids,
            "sources": len(result.per_source),
            "errors": sum(len(r.errors) for r in result.per_source.values()),
            "dropped_no_description": sum(
                len(r.dropped_no_description) for r in result.per_source.values()
            ),
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
        # Stored rather than recomputed per comparison: the scan already pays
        # for it, and the promotion check and duplicate prompt read it per
        # card (url-less-jobs.md section 5.2). 0 means "too short to shingle".
        "fingerprint": to_signed64(simhash64(job.description)) or None,
    }


BACKFILL_LOCATION_BATCH = 500


def backfill_resolved_locations(
    db: Database | None, *, limit: int = BACKFILL_LOCATION_BATCH
) -> int:
    """Resolve locations for rows that predate the resolver; returns the count.
    Converges, since `location_remote IS NULL` stops matching once touched. Bounded
    per call: the maintenance tick has other stages."""
    if db is None:
        return 0
    with db.repos() as repos:
        pending = repos.jobs.list_unresolved_locations(limit=limit)
        for job in pending:
            resolved = resolve_location(job.location or "")
            primary = resolved.primary
            repos.jobs.update(
                job.id,
                location_country=primary.country if primary else None,
                location_admin1=primary.admin1_key if primary else None,
                location_city_id=primary.city_id if primary else None,
                location_remote=resolved.remote_scope,
                location_attendance=str(resolved.attendance) if resolved.attendance else None,
            )
        return len(pending)


def backfill_fingerprints(db: Database | None, *, limit: int = BACKFILL_LOCATION_BATCH) -> int:
    """Fingerprint rows from before migration `c9e4a71b3f02`; returns the count.
    Too-short text stores 0, so it converges. Flags nothing itself."""
    if db is None:
        return 0
    with db.repos() as repos:
        pending = repos.jobs.list_unfingerprinted(limit=limit)
        for job in pending:
            repos.jobs.update(job.id, fingerprint=to_signed64(simhash64(job.description)))
        return len(pending)


# Jev is reached on the OpenRouter key the user may already have for everything
# else, so "do you have one" is a database read. The key is opened for THIS
# operation only, never cached and never written into a snapshot or a log, the
# same discipline as the scraper credentials above.
DECISIONS_ENGINE_ID = "openrouter"


def decisions_key_present(repos: Repos) -> bool:
    """Whether a usable key for the decision model is stored, without opening it.

    The Settings row reads this to say "add a key" instead of going grey: a method
    is never hidden for a missing prerequisite (`talk.md` D44).
    """
    row = repos.engine_settings.get_by_engine(DECISIONS_ENGINE_ID)
    return bool(row is not None and row.key_encrypted and row.enabled)


def load_decisions_client(repos: Repos) -> Any:
    """The Jev client for this operation, or None when no key is stored."""
    from ..db.database import resolve_data_dir
    from ..security import get_app_key, open_secret
    from .engines_http import DecisionsHttpClient

    row = repos.engine_settings.get_by_engine(DECISIONS_ENGINE_ID)
    if row is None or not row.key_encrypted or not row.enabled:
        return None
    try:
        api_key = open_secret(row.key_encrypted, get_app_key(resolve_data_dir()))
    except Exception:  # noqa: BLE001 — a corrupt key is a missing key, not a crash
        import logging

        logging.getLogger("fyj.sidecar").exception("could not open the sealed decisions key")
        return None
    return DecisionsHttpClient(api_key=api_key)


def render_preferences_block(prefs: Any) -> str:
    """The user's preferences as the Scorer's third prompt block, empty when none
    are set. The wording is the bench's (evidence/2026-09-25-scoring-bench), the
    text the 2026-09-28 measurement used."""
    roles = [str(r) for r in (getattr(prefs, "role_aliases", None) or []) if str(r).strip()]
    chips = getattr(prefs, "locations", None) or []
    locations = [chip_label(loc) for loc in chips if chip_label(loc).strip()]
    excludes = getattr(prefs, "hard_excludes", None) or {}
    companies = [str(c) for c in (excludes.get("companies") or []) if str(c).strip()]
    keywords = [str(k) for k in (excludes.get("keywords") or []) if str(k).strip()]
    freshness = int(getattr(prefs, "freshness_days", 0) or 0)
    if not (roles or locations or companies or keywords):
        return ""
    lines = [
        "The user set these in the product's job-finder preferences. They state what this "
        "user wants, and they outrank the resume wherever the two disagree.",
        "",
        f"Roles wanted: {', '.join(roles) or 'any'}",
        f"Locations wanted: {', '.join(locations) or 'any'}",
        f"Excluded companies: {', '.join(companies) or 'none'}",
        f"Excluded keywords: {', '.join(keywords) or 'none'}",
    ]
    if freshness > 0:
        lines.append(f"Posting freshness wanted: within {freshness} days")
    return "\n".join(lines)


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


def flag_near_duplicate(
    repos: Repos, job: Job, *, rows: list[tuple[str, int, str, str]] | None = None
) -> str | None:
    """Record a new job as a probable copy of every active job it matches
    (url-less-jobs.md 9.6), on both write paths. `rows` is `fingerprint_rows()`
    read once by a batch caller. Returns the first match, or None."""
    others = repos.jobs.near_duplicate_ids(
        job.fingerprint, company=job.company, title=job.title, exclude_id=job.id,
        rows=rows,
    )
    if not others:
        return None
    repos.duplicates.record(job.id, others)
    return others[0]


def load_job_and_master(
    repos: Repos, snapshot: dict[str, Any]
) -> tuple[str, str, int]:
    """Resolve `(job_text, master_md, profile_version)` for an LLM op: inline snapshot
    values win, else the DB by `job_id` and the current profile. Raises
    `LookupError` when an input is missing."""
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
    """Tombstone and delete every trashed job past the TTL (US-SYS-04), as if the
    user emptied Trash. Unstamped rows are stamped and skipped this tick, so the
    clock starts once. Returns the tombstoned ids."""
    if db is None:
        return []
    now = now or now_utc()
    cutoff = now - timedelta(days=ttl_days)
    tombstoned: list[str] = []
    with db.repos() as repos:
        repos.jobs.stamp_missing_lifecycle_dates(now=now)
        for job in repos.jobs.list_trashed_before(cutoff):
            tombstone_url(repos, job, reason="trash_ttl")
            delete_job_cascade(repos, job.id)
            tombstoned.append(job.id)
    return tombstoned


def age_expired_jobs(
    db: Database | None,
    *,
    now: datetime | None = None,
    expire_after_days: int = EXPIRE_AFTER_DAYS,
    delete_after_days: int = EXPIRED_DELETE_AFTER_DAYS,
) -> dict[str, list[str]]:
    """TTL aging for feed jobs (FR-SYS-03), daily: active to expired after
    `expire_after_days` from `feed_since`, then hard-deleted after
    `delete_after_days`, with no tombstone so a scan can bring it back. Ages only
    scanned jobs: a hand-added one, link or not, is no scan's to replace, the same
    test `tombstone_url` asks (url-less-jobs.md 5.6, 8.3). Skips saved jobs.
    Returns `{"expired": [...], "deleted": [...]}`."""
    if db is None:
        return {"expired": [], "deleted": []}
    now = now or now_utc()
    expire_cutoff = now - timedelta(days=expire_after_days)
    delete_cutoff = now - timedelta(days=delete_after_days)
    expired: list[str] = []
    deleted: list[str] = []
    with db.repos() as repos:
        repos.jobs.stamp_missing_lifecycle_dates(now=now)
        # The applications table lands with the tracker commit; until then no
        # job can be Saved, so nothing is rescued from aging.
        saved: set[str] = saved_job_ids(repos)

        # Stage 2 first: hard-delete stale Expired rows (no tombstone).
        for job in repos.jobs.list_expired_before(delete_cutoff):
            if job.id in saved:
                continue  # Saved — never auto-delete
            delete_job_cascade(repos, job.id)  # no Tombstone — FR-SYS-03
            deleted.append(job.id)

        # Stage 1: grey out active jobs past the freshness window.
        for job in repos.jobs.list_active_before(expire_cutoff):
            if job.id in saved:
                continue
            repos.jobs.set_expired(job.id, now=now)
            expired.append(job.id)
    return {"expired": expired, "deleted": deleted}


def tombstone_url(repos: Repos, job: Job, *, reason: str) -> bool:
    """Tombstone a scanned job's URL so no scan re-adds it (FR-SYS-04). True if
    it wrote or found one. A job the user added by hand is only deleted, link or
    not (url-less-jobs.md section 8.2)."""
    if not job.canonical_url or job.origin != "scan":
        return False
    if not repos.tombstones.exists(job.canonical_url):
        repos.tombstones.create(job.canonical_url, reason=reason)
    return True


def saved_job_ids(repos: Repos) -> set[str]:
    """Job ids rescued by a Saved application: never auto-expired or deleted, and
    excluded from the board server-side (US-JB-06). Public because `api/jobs`
    reads it too (duplication audit D-A17)."""
    return set(repos.applications.job_ids())


def delete_job_cascade(repos: Repos, job_id: str) -> bool:
    """The ONE job cascade; all 4 delete paths call it, so a new FK child
    cannot wedge one path while another keeps working (S-C46). Referral
    candidates hard-delete; outreach logs nullify, because the log is audit
    data. Applications are not touched: `saved_job_ids` rescues a saved job
    before deletion, and trashing requires unsaving first."""
    repos.referral_candidates.delete_for_job(job_id)
    repos.outreach_logs.nullify_for_job(job_id)
    return repos.jobs.delete(job_id)


def delete_application_cascade(
    repos: Repos, application_id: str
) -> tuple[bool, list[str], list[str]]:
    """Delete 1 application and every FK child (events, documents, apply runs), the
    1 cascade (D-A3) both callers use, so a new child can't wedge one path. Returns
    `(deleted, apply_run_ids, orphaned_document_hashes)`; purging the files is the
    caller's job."""
    run_ids = [r.id for r in repos.apply_runs.list_for_application(application_id)]
    repos.application_events.delete_for_application(application_id)
    orphaned = repos.documents.delete_for_application(application_id)
    repos.apply_runs.delete_for_application(application_id)
    return repos.applications.delete(application_id), run_ids, orphaned


def purge_archived_applications(
    db: Database | None, *, retention_days: int, now: datetime | None = None
) -> list[str]:
    """Permanently delete archived tracker cards past the retention window
    (FR-SYS-06). Cascades the card's artifacts (ORM) + events; the underlying
    `Job` is untouched (it may re-surface in the feed). Returns the purged
    application ids."""
    if db is None:
        return []
    from .. import documents
    from .apply_op import purge_run_dirs

    now = now or now_utc()
    cutoff = now - timedelta(days=retention_days)
    purged: list[str] = []
    run_ids: list[str] = []
    orphaned: list[str] = []
    with db.repos() as repos:
        for app in repos.applications.list_archived_before(cutoff):
            deleted, app_run_ids, app_orphans = delete_application_cascade(repos, app.id)
            run_ids += app_run_ids
            orphaned += app_orphans
            if deleted:
                purged.append(app.id)
    # F-M8: the runs' on-disk artifact dirs go with the rows, and so do the
    # uploaded blobs no surviving row names (S-C37 — a purge used to leak both
    # the row and the file). Best-effort, path-guarded. Sync context: retention
    # runs on a runner worker thread.
    purge_run_dirs(run_ids)
    for sha in orphaned:
        documents.delete_blob(sha, db.data_dir)
    return purged


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
