"""Covers: the `view_page` operation — user page-views ride the op queue.

- US-NW-09 / US-REF-06 — a contact's "open in LinkedIn" click shows the profile
  on the watch-only broker surface, WITHOUT interrupting a running LinkedIn op:
  the click enqueues a `view_page` operation that waits its turn on the same
  single lane every browser-driving op uses (NFR-LI-*: one account, one lane).
- Human-shaped flow (maintainer, 2026-08-16): a view of a page the surface is
  already showing is a no-op (`skipped`), mirroring `_goto_profile`'s
  already-there check — no pointless refresh for LinkedIn to profile.

ZERO live browser traffic: `networker_ops.SURFACE_PROVIDER` is replaced with an
in-memory fake surface; the real bridge is covered by
`test_networker_surface_provider.py`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sidecar.app.db import Database
from sidecar.app.main import create_app
from sidecar.app.registry import networker_ops
from sidecar.app.registry.operations import (
    OperationContext,
    OperationOutcome,
    OperationRegistry,
)
from sidecar.app.registry.presence_gate import PresenceAbsent
from sidecar.app.registry.view_page_op import _same_page, view_page_entrypoint
from sidecar.app.runner import DEFAULT_POLICY, OperationRunner, can_start
from sidecar.app.runner.policy import BROWSER_LANE_KINDS

from .conftest import wait_for_state
from .test_runner import _Blocking

TOKEN = "test-token-view-page"  # noqa: S105 — test fixture, not a real secret
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class _FakeSurface:
    """In-memory stand-in for a broker surface: records geometry + navigations,
    serves a scripted `page_url`."""

    def __init__(self, page_url: str = "") -> None:
        self.page_url = page_url
        self.navigated: list[str] = []
        self.geometry: tuple[int, int, float] | None = None

    def set_geometry(self, width: int, height: int, dpr: float) -> None:
        self.geometry = (width, height, dpr)

    def navigate(self, url: str) -> Future[str]:
        self.navigated.append(url)
        self.page_url = url
        fut: Future[str] = Future()
        fut.set_result(url)
        return fut


@pytest.fixture
def fake_surface(monkeypatch: pytest.MonkeyPatch) -> _FakeSurface:
    surface = _FakeSurface()
    monkeypatch.setattr(networker_ops, "SURFACE_PROVIDER", lambda slug: surface)
    return surface


@pytest.fixture
def app_client(
    tmp_path: Path, fake_surface: _FakeSurface
) -> Iterator[tuple[FastAPI, TestClient]]:
    app = create_app(
        token=TOKEN, original_ppid=None, data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        # create_app's lifespan installs the REAL surface provider over the
        # broker; re-point it at the fake AFTER startup so an executed op can
        # never launch a browser from a unit test.
        networker_ops.SURFACE_PROVIDER = lambda slug: fake_surface
        yield app, client


def _enable_networking(client: TestClient) -> None:
    resp = client.post(
        "/api/settings", headers=AUTH, json={"voyager_risk_marker_on": True}
    )
    assert resp.status_code == 200


def _snapshot(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "url": "https://www.linkedin.com/in/ada-lovelace/",
        "surface": "linkedin",
        "user_initiated": True,
    }
    base.update(overrides)
    return base


def _ctx(**overrides: Any) -> OperationContext:
    return OperationContext(kind="view_page", input_snapshot=_snapshot(**overrides))


# -- pure policy: the view waits for the lane, and the lane waits for it ----


def test_view_page_defers_to_every_lane_kind_and_vice_versa() -> None:
    for kind in sorted(BROWSER_LANE_KINDS - {"view_page"}):
        assert can_start("view_page", [kind], DEFAULT_POLICY) is False, kind
        assert can_start(kind, ["view_page"], DEFAULT_POLICY) is False, kind


def test_view_page_single_flight_and_free_when_lane_is_quiet() -> None:
    assert can_start("view_page", [], DEFAULT_POLICY) is True
    assert can_start("view_page", ["view_page"], DEFAULT_POLICY) is False
    # Non-lane work never blocks a view, and a view never blocks it.
    assert can_start("view_page", ["score", "scan"], DEFAULT_POLICY) is True
    assert can_start("score", ["view_page"], DEFAULT_POLICY) is True


def test_send_discover_concurrency_is_untouched() -> None:
    # The lane mutual-exclusion is view_page-specific — the existing send ↔
    # discover admission (serialized by the surface lane, not the policy)
    # must not change under this rule.
    assert can_start("send", ["discover"], DEFAULT_POLICY) is True
    assert can_start("discover", ["send"], DEFAULT_POLICY) is True


# -- live runner: a queued view starts only after the lane op settles -------


def _stays_queued(db: Database, operation_id: str, *, window: float = 0.4) -> bool:
    deadline = time.monotonic() + window
    while time.monotonic() < deadline:
        with db.repos() as repos:
            op = repos.operations.get(operation_id)
            if op is not None and op.state != "queued":
                return False
        time.sleep(0.02)
    return True


def test_view_stays_queued_behind_a_running_send(migrated_db: Database) -> None:
    db = migrated_db
    blocking = _Blocking()

    def _view(ctx: OperationContext) -> OperationOutcome:
        return OperationOutcome(result_ref={"url": ctx.input_snapshot["url"]})

    runner = OperationRunner(
        db, registry=OperationRegistry({"send": blocking, "view_page": _view})
    )
    runner.start()
    try:
        runner.submit("send", {"contact_id": "c1", "user_initiated": True})
        assert blocking.started.acquire(timeout=3)
        view_id = runner.submit("view_page", _snapshot())
        # Held back while the send runs…
        assert _stays_queued(db, view_id)
        blocking.release.set()
        # …and dispatched once it settles.
        assert wait_for_state(db, view_id, "succeeded") == "succeeded"
    finally:
        blocking.release.set()
        runner.shutdown(drain_timeout=3)


# -- the entrypoint: navigate, skip, geometry, presence ---------------------


def test_entrypoint_navigates_and_reports(fake_surface: _FakeSurface) -> None:
    outcome = view_page_entrypoint(_ctx(contact_id="c-42"))
    assert fake_surface.navigated == ["https://www.linkedin.com/in/ada-lovelace/"]
    ref = outcome.result_ref or {}
    assert ref["url"] == "https://www.linkedin.com/in/ada-lovelace/"
    assert ref["skipped"] is False
    assert ref["contact_id"] == "c-42"


def test_entrypoint_skips_when_already_on_the_page(fake_surface: _FakeSurface) -> None:
    # Origin-agnostic on purpose: the e2e fixture serves linkedin-shaped paths
    # from a loopback origin, and LinkedIn itself appends query params on
    # redirect — the PATH is the page identity.
    fake_surface.page_url = "http://127.0.0.1:9999/in/ada-lovelace?trk=feed"
    outcome = view_page_entrypoint(_ctx())
    assert fake_surface.navigated == []
    ref = outcome.result_ref or {}
    assert ref["skipped"] is True
    assert ref["url"] == fake_surface.page_url


def test_entrypoint_navigates_a_blank_surface(fake_surface: _FakeSurface) -> None:
    fake_surface.page_url = ""
    outcome = view_page_entrypoint(_ctx())
    assert fake_surface.navigated == ["https://www.linkedin.com/in/ada-lovelace/"]
    assert (outcome.result_ref or {})["skipped"] is False


def test_entrypoint_applies_geometry_only_when_carried(
    fake_surface: _FakeSurface,
) -> None:
    view_page_entrypoint(_ctx(width=1512, height=982, dpr=2.0))
    assert fake_surface.geometry == (1512, 982, 2.0)
    fake_surface.geometry = None
    view_page_entrypoint(_ctx(url="https://www.linkedin.com/in/other/"))
    assert fake_surface.geometry is None


def test_entrypoint_requires_user_initiation(fake_surface: _FakeSurface) -> None:
    with pytest.raises(PresenceAbsent):
        view_page_entrypoint(_ctx(user_initiated=False))
    assert fake_surface.navigated == []


def test_entrypoint_without_provider_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(networker_ops, "SURFACE_PROVIDER", None)
    with pytest.raises(RuntimeError, match="browser broker unavailable"):
        view_page_entrypoint(_ctx())


def test_same_page_compares_paths_not_origins() -> None:
    assert _same_page(
        "http://127.0.0.1:9999/in/ada-lovelace/",
        "https://www.linkedin.com/in/ada-lovelace",
    )
    assert _same_page(
        "https://www.linkedin.com/in/x/?trk=nav#top", "https://www.linkedin.com/in/x"
    )
    assert not _same_page(
        "https://www.linkedin.com/in/x/", "https://www.linkedin.com/in/y/"
    )
    assert not _same_page("", "https://www.linkedin.com/in/x/")
    assert _same_page("http://127.0.0.1:9999/", "https://www.linkedin.com/")


# -- the route: POST /api/browser/view --------------------------------------


def test_view_route_enqueues_with_click_provenance(app_client) -> None:
    _app, client = app_client
    _enable_networking(client)
    resp = client.post(
        "/api/browser/view", headers=AUTH,
        json={
            "url": "https://www.linkedin.com/in/ada-lovelace/",
            "surface": "linkedin", "width": 1512, "height": 982, "dpr": 2.0,
            "contact_id": "c-42",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["kind"] == "view_page" and body["state"] == "queued"
    with _app.state.db.repos() as repos:
        op = repos.operations.get(body["id"])
        assert op is not None
        snap = op.input_snapshot or {}
        assert snap["user_initiated"] is True  # the click IS the presence
        assert snap["contact_id"] == "c-42"
        assert snap["url"] == "https://www.linkedin.com/in/ada-lovelace/"


def test_view_route_runs_through_the_real_registry(
    app_client, fake_surface: _FakeSurface
) -> None:
    _app, client = app_client
    _enable_networking(client)
    resp = client.post(
        "/api/browser/view", headers=AUTH,
        json={"url": "https://www.linkedin.com/in/grace-hopper/", "surface": "linkedin"},
    )
    assert resp.status_code == 202
    wait_for_state(_app.state.db, resp.json()["id"], "succeeded")
    assert fake_surface.navigated == ["https://www.linkedin.com/in/grace-hopper/"]


def test_view_route_rejects_non_http_urls(app_client) -> None:
    _app, client = app_client
    _enable_networking(client)
    resp = client.post(
        "/api/browser/view", headers=AUTH, json={"url": "javascript:alert(1)"}
    )
    assert resp.status_code == 422


def test_view_route_refuses_when_networking_is_off(app_client) -> None:
    _app, client = app_client
    resp = client.post(
        "/api/browser/view", headers=AUTH,
        json={"url": "https://www.linkedin.com/in/x/"},
    )
    assert resp.status_code == 403


def test_view_route_needs_no_valid_session(app_client) -> None:
    # A never_set session can still browse — the UI hides the entry points when
    # the feature is off, and validity gates the ops that ACT, not a watch.
    _app, client = app_client
    _enable_networking(client)
    resp = client.post(
        "/api/browser/view", headers=AUTH,
        json={"url": "https://www.linkedin.com/in/x/"},
    )
    assert resp.status_code == 202


def test_view_page_cannot_be_enqueued_generically(app_client) -> None:
    _app, client = app_client
    resp = client.post("/api/operations/view_page", headers=AUTH, json={})
    assert resp.status_code == 422
    assert "browser/view" in resp.json()["detail"]


# -- ledger: the row names the contact it showed ----------------------------


def test_ledger_subject_is_the_contact(app_client) -> None:
    _app, client = app_client
    with _app.state.db.repos() as repos:
        contact = repos.contacts.create(
            "https://www.linkedin.com/in/jane-doe", name="Jane Doe"
        )
        row = repos.operations.create(
            "view_page",
            {
                "url": "https://www.linkedin.com/in/jane-doe",
                "user_initiated": True, "contact_id": contact.id,
            },
        )
        repos.operations.mark_running(row.id)
        repos.operations.mark_succeeded(
            row.id,
            result_ref={"url": "https://www.linkedin.com/in/jane-doe", "skipped": False},
        )
    rows = client.get("/api/operations", headers=AUTH).json()
    mine = next(r for r in rows if r["id"] == row.id)
    assert mine["subject"]["label"] == "Jane Doe"
    assert mine["subject"]["href"] == "https://www.linkedin.com/in/jane-doe"


def test_ledger_subject_falls_back_to_the_url_path(app_client) -> None:
    # A contact-less view still names WHAT it showed — the URL's path — so the
    # queue panel never renders the vague bare kind label (2026-08-16).
    _app, client = app_client
    with _app.state.db.repos() as repos:
        row = repos.operations.create(
            "view_page",
            {"url": "https://www.linkedin.com/in/sandhya-singh/", "user_initiated": True},
        )
        home = repos.operations.create(
            "view_page",
            {"url": "https://www.linkedin.com/", "user_initiated": True},
        )
    rows = client.get("/api/operations", headers=AUTH).json()
    by_id = {r["id"]: r for r in rows}
    assert by_id[row.id]["subject"]["label"] == "/in/sandhya-singh/"
    assert by_id[row.id]["subject"]["href"] == "https://www.linkedin.com/in/sandhya-singh/"
    # A bare origin has no telling path — the whole URL is the honest label.
    assert by_id[home.id]["subject"]["label"] == "https://www.linkedin.com/"
