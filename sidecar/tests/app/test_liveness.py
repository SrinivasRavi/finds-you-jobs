"""Audit 2026-10-14 section 8.7: a posting that closed stops costing tailoring.

A complete board's scan marks every job it no longer lists as closed, free. A
job from a search source, or added by hand with a link, gets 1 read of its page
a day. A closed job greys out and leaves on the expiry clock, unless it's saved
or added by hand: those are only marked. A posting that comes back reopens."""

from __future__ import annotations

from datetime import timedelta

from sidecar.app.db.base import now_utc
from sidecar.app.db.database import Database
from sidecar.app.db.models import Job
from sidecar.app.registry.persistence import persist_scan
from sidecar.modules.scraper.types import NormalizedJob, ScanResult

_JD = "A long enough description for the board. " * 6
BOARD = "greenhouse:acme"


def _scanned(repos, url: str, **over):  # type: ignore[no-untyped-def]
    return repos.jobs.create(canonical_url=url, title="SWE", company="Acme",
                             source_adapter="greenhouse", **over)


def _get(repos, job_id: str) -> Job:  # type: ignore[no-untyped-def]
    job = repos.jobs.get(job_id)
    assert job is not None, job_id
    return job


def _listing(*urls: str) -> ScanResult:
    return ScanResult(listed={BOARD: list(urls)})


def test_a_job_its_board_no_longer_lists_is_closed_and_greyed(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        a = _scanned(repos, "https://x/a").id
        b = _scanned(repos, "https://x/b").id
    persist_scan(migrated_db, _listing("https://x/a", "https://x/b"))
    persist_scan(migrated_db, _listing("https://x/a"))

    with migrated_db.repos() as repos:
        ja, jb = _get(repos, a), _get(repos, b)
        assert ja.closed_at is None and ja.source_board == BOARD and ja.last_seen_at
        assert jb.closed_at is not None
        assert jb.feed_state == "expired" and jb.expired_at == jb.closed_at


def test_a_board_that_was_not_read_in_full_closes_nothing(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        b = _scanned(repos, "https://x/b").id
    persist_scan(migrated_db, _listing("https://x/b"))
    persist_scan(migrated_db, ScanResult(listed={}))  # board errored or was capped
    with migrated_db.repos() as repos:
        assert _get(repos, b).closed_at is None


def test_a_saved_or_hand_added_job_is_marked_never_greyed(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        saved = _scanned(repos, "https://x/s").id
        repos.applications.create(saved, column="saved", priority="P1")
        manual = _scanned(repos, "https://x/m", origin="manual").id
    persist_scan(migrated_db, _listing("https://x/s", "https://x/m"))
    persist_scan(migrated_db, _listing("https://x/other"))
    with migrated_db.repos() as repos:
        for job_id in (saved, manual):
            job = _get(repos, job_id)
            assert job.closed_at is not None
            assert job.feed_state == "active"


def test_a_posting_that_comes_back_reopens(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        b = _scanned(repos, "https://x/b").id
    persist_scan(migrated_db, _listing("https://x/b"))
    persist_scan(migrated_db, _listing("https://x/a"))
    persist_scan(migrated_db, _listing("https://x/b"))
    with migrated_db.repos() as repos:
        job = _get(repos, b)
        assert job.closed_at is None and job.feed_state == "active"


def test_an_aged_job_that_comes_back_stays_aged(migrated_db: Database) -> None:
    """Reopening undoes the close only, never the 14-day aging."""
    now = now_utc()
    with migrated_db.repos() as repos:
        b = _scanned(repos, "https://x/b").id
        repos.jobs.set_expired(b, now=now - timedelta(days=2))
    persist_scan(migrated_db, _listing("https://x/b"))
    with migrated_db.repos() as repos:
        assert _get(repos, b).feed_state == "expired"


def test_a_new_job_from_a_complete_board_is_stamped(migrated_db: Database) -> None:
    job = NormalizedJob(title="SWE", canonical_url="https://x/n", company="Acme",
                        description=_JD, source_adapter="greenhouse")
    persist_scan(migrated_db, ScanResult(jobs=[job], listed={BOARD: ["https://x/n"]}))
    with migrated_db.repos() as repos:
        row = repos.jobs.get_by_canonical_url("https://x/n")
        assert row is not None and row.source_board == BOARD and row.last_seen_at is not None


# --- the daily read, for a job no complete board lists ----------------------

from sidecar.app.registry import liveness  # noqa: E402


class _Pages:
    """A fake page reader: url -> (status, final url, html), or an Exception."""

    def __init__(self, pages: dict) -> None:
        self.pages, self.reads = pages, []

    def __call__(self, url: str):  # noqa: ANN204
        self.reads.append(url)
        page = self.pages[url]
        if isinstance(page, Exception):
            raise page
        return page


def _search_job(repos, url: str, **over):  # type: ignore[no-untyped-def]
    return repos.jobs.create(canonical_url=url, title="SWE", company="Acme",
                             source_adapter="linkedin", **over).id


def test_the_daily_read_closes_what_the_page_says_is_closed(migrated_db: Database) -> None:
    with migrated_db.repos() as repos:
        gone = _search_job(repos, "https://x/gone")
        text = _search_job(repos, "https://x/text")
        moved = _search_job(repos, "https://x/gh")
        live = _search_job(repos, "https://x/live")
        blocked = _search_job(repos, "https://x/403")
    pages = _Pages({
        "https://x/gone": (404, "https://x/gone", ""),
        "https://x/text": (200, "https://x/text",
                           "<h1>SWE</h1><p>No longer accepting applications</p>"),
        "https://x/gh": (200, "https://job-boards.greenhouse.io/acme?error=true", "<p>Jobs</p>"),
        "https://x/live": (200, "https://x/live",
                           "<script>var s='This job is no longer available'</script>"
                           "<p>Apply now</p>"),
        "https://x/403": (403, "https://x/403", ""),
    })
    report = liveness.check_due(migrated_db, read=pages, now=now_utc())

    assert report == {"checked": 5, "closed": 3, "unknown": 1}
    with migrated_db.repos() as repos:
        for job_id in (gone, text, moved):
            assert _get(repos, job_id).closed_at is not None, job_id
        for job_id in (live, blocked):
            job = _get(repos, job_id)
            assert job.closed_at is None and job.liveness_checked_at is not None


def test_each_job_is_read_at_most_once_a_day(migrated_db: Database) -> None:
    now = now_utc()
    with migrated_db.repos() as repos:
        _search_job(repos, "https://x/a", liveness_checked_at=now - timedelta(hours=3))
        due = _search_job(repos, "https://x/b", liveness_checked_at=now - timedelta(hours=25))
        on_board = _search_job(repos, "https://x/c", source_board="greenhouse:acme")
        repos.jobs.create(title="Pasted", company="Acme", source_adapter="paste",
                          content_hash="h1", origin="manual")
        repos.jobs.update(on_board, last_seen_at=now)
        unscanned = _search_job(repos, "https://x/d", source_board="greenhouse:gone")
        repos.jobs.update(unscanned, last_seen_at=now - timedelta(days=3))
    pages = _Pages({"https://x/b": (200, "https://x/b", "<p>Apply</p>"),
                    "https://x/d": (200, "https://x/d", "<p>Apply</p>")})
    liveness.check_due(migrated_db, read=pages, now=now)
    # Read today, listed by a board scanned today, or no link at all: not read.
    # A board nobody has scanned for 3 days no longer vouches for its jobs.
    assert sorted(pages.reads) == ["https://x/b", "https://x/d"]
    with migrated_db.repos() as repos:
        assert _get(repos, due).liveness_checked_at == now


def test_a_page_that_comes_back_reopens(migrated_db: Database) -> None:
    now = now_utc()
    with migrated_db.repos() as repos:
        job = _search_job(repos, "https://x/a", closed_at=now - timedelta(days=2),
                          liveness_checked_at=now - timedelta(days=2))
    pages = _Pages({"https://x/a": (200, "https://x/a", "<p>Apply</p>")})
    liveness.check_due(migrated_db, read=pages, now=now)
    with migrated_db.repos() as repos:
        assert _get(repos, job).closed_at is None


def test_reserved_test_domains_are_never_read(migrated_db: Database) -> None:
    """RFC 2606 names (example.com, .test, .invalid, localhost) host no real
    posting, so the daily read never calls them: the e2e stack stays offline."""
    with migrated_db.repos() as repos:
        for url in ("https://example.com/e2e-job-1", "https://jobs.acme.test/1",
                    "http://localhost:8000/x", "https://www.example.org/y"):
            _search_job(repos, url)
    pages = _Pages({})
    report = liveness.check_due(migrated_db, read=pages, now=now_utc())
    assert pages.reads == []
    assert report == {"checked": 0, "closed": 0, "unknown": 0}
    # Stamped, so the next scan's 60 reads go to real postings.
    with migrated_db.repos() as repos:
        assert repos.jobs.list_liveness_due(now_utc() - liveness.RECHECK_AFTER, 60) == []
