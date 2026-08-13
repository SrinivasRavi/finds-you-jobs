"""Covers: the screencast websocket (`/api/browser/screencast`).

The real app, the real route, a fake broker on `app.state.browser` — so the
socket's own contract is what's under test, not the browser behind it. Auth is
the load-bearing half: `BearerAuthMiddleware` returns early for non-`http`
scopes (`auth.py`), so if this route ever stopped checking `?token=` itself, the
surface would be open to anything that can reach the loopback port.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from sidecar.app.browser import BrowserBroker, Frame, Viewer
from sidecar.app.main import create_app

TOKEN = "test-token-screencast"  # noqa: S105 — test fixture, not a real secret
FRAME = Frame(jpeg=b"\xff\xd8\xff\xe0-frame", session_id=7, width=1280, height=800)


class FakeSurface:
    """One frame, handed to whatever viewer attaches, plus a record of every
    command the socket routed through."""

    def __init__(self, slug: str, launch_error: Exception | None = None) -> None:
        self.slug = slug
        self.launch_error = launch_error
        self.viewers: list[Viewer | None] = []
        self.acks: list[int] = []
        self.navigations: list[str] = []
        self.resizes: list[tuple[int, int]] = []
        self.geometries: list[tuple[int, int, float]] = []

    async def wait_ready(self) -> None:
        if self.launch_error is not None:
            raise self.launch_error

    def set_viewer(self, viewer: Viewer | None) -> None:
        self.viewers.append(viewer)
        if viewer is not None:
            # `set_viewer` runs on the serving loop, exactly where the real
            # broker's `_offer` puts a frame, so this hand-off is faithful.
            viewer.queue.put_nowait(FRAME)

    def ack(self, session_id: int) -> None:
        self.acks.append(session_id)

    def navigate(self, url: str) -> "concurrent.futures.Future[str]":
        # The real surface hands back a future that resolves to the committed
        # URL; complete one at once so the route can await and echo it.
        self.navigations.append(url)
        future: concurrent.futures.Future[str] = concurrent.futures.Future()
        future.set_result(url)
        return future

    def resize(self, width: int, height: int) -> None:
        self.resizes.append((width, height))

    def set_geometry(self, width: int, height: int, dpr: float) -> None:
        self.geometries.append((width, height, dpr))


class FakeBroker:
    """Get-or-create fake surfaces, borrowing the real broker's name check so
    the route's rejection path is tested against the real rule."""

    def __init__(self, root: Path) -> None:
        self._names = BrowserBroker(root)
        self.surfaces: dict[str, FakeSurface] = {}
        self.launch_error: Exception | None = None

    def surface(self, slug: str) -> FakeSurface:
        self._names.profile_dir(slug)  # raises ValueError on an unusable name
        surface = self.surfaces.get(slug)
        if surface is None:
            surface = FakeSurface(slug, self.launch_error)
            self.surfaces[slug] = surface
        return surface


@pytest.fixture
def broker(tmp_path: Path) -> FakeBroker:
    return FakeBroker(tmp_path / "fake")


@pytest.fixture
def client(tmp_path: Path, broker: FakeBroker) -> Iterator[TestClient]:
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app) as client:
        # The lifespan builds a real broker (which launches nothing); swap in
        # the fake so no test can reach a browser.
        app.state.browser = broker
        yield client


def _url(token: str | None = TOKEN, **params: str) -> str:
    query = dict(params)
    if token is not None:
        query["token"] = token
    return "/api/browser/screencast?" + "&".join(f"{k}={v}" for k, v in query.items())


def _eventually(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    """The app runs on TestClient's own loop in another thread; give it a beat."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_missing_token_is_rejected_before_accept(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/browser/screencast"):
            pass  # pragma: no cover — the handshake never completes
    assert excinfo.value.code == 1008


def test_wrong_token_is_rejected(client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(_url("not-the-token")):
            pass  # pragma: no cover — the handshake never completes
    assert excinfo.value.code == 1008


def test_header_auth_does_not_open_the_socket(client: TestClient) -> None:
    """The bearer middleware never sees a websocket scope, so a header-only
    handshake has to be rejected by the route."""
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/browser/screencast", headers={"Authorization": f"Bearer {TOKEN}"}
        ):
            pass  # pragma: no cover — the handshake never completes


def test_correct_token_streams_status_then_frames(
    client: TestClient, broker: FakeBroker
) -> None:
    with client.websocket_connect(_url(surface="pane-a")) as ws:
        status = json.loads(ws.receive_text())
        assert status == {"type": "status", "state": "streaming", "surface": "pane-a"}
        assert ws.receive_bytes() == FRAME.jpeg

    surface = broker.surfaces["pane-a"]
    # Acked exactly once, and only after the send flushed.
    assert _eventually(lambda: surface.acks == [7])
    # Attached on entry, detached on exit.
    assert _eventually(lambda: [v is not None for v in surface.viewers] == [True, False])


def test_client_control_messages_reach_the_surface(
    client: TestClient, broker: FakeBroker
) -> None:
    with client.websocket_connect(_url(surface="pane-a")) as ws:
        ws.receive_text()  # status
        ws.receive_bytes()  # the one frame
        ws.send_text("this is not json {")  # ignored, never closes the socket
        ws.send_text(json.dumps({"type": "navigate", "url": "https://example.test/"}))
        ws.send_text(json.dumps({"type": "resize", "width": 640, "height": 480}))
        ws.send_text(json.dumps({"type": "nonsense"}))

        surface = broker.surfaces["pane-a"]
        assert _eventually(lambda: surface.navigations == ["https://example.test/"])
        assert _eventually(lambda: surface.resizes == [(640, 480)])
        # The committed URL comes back as a `status` frame the viewer can show.
        assert json.loads(ws.receive_text()) == {
            "type": "status",
            "url": "https://example.test/",
        }
        # Still live after the malformed and the unknown message.
        ws.send_text(json.dumps({"type": "navigate", "url": "https://second.test/"}))
        assert _eventually(lambda: len(surface.navigations) == 2)


def test_viewport_control_message_sets_the_display_geometry(
    client: TestClient, broker: FakeBroker
) -> None:
    with client.websocket_connect(_url(surface="pane-a")) as ws:
        ws.receive_text()  # status
        ws.receive_bytes()  # the one frame
        ws.send_text(
            json.dumps(
                {"type": "viewport", "width": 1512, "height": 982, "dpr": 2.0, "colorDepth": 30}
            )
        )
        surface = broker.surfaces["pane-a"]
        assert _eventually(lambda: surface.geometries == [(1512, 982, 2.0)])


def test_default_surface_when_the_client_names_none(
    client: TestClient, broker: FakeBroker
) -> None:
    with client.websocket_connect(_url()) as ws:
        status = json.loads(ws.receive_text())
    assert status["surface"] == "default"
    assert list(broker.surfaces) == ["default"]


def test_unusable_surface_name_closes_the_socket(client: TestClient) -> None:
    with client.websocket_connect(_url(surface="pane%2Fa")) as ws:
        error = json.loads(ws.receive_text())
        closed = ws.receive()
    assert error["type"] == "error" and "pane/a" in error["message"]
    assert closed == {"type": "websocket.close", "code": 1008, "reason": ""}


def test_launch_failure_reports_an_error_then_closes(
    client: TestClient, broker: FakeBroker
) -> None:
    broker.launch_error = RuntimeError("Chromium is not installed")
    with client.websocket_connect(_url(surface="pane-a")) as ws:
        error = json.loads(ws.receive_text())
        closed = ws.receive()
    assert error["type"] == "error"
    assert "Chromium is not installed" in error["message"]
    assert closed == {"type": "websocket.close", "code": 1011, "reason": ""}


async def test_viewer_queue_holds_exactly_one_frame() -> None:
    """The one-slot queue is the shape the drop-newest rule depends on."""
    viewer = Viewer()
    viewer.queue.put_nowait(FRAME)
    with pytest.raises(asyncio.QueueFull):
        viewer.queue.put_nowait(FRAME)


def test_boot_builds_a_broker_that_launched_nothing(tmp_path: Path) -> None:
    """The lifespan wires the broker but must never spend a Chrome process."""
    app = create_app(
        token=TOKEN,
        original_ppid=None,
        data_dir=tmp_path / "data",
        enable_scheduler=False,
    )
    with TestClient(app):
        assert app.state.browser.live_slugs == frozenset()
        assert not (tmp_path / "data" / "browser").exists()
