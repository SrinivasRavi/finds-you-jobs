"""The daily read of a job no complete board lists (audit-2026-10-14.md 8.7).

A scan already closes what a complete board stops listing, for free. A search
hit or a hand-added link has no such list, so its page is read at most once a
day, right after a scan, paced like any scan request. Blocking I/O: it runs on
the runner's thread pool inside the scan operation, never on the event loop."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from sidecar.modules.scraper.http import BROWSER_HEADERS, Fetcher, page_pause
from sidecar.modules.scraper.liveness import page_verdict

from ..db import Database
from ..db.base import now_utc
from .persistence import close_job, reopen_job, saved_job_ids

# How many pages 1 scan reads at most; the rest wait for the next scan.
MAX_READS_PER_SCAN = 60
RECHECK_AFTER = timedelta(days=1)

PageReader = Callable[[str], tuple[int | None, str, str]]


def _default_reader() -> PageReader:
    fetcher = Fetcher(timeout_s=15)
    first = [True]

    def read(url: str) -> tuple[int | None, str, str]:
        if not first[0]:
            page_pause()
        first[0] = False
        return fetcher.read_page(url, headers=BROWSER_HEADERS)

    return read


def check_due(
    db: Database | None,
    *,
    read: PageReader | None = None,
    now: datetime | None = None,
    limit: int = MAX_READS_PER_SCAN,
) -> dict[str, int]:
    """Read every due job's page once, and close or reopen it on what it says."""
    report = {"checked": 0, "closed": 0, "unknown": 0}
    if db is None:
        return report
    now = now or now_utc()
    with db.repos() as repos:
        rows = repos.jobs.list_liveness_due(now - RECHECK_AFTER, limit)
        due = [(j.id, j.canonical_url) for j in rows]
    if not due:
        return report
    read = read or _default_reader()
    for job_id, url in due:
        status, final_url, html = read(url or "")
        verdict = page_verdict(status, final_url, html)
        with db.repos() as repos:
            job = repos.jobs.get(job_id)
            if job is None:
                continue
            job.liveness_checked_at = now
            if verdict == "closed" and job.closed_at is None:
                close_job(repos, job, saved=job_id in saved_job_ids(repos), now=now)
                report["closed"] += 1
            elif verdict == "open" and job.closed_at is not None:
                reopen_job(repos, job, now=now)
        report["checked"] += 1
        report["unknown"] += verdict == "unknown"
    return report
