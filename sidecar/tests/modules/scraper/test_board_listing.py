"""Audit 2026-10-14 section 8.7: a scan reports which boards it read in full, and
every posting each one listed, so a job the board no longer lists can be marked
closed at no extra request. A board that errored, hit a page cap, came back
empty, or is a search or an aggregator proves nothing by absence."""

from __future__ import annotations

from typing import Any

from sidecar.modules.scraper.config import PortalsConfig, SourceEntry
from sidecar.modules.scraper.http import Fetcher
from sidecar.modules.scraper.scraper import scan
from sidecar.modules.scraper.types import NormalizedJob, ScanPrefs, ScraperError, Usage

_JD = "A long enough description for the scan to keep this row on the board. " * 3


def _job(url: str, title: str = "Backend Engineer") -> NormalizedJob:
    return NormalizedJob(title=title, canonical_url=url, location="Remote", description=_JD)


class _Adapter:
    def __init__(self, kind: str, jobs: list[NormalizedJob], *, error: bool = False,
                 truncate: bool = False) -> None:
        self.ID, self.KIND = "fake", kind
        self._jobs, self._error, self._truncate = jobs, error, truncate

    def fetch(self, entry: SourceEntry, fetcher: Fetcher) -> list[NormalizedJob]:
        if self._error:
            raise ScraperError("fake", "HTTP 500")
        if self._truncate:
            fetcher.truncated = True
        return list(self._jobs)


def _scan(monkeypatch, mapping: dict[str, tuple[Any, str]], prefs: ScanPrefs | None = None):
    monkeypatch.setattr(
        "sidecar.modules.scraper.scraper.adapters.resolve",
        lambda entry: mapping.get(entry.board),
    )
    config = PortalsConfig(
        sources=[SourceEntry(board=b) for b in mapping], prefs=prefs or ScanPrefs(max_workers=1)
    )
    def factory(timeout_s: int = 20, usage: Usage | None = None) -> Fetcher:
        return Fetcher(usage=usage or Usage())

    return scan(config, fetcher_factory=factory)


def test_a_whole_board_lists_every_posting_even_ones_the_filters_drop(monkeypatch) -> None:
    board = _Adapter("ats", [_job("https://x/a1"), _job("https://x/a2", title="Chef")])
    result = _scan(monkeypatch, {"a": (board, "ats:a")},
                   ScanPrefs(max_workers=1, title_allow=["engineer"]))
    assert [j.canonical_url for j in result.jobs] == ["https://x/a1"]
    # The Chef role is still open on the board; filtering it is not closing it.
    assert result.listed == {"ats:a": ["https://x/a1", "https://x/a2"]}


def test_only_a_complete_company_board_counts(monkeypatch) -> None:
    mapping = {
        "ok": (_Adapter("ats", [_job("https://x/ok")]), "ats:ok"),
        "err": (_Adapter("ats", [], error=True), "ats:err"),
        "capped": (_Adapter("ats", [_job("https://x/c")], truncate=True), "ats:capped"),
        "empty": (_Adapter("ats", []), "ats:empty"),
        "agg": (_Adapter("board", [_job("https://x/agg")]), "board:agg"),
        "search": (_Adapter("search", [_job("https://x/s")]), "search:s"),
    }
    result = _scan(monkeypatch, mapping)
    assert set(result.listed) == {"ats:ok"}


def test_workday_marks_a_page_capped_board_truncated(monkeypatch) -> None:
    from sidecar.modules.scraper.adapters import workday

    page = {"total": 999, "jobPostings": [
        {"title": f"Role {i}", "externalPath": f"/job/x/Role_{i}", "locationsText": "Pune"}
        for i in range(workday._PAGE_SIZE)
    ]}
    monkeypatch.setattr(workday, "MAX_PAGES", 2)
    monkeypatch.setattr("sidecar.modules.scraper.http.page_pause", lambda: None)

    class F(Fetcher):
        def post_json(self, url, payload, headers=None, timeout_s=None):  # noqa: ANN001, ANN201
            return page

    f = F(usage=Usage())
    workday.fetch(SourceEntry(url="https://acme.wd1.myworkdayjobs.com/en-US/External"), f)
    assert f.truncated is True
