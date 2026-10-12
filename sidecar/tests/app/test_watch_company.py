"""Following a company's board (docs/internal/watch-company.md).

Every follow reads the board once and is never refused for what it finds; a
followed URL carries no search filters, because the scan applies the user's
live preferences."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.api import discovery as discovery_api

from ._support import AUTH


@pytest.mark.parametrize(
    ("pasted", "followed"),
    [
        # A search's filters come off; the feed parameter stays.
        (
            "https://careers.acme.com/jobs?location=Pune&team=Eng&q=python",
            "https://careers.acme.com/jobs",
        ),
        ("https://careers.acme.com/feed.rss?format=rss&utm_source=x", "https://careers.acme.com/feed.rss?format=rss"),
        ("https://careers.acme.com/jobs#open-roles", "https://careers.acme.com/jobs"),
        ("https://careers.acme.com/jobs?gh_src=abc&department=sales", "https://careers.acme.com/jobs"),
    ],
)
def test_a_followed_url_carries_no_search_filters(pasted: str, followed: str) -> None:
    assert discovery_api._strip_filters(pasted) == followed


def test_a_known_board_reduces_to_its_root_query_and_all() -> None:
    assert (
        discovery_api._board_root("https://boards.greenhouse.io/acme/jobs/1?gh_src=x")
        == "https://boards.greenhouse.io/acme"
    )


def _follow(client: TestClient, monkeypatch: pytest.MonkeyPatch, read: tuple) -> dict:
    monkeypatch.setattr(discovery_api, "_read_board", lambda *_: read)
    resp = client.post(
        "/api/discovery/watchlist",
        headers=AUTH,
        json={"url": "https://boards.greenhouse.io/followco", "company": "FollowCo"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(
    ("read", "openings", "error"),
    [
        ((14, ["A", "B", "C"], ""), 14, ""),
        ((1, ["Only One"], ""), 1, ""),
        ((0, [], ""), 0, ""),
        ((None, [], "exceeded 20971520 bytes"), None, "exceeded 20971520 bytes"),
    ],
)
def test_every_read_result_is_followed_and_reported(
    app_client: tuple[FastAPI, TestClient],
    monkeypatch: pytest.MonkeyPatch,
    read: tuple,
    openings: int | None,
    error: str,
) -> None:
    _, client = app_client
    body = _follow(client, monkeypatch, read)
    assert body["added"] is True
    assert body["openings"] == openings
    assert body["read_error"] == error
    roster = client.get("/api/discovery/watchlist", headers=AUTH).json()["entries"]
    entry = next(e for e in roster if e["url"] == "https://boards.greenhouse.io/followco")
    assert entry["openings"] == openings
    assert entry["read_error"] == error


def test_refollowing_refreshes_the_count(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, client = app_client
    _follow(client, monkeypatch, (3, [], ""))
    again = _follow(client, monkeypatch, (9, [], ""))
    assert again["added"] is False and again["openings"] == 9
    roster = client.get("/api/discovery/watchlist", headers=AUTH).json()["entries"]
    assert next(e for e in roster if "followco" in e["url"])["openings"] == 9


def test_following_from_a_job_with_no_link_leaves_the_job_alone(
    app_client: tuple[FastAPI, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit finding 5: the follow dialog on a job with no link saved the careers
    page as the job's own link. Following writes the sources list only."""
    app, client = app_client
    with app.state.db.repos() as repos:
        job = repos.jobs.create(
            canonical_url=None, content_hash="h1", title="Engineer", company="FollowCo",
            description="pasted", source_adapter="paste-text", origin="manual",
        )
    _follow(client, monkeypatch, (4, [], ""))
    with app.state.db.repos() as repos:
        assert repos.jobs.get(job.id).canonical_url is None
