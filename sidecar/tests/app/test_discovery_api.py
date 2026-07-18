"""Covers: Settings → Discovery sources (per-source opt-out toggles).

Through the real app (TestClient → lifespan → real migration + seeded
portals): the catalog lists every shipped adapter family with entry counts,
all enabled by default; a toggle persists into
`portals_config["disabled_sources"]` and the next scan skips the family
before any fetch.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.main import create_app
from sidecar.modules.scraper import adapters

TOKEN = "test-token-discovery"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def app_client(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        yield app, client


def test_catalog_lists_every_family_enabled_by_default(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    resp = client.get("/api/discovery/sources", headers=AUTH)
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["id"] for r in rows} == set(adapters.CATALOG)
    assert all(r["enabled"] for r in rows)
    # The seeded registry resolves to real families — the counts are visible.
    by_id = {r["id"]: r for r in rows}
    assert by_id["greenhouse"]["entries"] > 0
    assert by_id["greenhouse"]["kind"] == "ats"
    assert by_id["linkedin"]["kind"] == "search"


def test_toggle_persists_and_scan_skips_family(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    off = client.post(
        "/api/discovery/sources",
        headers=AUTH,
        json={"id": "greenhouse", "enabled": False},
    )
    assert off.status_code == 200
    assert not next(r for r in off.json() if r["id"] == "greenhouse")["enabled"]

    # Persisted in portals_config — the document scan() reads.
    settings = client.get("/api/settings", headers=AUTH).json()
    assert settings["preferences"]["portals_config"]["disabled_sources"] == ["greenhouse"]

    # And the effective ScanPrefs the scan entrypoint would run with carry it.
    from sidecar.app.registry.persistence import resolve_portals, resolve_scan_prefs

    db = app.state.db
    with db.repos() as repos:
        portals = resolve_portals({}, repos)
        prefs = resolve_scan_prefs({}, repos=repos, portals=portals)
    effective = prefs if prefs is not None else portals.prefs  # type: ignore[union-attr]
    assert "greenhouse" in effective.disabled_sources

    # Re-enable round-trips clean.
    on = client.post(
        "/api/discovery/sources", headers=AUTH, json={"id": "greenhouse", "enabled": True}
    )
    assert next(r for r in on.json() if r["id"] == "greenhouse")["enabled"]
    settings = client.get("/api/settings", headers=AUTH).json()
    assert settings["preferences"]["portals_config"]["disabled_sources"] == []


def test_unknown_source_404s(app_client: tuple[FastAPI, TestClient]) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/discovery/sources", headers=AUTH, json={"id": "monster", "enabled": False}
    )
    assert resp.status_code == 404


def test_bulk_section_toggle_is_atomic(app_client: tuple[FastAPI, TestClient]) -> None:
    """The Settings section-title checkboxes flip a whole kind group in one
    POST (`ids`); an invalid id anywhere in the batch flips nothing."""
    _app, client = app_client
    ats = ["greenhouse", "lever", "ashby", "workable"]
    off = client.post(
        "/api/discovery/sources", headers=AUTH, json={"ids": ats, "enabled": False}
    )
    assert off.status_code == 200
    rows = {r["id"]: r for r in off.json()}
    assert all(not rows[i]["enabled"] for i in ats)

    # Atomic: one bad id → 404 and NO state change.
    bad = client.post(
        "/api/discovery/sources",
        headers=AUTH,
        json={"ids": ["remoteok", "monster"], "enabled": False},
    )
    assert bad.status_code == 404
    rows = {r["id"]: r for r in client.get("/api/discovery/sources", headers=AUTH).json()}
    assert rows["remoteok"]["enabled"]

    # Re-enable the section round-trips clean.
    on = client.post(
        "/api/discovery/sources", headers=AUTH, json={"ids": ats, "enabled": True}
    )
    rows = {r["id"]: r for r in on.json()}
    assert all(rows[i]["enabled"] for i in ats)

    # Neither id nor ids → 422.
    neither = client.post(
        "/api/discovery/sources", headers=AUTH, json={"enabled": False}
    )
    assert neither.status_code == 422


def test_full_key_toggle_validates_family_prefix(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    _app, client = app_client
    ok = client.post(
        "/api/discovery/sources",
        headers=AUTH,
        json={"id": "greenhouse:acme", "enabled": False},
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/discovery/sources",
        headers=AUTH,
        json={"id": "monster:acme", "enabled": False},
    )
    assert bad.status_code == 404


# ---------------------------------------------------------------------------
# BYO scraper keys (Apify / Brave)
# ---------------------------------------------------------------------------


@pytest.fixture
def sealed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    from sidecar.app.security import SESSION_KEY_ENV

    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())


def test_credentials_roundtrip_masked_and_sealed(
    sealed_env: None, app_client: tuple[FastAPI, TestClient], tmp_path: Path
) -> None:
    app, client = app_client
    rows = client.get("/api/discovery/credentials", headers=AUTH).json()
    assert {r["id"] for r in rows} == {"apify", "brave"}
    assert all(not r["has_key"] for r in rows)

    secret = "apify_api_supersecrettoken1234"  # noqa: S105 — test fixture
    saved = client.post(
        "/api/discovery/credentials", headers=AUTH, json={"id": "apify", "key": secret}
    ).json()
    apify_row = next(r for r in saved if r["id"] == "apify")
    assert apify_row["has_key"]
    assert secret not in str(saved)  # masked hint only, never the key

    # Sealed at rest — the DB row carries ciphertext, not the token.
    db = app.state.db
    with db.repos() as repos:
        row = repos.engine_settings.get_by_engine("scraper:apify")
        assert row is not None and row.key_encrypted
        assert secret.encode() not in row.key_encrypted

    # Invisible to the LLM engines list.
    settings = client.get("/api/settings", headers=AUTH).json()
    assert all(not e["engine"].startswith("scraper:") for e in settings["engines"])

    # Delete removes it; a second delete 404s.
    assert client.delete("/api/discovery/credentials/apify", headers=AUTH).status_code == 200
    assert client.delete("/api/discovery/credentials/apify", headers=AUTH).status_code == 404


def test_apify_key_save_seeds_actor_sources_and_catalog_rows(
    sealed_env: None, app_client: tuple[FastAPI, TestClient]
) -> None:
    from sidecar.modules.scraper.adapters import apify

    app, client = app_client
    client.post(
        "/api/discovery/credentials", headers=AUTH, json={"id": "apify", "key": "tok-123456"}
    )
    settings = client.get("/api/settings", headers=AUTH).json()
    sources = settings["preferences"]["portals_config"]["sources"]
    seeded = {s["actor"] for s in sources if s.get("board") == "apify"}
    assert seeded == set(apify.DEFAULT_ACTORS)

    # Catalog now carries one per-actor row each, plus the family row.
    catalog = client.get("/api/discovery/sources", headers=AUTH).json()
    actor_rows = [r for r in catalog if r["id"].startswith("apify:")]
    assert {r["id"] for r in actor_rows} == {f"apify:{a}" for a in apify.DEFAULT_ACTORS}
    assert all(r["enabled"] for r in actor_rows)

    # Family master toggle off → every actor row reads disabled.
    client.post(
        "/api/discovery/sources", headers=AUTH, json={"id": "apify", "enabled": False}
    )
    catalog = client.get("/api/discovery/sources", headers=AUTH).json()
    assert all(
        not r["enabled"] for r in catalog if r["id"].startswith("apify:") or r["id"] == "apify"
    )

    # Idempotent: saving the key again does not duplicate entries.
    client.post(
        "/api/discovery/credentials", headers=AUTH, json={"id": "apify", "key": "tok-654321"}
    )
    settings = client.get("/api/settings", headers=AUTH).json()
    sources = settings["preferences"]["portals_config"]["sources"]
    assert len([s for s in sources if s.get("board") == "apify"]) == len(apify.DEFAULT_ACTORS)


def test_scan_entrypoint_injects_credentials_in_memory_only(
    sealed_env: None, app_client: tuple[FastAPI, TestClient]
) -> None:
    """The sealed Apify token reaches ScanPrefs.credentials at scan time and
    is absent from everything durable (snapshot, result_ref)."""
    app, client = app_client
    secret = "apify_api_in_memory_only_9876"  # noqa: S105 — test fixture
    client.post(
        "/api/discovery/credentials", headers=AUTH, json={"id": "apify", "key": secret}
    )

    from sidecar.app.registry.persistence import (
        load_scraper_credentials,
        resolve_portals,
        resolve_scan_prefs,
        with_credentials,
    )

    db = app.state.db
    with db.repos() as repos:
        portals = resolve_portals({}, repos)
        prefs = resolve_scan_prefs({}, repos=repos, portals=portals)
        creds = load_scraper_credentials(repos)
        effective = with_credentials(prefs, portals, creds)
    assert effective is not None
    assert effective.credentials == {"apify": secret}

    # Durable surfaces stay secret-free: the stored snapshot of a scan op.
    resp = client.post("/api/operations/scan", headers=AUTH, json={})
    op_id = resp.json()["id"]
    op = client.get(f"/api/operations/{op_id}", headers=AUTH).json()
    assert secret not in str(op)


def test_brave_key_seeds_source_and_budget_ledger_caps(
    sealed_env: None, app_client: tuple[FastAPI, TestClient]
) -> None:
    from datetime import datetime, timedelta

    from sidecar.app.registry.persistence import (
        BRAVE_LEDGER_KEY,
        BRAVE_MONTHLY_BUDGET,
        apply_brave_budget,
        record_brave_usage,
        resolve_portals,
        resolve_scan_prefs,
    )

    app, client = app_client
    client.post(
        "/api/discovery/credentials", headers=AUTH, json={"id": "brave", "key": "BSA-abcdef"}
    )
    settings = client.get("/api/settings", headers=AUTH).json()
    sources = settings["preferences"]["portals_config"]["sources"]
    assert len([s for s in sources if s.get("board") == "brave"]) == 1
    # Idempotent on re-save.
    client.post(
        "/api/discovery/credentials", headers=AUTH, json={"id": "brave", "key": "BSA-zzz999"}
    )
    settings = client.get("/api/settings", headers=AUTH).json()
    sources = settings["preferences"]["portals_config"]["sources"]
    assert len([s for s in sources if s.get("board") == "brave"]) == 1

    db = app.state.db
    now = datetime.now()

    # Under budget: prefs untouched.
    with db.repos() as repos:
        portals = resolve_portals({}, repos)
        prefs = resolve_scan_prefs({}, repos=repos, portals=portals)
        assert apply_brave_budget(prefs, portals, repos) is prefs

    # This scan used 12 Brave calls → ledger accumulates.
    record_brave_usage(db, {"per_source": {"brave:search": {"http_calls": 12}}})
    with db.repos() as repos:
        ledger = repos.preferences.get_or_create().thresholds[BRAVE_LEDGER_KEY]
    assert ledger == {"month": now.strftime("%Y-%m"), "used": 12}

    # At budget: the source is disabled for this scan (in-memory only).
    with db.repos() as repos:
        row = repos.preferences.get_or_create()
        thresholds = dict(row.thresholds)
        thresholds[BRAVE_LEDGER_KEY] = {
            "month": now.strftime("%Y-%m"),
            "used": BRAVE_MONTHLY_BUDGET,
        }
        repos.preferences.update(thresholds=thresholds)
    with db.repos() as repos:
        portals = resolve_portals({}, repos)
        prefs = resolve_scan_prefs({}, repos=repos, portals=portals)
        capped = apply_brave_budget(prefs, portals, repos)
    assert capped is not None and "brave" in capped.disabled_sources
    # The user's own portals_config opt-outs were not touched.
    settings = client.get("/api/settings", headers=AUTH).json()
    assert "brave" not in settings["preferences"]["portals_config"].get(
        "disabled_sources", []
    )

    # Month rollover: a stale ledger reads as 0 used → source active again.
    last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    with db.repos() as repos:
        row = repos.preferences.get_or_create()
        thresholds = dict(row.thresholds)
        thresholds[BRAVE_LEDGER_KEY] = {"month": last_month, "used": BRAVE_MONTHLY_BUDGET}
        repos.preferences.update(thresholds=thresholds)
    with db.repos() as repos:
        portals = resolve_portals({}, repos)
        prefs = resolve_scan_prefs({}, repos=repos, portals=portals)
        assert apply_brave_budget(prefs, portals, repos) is prefs


def test_discovery_analytics_aggregates_jobs_and_scan_reports(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    db = app.state.db
    with db.repos() as repos:
        j1 = repos.jobs.create(
            canonical_url="https://boards.greenhouse.io/acme/jobs/1",
            title="Backend Engineer", company="Acme", location="Remote",
            description="", source_adapter="greenhouse",
        )
        repos.jobs.create(
            canonical_url="https://www.linkedin.com/jobs/view/42",
            title="Platform Engineer", company="Beta", location="Remote",
            description="", source_adapter="linkedin",
        )
        repos.operations.create("scan", {})
    with db.repos() as repos:
        ops = repos.operations.list_by_kind_states("scan", {"queued"})
        repos.operations.mark_running(ops[0].id)
        repos.operations.mark_succeeded(
            ops[0].id,
            result_ref={
                "per_source": {
                    "greenhouse:acme": {
                        "fetched": 10, "kept": 1, "http_calls": 1,
                        "latency_ms": 120, "errors": [],
                    },
                    "linkedin:linkedin": {
                        "fetched": 25, "kept": 1, "http_calls": 3,
                        "latency_ms": 900, "errors": ["429 slow down"],
                    },
                }
            },
        )
        repos.applications.create(j1.id)

    data = client.get("/api/discovery/analytics", headers=AUTH).json()
    rows = {r["id"]: r for r in data["sources"]}
    gh, li = rows["greenhouse"], rows["linkedin"]
    assert (gh["jobs"], gh["saved"], gh["fetched"], gh["kept"]) == (1, 1, 10, 1)
    assert (li["jobs"], li["saved"], li["errors"]) == (1, 0, 1)
    assert gh["label"] == "Greenhouse" and gh["kind"] == "ats"
    assert data["scans"] == 1


def test_discovery_analytics_shows_real_boards_behind_apify_actors(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    """Maintainer directive 2026-07-18 (#6): the user sees "Naukri", never the
    "Apify" plumbing — stored rows are stamped with the real board id at parse
    time, and per-scan `apify:<actor>` reports bucket to the same identity."""
    app, client = app_client
    db = app.state.db
    with db.repos() as repos:
        repos.jobs.create(
            canonical_url="https://www.naukri.com/job-listings-backend-91011",
            title="Backend Engineer", company="Acme India", location="Bengaluru",
            description="", source_adapter="naukri",
        )
        repos.operations.create("scan", {})
    with db.repos() as repos:
        ops = repos.operations.list_by_kind_states("scan", {"queued"})
        repos.operations.mark_running(ops[0].id)
        repos.operations.mark_succeeded(
            ops[0].id,
            result_ref={
                "per_source": {
                    "apify:memo23/naukri-scraper": {
                        "fetched": 40, "kept": 12, "http_calls": 3,
                        "latency_ms": 8000, "errors": [],
                    },
                    "apify:epicscrapers/seek-job-scraper": {
                        "fetched": 5, "kept": 0, "http_calls": 1,
                        "latency_ms": 2000, "errors": [],
                    },
                }
            },
        )

    data = client.get("/api/discovery/analytics", headers=AUTH).json()
    rows = {r["id"]: r for r in data["sources"]}
    assert "apify" not in rows  # the plumbing never surfaces as a source
    naukri = rows["naukri"]
    assert naukri["label"] == "Naukri (via Apify)"
    assert (naukri["jobs"], naukri["fetched"], naukri["kept"]) == (1, 40, 12)
    assert rows["seek"]["label"] == "Seek (via Apify)"
    assert rows["seek"]["fetched"] == 5


def test_watch_company_from_url_and_job_row(
    app_client: tuple[FastAPI, TestClient],
) -> None:
    app, client = app_client
    # Paste a Greenhouse JOB url → watch the tenant's whole board.
    r = client.post(
        "/api/discovery/watchlist",
        headers=AUTH,
        json={"url": "https://boards.greenhouse.io/totally-new-co/jobs/12345", "company": "NewCo"},
    ).json()
    assert r == {
        "added": True,
        "source_url": "https://boards.greenhouse.io/totally-new-co",
        "adapter": "greenhouse",
        "company": "NewCo",
    }
    # Idempotent — watching again reports added=False, no duplicate entry.
    again = client.post(
        "/api/discovery/watchlist",
        headers=AUTH,
        json={"url": "https://boards.greenhouse.io/totally-new-co", "company": "NewCo"},
    ).json()
    assert not again["added"]
    settings = client.get("/api/settings", headers=AUTH).json()
    sources = settings["preferences"]["portals_config"]["sources"]
    assert (
        len([s for s in sources if s.get("url") == "https://boards.greenhouse.io/totally-new-co"])
        == 1
    )

    # Row-level: watch the company behind a stored job.
    db = app.state.db
    with db.repos() as repos:
        job = repos.jobs.create(
            canonical_url="https://jobs.lever.co/acme/abc-123",
            title="Backend Engineer", company="Acme", location="Remote",
            description="", source_adapter="lever",
        )
    r2 = client.post(
        "/api/discovery/watchlist", headers=AUTH, json={"job_id": job.id}
    ).json()
    assert r2["source_url"] == "https://jobs.lever.co/acme"
    assert r2["adapter"] == "lever" and r2["company"] == "Acme"

    # An unclaimable URL is refused with the honest guidance.
    bad = client.post(
        "/api/discovery/watchlist",
        headers=AUTH,
        json={"url": "https://example.com/careers"},
    )
    assert bad.status_code == 422
    assert "no adapter recognizes" in bad.json()["detail"]
