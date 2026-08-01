"""LinkedIn session-capture + lifecycle operations (Track N4).

Two bounded ops beyond the N3 discover/draft/send core:

- `linkedin_login` — the headed-browser session capture (US-SET-06 as-built).
  Opens LinkedIn's own login page in a **visible** Chromium (driven by the GPL
  `voyager_py login` subprocess), waits for the user to finish logging in (incl.
  2FA), detects the `li_at` auth cookie, and persists the Playwright
  storage-state under the app-data dir. **The password never passes through
  finds-you-jobs** — the human logs in themselves. Long-running + user-interactive,
  cancellable via `LOGIN_CONTROL` (mirrors the Applier's takeover latch, §A5b).
  Streams typed `linkedin` SSE events for the connect UI + pill.

- `archive_stale_contacts` — the US-NW-11 / FR-NW-13 scheduler tick: auto-archive
  connections that stayed `Sent` (never `Accepted`) for 60 days. Zero-LLM,
  zero-network, non-destructive (the same restorable Archive as a manual one).

**License firewall.** `voyager_py` is reached ONLY through the silo's subprocess
driver (`DRIVER_FACTORY` → `DirectVoyagerDriver`, in-process; §2).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sidecar.modules.networker.types import NetworkerError

from ..db.base import now_utc
from ..events import make_event
from . import networker_ops
from .networker_ops import _resolve_profile, linkedin_feature_flags, linkedin_storage_path
from .operations import OperationContext, OperationOutcome

if TYPE_CHECKING:
    from pathlib import Path

# 60-day never-accepted auto-archive window (US-NW-11 / FR-NW-13).
STALE_CONTACT_DAYS = 60
# How long the headed login browser stays open waiting for the user (seconds).
LOGIN_TIMEOUT_S = 300
# How long a Fresh search's pagination snapshot stays continuable (Next page).
# LinkedIn's own pagination is stateless — the offset lives in the URL and
# nothing server-side expires — so this mirrors no LinkedIn limit. It bounds
# result-coherence drift (rankings shift between requests, so a stale offset
# points into a different list) to one job-hunting session (maintainer,
# 2026-08-01). After it lapses, only a Fresh search makes sense.
SEARCH_CURSOR_TTL = timedelta(hours=12)


# ---------------------------------------------------------------------------
# In-process login-control registry (the Cancel channel for the headed browser)
# ---------------------------------------------------------------------------


class LoginControl:
    """One in-flight login's cancel latch. The worker polls `is_cancelled()`
    while the headed browser is open; the UI's Cancel POST calls `cancel()` to
    abort — the driver then kills the child (closing the browser)."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()


class LoginControlRegistry:
    """operation_id → LoginControl for in-flight login runs (one process)."""

    def __init__(self) -> None:
        self._controls: dict[str, LoginControl] = {}
        self._lock = threading.Lock()

    def register(self, operation_id: str) -> LoginControl:
        with self._lock:
            control = LoginControl()
            self._controls[operation_id] = control
            return control

    def remove(self, operation_id: str) -> None:
        with self._lock:
            self._controls.pop(operation_id, None)

    def cancel(self, operation_id: str) -> bool:
        """Signal a waiting login to abort. False when no run is in flight."""
        with self._lock:
            control = self._controls.get(operation_id)
        if control is None:
            return False
        control.cancel()
        return True

    def cancel_all(self) -> int:
        """Cancel every in-flight login (login is single-flight, so ≤ 1). Returns
        the count cancelled — lets Disconnect abort a connecting session without
        the UI tracking the operation id."""
        with self._lock:
            controls = list(self._controls.values())
        for control in controls:
            control.cancel()
        return len(controls)


LOGIN_CONTROL = LoginControlRegistry()


# ---------------------------------------------------------------------------
# linkedin_login
# ---------------------------------------------------------------------------


def _publish_linkedin(ctx: OperationContext, state: str, **extra: Any) -> None:
    if ctx.publish is not None:
        ctx.publish(make_event(
            "linkedin", {"id": ctx.operation_id, "state": state, **extra}
        ))


def login_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Capture a LinkedIn session via the headed-login flow (US-SET-06)."""
    if ctx.db is None:
        raise RuntimeError("linkedin_login requires a database context")
    snap = ctx.input_snapshot
    login_url = snap.get("login_url")  # None in production; a fixture for the maintainer
    timeout_s = float(snap.get("timeout_s", LOGIN_TIMEOUT_S))

    # Self-gate, like `contact_sync` (posture doc §4 #8): the route gates too,
    # but this op *launches a real browser at linkedin.com*, so it re-checks its
    # own precondition rather than trusting whoever enqueued it. Both opt-ins
    # off → clean no-op, zero LinkedIn traffic.
    with ctx.db.repos() as repos:
        referral_on, search_on = linkedin_feature_flags(repos)
    if not (referral_on or search_on):
        return OperationOutcome(
            result_ref={"connected": False, "skipped": "no_linkedin_feature_enabled"}
        )

    with ctx.db.repos() as repos:
        profile = _resolve_profile(repos)
        repos.linkedin_session.update(status="connecting", paused_until=None, paused_reason="")
    _publish_linkedin(ctx, "connecting")

    control = LOGIN_CONTROL.register(ctx.operation_id or "")
    driver = networker_ops.DRIVER_FACTORY(profile)
    try:
        # The --linger window (TEMPORARY, 2026-07-08) was retired 2026-07-09:
        # the persistent Chromium profile keeps the session reusable across
        # runs, so no window needs to stay open (US-SET-06 revert).
        result = driver.login(
            login_url=login_url,
            timeout_s=timeout_s,
            cancel_check=control.is_cancelled,
        )
    except NetworkerError as exc:
        # Cancel / timeout / no-cookie — an expected domain outcome, not a crash.
        # Persist a disconnected session + surface it, then let the op record the
        # verbatim reason in the ledger (NFR-SIDE-04) by re-raising.
        with ctx.db.repos() as repos:
            repos.linkedin_session.update(status="never_set")
        _publish_linkedin(ctx, "disconnected", error=str(exc))
        raise
    finally:
        if ctx.operation_id is not None:
            LOGIN_CONTROL.remove(ctx.operation_id)
        driver.close()

    now = now_utc()
    expires = result.get("li_at_expires")
    expires_at = datetime.fromtimestamp(expires, tz=UTC) if expires else None
    connected_as = result.get("connected_as", "") or ""
    with ctx.db.repos() as repos:
        repos.linkedin_session.update(
            status="valid",
            connected_as=connected_as,
            li_at_expires_at=expires_at,
            last_validated_at=now,
            paused_until=None,
            paused_reason="",
        )
    _publish_linkedin(ctx, "connected", connected_as=connected_as)
    return OperationOutcome(
        result_ref={
            "connected": True, "connected_as": connected_as,
            "li_at_expires": expires, "cookie_count": result.get("cookie_count"),
        },
    )


# ---------------------------------------------------------------------------
# archive_stale_contacts (US-NW-11 / FR-NW-13 scheduler tick)
# ---------------------------------------------------------------------------


def linkedin_search_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """One-shot logged-in LinkedIn job search (discovery-expansion #6).

    A **user-clicked** operation — never a scheduled scan source (a scheduled
    scan must never touch a logged-in session; that isolation is the whole
    point of the guest adapter). Gated on the networking toggle + a valid
    session (the route checks the toggle; here we fail clearly if the session
    is not connected). Read-only against LinkedIn (no send, no caps decrement).

    Queries come from the same `build_queries()` prefs as every search source
    (role aliases × locations, bounded). Results map into the **shared
    discovery funnel** via `persist_scan` — same canonical-URL dedup (so a job
    also found by the guest adapter or Apify collapses to one row), tombstone
    suppression, and per-source diagnostics. `source_adapter="linkedin"` keeps
    it indistinguishable downstream from a guest-found LinkedIn row.

    **Fresh search / Next page (2026-08-01).** `mode: "fresh"` (default) runs
    page 0 of every query pair and snapshots them into the single-row cursor;
    `mode: "next"` resumes the SNAPSHOT — each pair from its own `next_start`,
    exhausted pairs skipped (no request wasted on a list LinkedIn already
    ended). Next page self-gates on the cursor being younger than
    `SEARCH_CURSOR_TTL` and not fully exhausted — the route pre-checks the
    same for a fast 409, but this op re-checks its own precondition rather
    than trusting whoever enqueued it. Every request is still exactly one
    page of 25 (the package clamp is untouched).
    """
    if ctx.db is None:
        raise RuntimeError("linkedin_search requires a database context")
    from datetime import UTC, datetime

    from sidecar.modules.scraper.canonical import canonicalize_url
    from sidecar.modules.scraper.quality import assess, is_structurally_broken
    from sidecar.modules.scraper.searchquery import build_queries
    from sidecar.modules.scraper.types import (
        NormalizedJob,
        ScanPrefs,
        ScanResult,
        SourceReport,
        Usage,
    )

    from .persistence import persist_scan, resolve_scan_prefs

    snap = ctx.input_snapshot
    limit = int(snap.get("limit", 50))
    mode = str(snap.get("mode", "fresh"))
    dry_run = bool(snap.get("dry_run", False))

    with ctx.db.repos() as repos:
        session = repos.linkedin_session.get_or_create()
        if session.status != "valid":
            raise NetworkerError(
                "voyager",
                "LinkedIn is not connected — connect your session in "
                "Settings → Referral Outreach before running a LinkedIn search",
            )
        profile = _resolve_profile(repos)
        prefs = resolve_scan_prefs(snap, repos=repos) or ScanPrefs(
            title_allow=[str(a) for a in (repos.preferences.get_or_create().role_aliases or [])],
            location_allow=[
                str(loc) for loc in (repos.preferences.get_or_create().locations or [])
            ],
        )
        cursor_row = repos.linkedin_search_cursor.get()
        cursor_fresh_at = cursor_row.fresh_at if cursor_row is not None else None
        cursor_entries = list(cursor_row.queries or []) if cursor_row is not None else []

    now = datetime.now(UTC)
    if mode == "next":
        if cursor_fresh_at is None or not cursor_entries:
            raise NetworkerError(
                "voyager", "No search to continue — run a Fresh search first"
            )
        if now - cursor_fresh_at > SEARCH_CURSOR_TTL:
            raise NetworkerError(
                "voyager",
                "The last Fresh search has expired — run a Fresh search first",
            )
        if all(e.get("exhausted") for e in cursor_entries):
            raise NetworkerError(
                "voyager",
                "No more results — the last search reached LinkedIn's end of "
                "results. Run a Fresh search.",
            )
        entries = cursor_entries
    else:
        queries = build_queries(prefs)
        if not queries:
            raise NetworkerError(
                "voyager",
                "LinkedIn search needs at least one role alias — set your roles in "
                "onboarding/preferences first",
            )
        entries = [
            {"keyword": q.keyword, "location": q.location,
             "next_start": 0, "exhausted": False}
            for q in queries
        ]

    _publish_linkedin(ctx, "searching")
    driver = networker_ops.DRIVER_FACTORY(profile)
    report = SourceReport(usage=Usage())
    jobs: list[NormalizedJob] = []
    try:
        for entry in entries:
            if entry.get("exhausted"):
                continue
            start = max(0, int(entry.get("next_start", 0)))
            try:
                result = driver.search_jobs(
                    str(entry["keyword"]), str(entry["location"]),
                    limit=limit, start=start, dry_run=dry_run,
                )
            except NetworkerError as exc:
                report.errors.append(f"{entry['keyword']!r}@{entry['location']!r}: {exc}")
                continue
            report.usage.internal_calls += 1
            raws = result.get("jobs", [])
            if result.get("ok") and not dry_run:
                # Advance this pair's cursor only on a real, successful page.
                # A backoff refusal (ok=False) leaves it untouched to retry.
                entry["next_start"] = int(result.get("next_start", start + 25))
                entry["exhausted"] = bool(result.get("exhausted", len(raws) < 25))
            for raw in raws:
                report.fetched += 1
                job = NormalizedJob(
                    title=str(raw.get("title", "")),
                    canonical_url=canonicalize_url(str(raw.get("url", ""))),
                    company=str(raw.get("company", "")),
                    location=str(raw.get("location", "")),
                    source_adapter="linkedin",
                )
                if is_structurally_broken(job):
                    continue
                assess(job, now=now)
                jobs.append(job)
    finally:
        driver.close()

    # Global dedup within this op (first occurrence wins), mirroring scan().
    seen: set[str] = set()
    deduped: list[NormalizedJob] = []
    for job in jobs:
        if job.canonical_url in seen:
            continue
        seen.add(job.canonical_url)
        deduped.append(job)
    report.kept = len(deduped)

    if not dry_run:
        with ctx.db.repos() as repos:
            if mode == "next":
                repos.linkedin_search_cursor.update(queries=entries)
            else:
                repos.linkedin_search_cursor.update(fresh_at=now, queries=entries)

    scan_result = ScanResult(jobs=deduped, per_source={"linkedin:search": report})
    result_ref = persist_scan(ctx.db, scan_result)
    result_ref["search_cursor"] = {
        "mode": mode,
        "exhausted": all(e.get("exhausted") for e in entries),
    }
    _publish_linkedin(ctx, "search_done", found=result_ref["scan"]["persisted"])
    return OperationOutcome(
        result_ref=result_ref,
        usage={"internal_calls": report.usage.internal_calls, "model": None},
    )


def archive_stale_contacts_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Auto-archive never-accepted connections older than 60 days (US-NW-11).

    Scope: `connection_status == 'sent'` AND `accepted_at IS NULL` AND the
    connection's `sent_at` is ≥ 60 days ago. An `Accepted` contact (incl.
    1st-degree, who start Accepted) is never touched; the archive is the same
    restorable state as a manual one (no delete, no tombstone)."""
    if ctx.db is None:
        raise RuntimeError("archive_stale_contacts requires a database context")
    now = now_utc()
    cutoff = now - timedelta(days=STALE_CONTACT_DAYS)
    archived_ids: list[str] = []
    with ctx.db.repos() as repos:
        for contact in repos.contacts.list_never_accepted_before(cutoff):
            repos.contacts.update(contact.id, archived_at=now)
            archived_ids.append(contact.id)
    if ctx.publish is not None and archived_ids:
        ctx.publish(make_event("networker", {
            "id": ctx.operation_id, "phase": "auto_archived",
            "count": len(archived_ids), "contact_ids": archived_ids,
        }))
    return OperationOutcome(
        result_ref={"archived_count": len(archived_ids), "contact_ids": archived_ids},
    )


def linkedin_entrypoints() -> dict[str, Any]:
    """The two N4 lifecycle kinds → their entrypoints (registered in operations.py)."""
    return {
        "linkedin_login": login_entrypoint,
        "linkedin_search": linkedin_search_entrypoint,
        "archive_stale_contacts": archive_stale_contacts_entrypoint,
    }


def storage_path_for_disconnect() -> Path:
    """Exposed for the disconnect route (delete the saved session file)."""
    return linkedin_storage_path()
