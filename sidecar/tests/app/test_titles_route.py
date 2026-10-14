"""10-10 report 4.3: the role field's suggestions, ranked by the user's own board."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ._support import AUTH


def test_the_route_ranks_titles_from_the_users_board_first(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    with app.state.db.repos() as repos:
        repos.jobs.create(canonical_url="https://x/1", title="Staff Backend Engineer",
                          company="Acme", source_adapter="lever")
    resp = client.get("/api/titles/suggest", params={"q": "back"}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()[0] == "Back End Engineer"
    assert client.get("/api/titles/suggest", params={"q": "b"}, headers=AUTH).json() == []
