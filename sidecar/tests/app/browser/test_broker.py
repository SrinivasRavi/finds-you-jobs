"""Covers: the browser-session broker's contract (`sidecar/app/browser`).

No real Chrome. A fake CDP session, page, context and Playwright handle go in
through the broker's `opener` seam, so the tests drive the real surface thread,
the real cross-thread marshalling and the real ack accounting — everything the
broker actually owns — with only the browser itself faked.

The three things worth breaking a build over:

- **Exactly one ack per frame.** Chrome captures the next frame only once the
  previous one is acked, so a missing ack stalls the stream forever and a double
  ack lets capture outrun the viewer.
- **Shipped frames are acked after the flush, dropped ones at once.**
- **Drop the newest**, never the queued frame the viewer is already sending.
"""

from __future__ import annotations

import asyncio
import base64
import queue
import threading
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from sidecar.app.browser import BrowserBroker, BrowserLaunchError, Frame, Viewer
from sidecar.app.browser import broker as broker_module
from sidecar.app.browser import launch as launch_module
from sidecar.app.browser.broker import SurfaceSession

FRAME_BYTES = b"\xff\xd8\xff\xe0-not-really-a-jpeg"
STOCK_KWARGS = {"headless": True, "channel": "chrome", "chromium_sandbox": True}


def _frame_params(session_id: int, *, data: bytes = FRAME_BYTES) -> dict[str, Any]:
    """A `Page.screencastFrame` payload as Chrome sends it (base64 `data`)."""
    return {
        "data": base64.b64encode(data).decode("ascii"),
        "sessionId": session_id,
        "metadata": {"deviceWidth": 1280.0, "deviceHeight": 800.0},
    }


class FakeCdp:
    def __init__(self) -> None:
        self.sends: list[tuple[str, dict[str, Any]]] = []
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {}
        self.detached = 0
        self._lock = threading.Lock()

    def on(self, event: str, handler: Callable[[dict[str, Any]], None]) -> None:
        self.handlers[event] = handler

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            self.sends.append((method, dict(params or {})))
        return {}

    def detach(self) -> None:
        self.detached += 1

    def methods(self, name: str) -> list[dict[str, Any]]:
        with self._lock:
            return [params for method, params in self.sends if method == name]

    @property
    def acked(self) -> list[int]:
        return [int(params["sessionId"]) for params in self.methods("Page.screencastFrameAck")]


class FakePage:
    """`wait_for_timeout` is where a real surface thread sits, so it is also
    where a real frame callback fires. The fake fires queued frames from there,
    on the surface thread, so the handoff under test is the real one."""

    def __init__(self, cdp: FakeCdp) -> None:
        self.cdp = cdp
        self.pending: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self.calls: list[tuple[str, Any]] = []
        # The committed main-frame URL, as Playwright's `page.url` exposes it;
        # `goto` updates it, and `navigate` reads it back to the caller.
        self.url = ""

    def wait_for_timeout(self, _ms: float) -> None:
        try:
            params = self.pending.get_nowait()
        except queue.Empty:
            time.sleep(0.001)
            return
        self.cdp.handlers["Page.screencastFrame"](params)

    def goto(self, url: str, **_kwargs: Any) -> str:
        self.calls.append(("goto", url))
        self.url = url
        return url

    def evaluate(self, expression: str) -> str:
        self.calls.append(("evaluate", expression))
        return f"evaluated:{expression}"


class FakeContext:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class FakePlaywright:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class FakeBrowser:
    """One fake launch, plus what the opener recorded about it."""

    def __init__(self) -> None:
        self.cdp = FakeCdp()
        self.page = FakePage(self.cdp)
        self.context = FakeContext()
        self.playwright = FakePlaywright()
        self.opened: list[tuple[Path, dict[str, Any]]] = []

    def opener(self, profile_dir: Path, launch_kwargs: dict[str, Any]) -> SurfaceSession:
        self.opened.append((Path(profile_dir), dict(launch_kwargs)))
        return SurfaceSession(
            playwright=self.playwright, context=self.context, page=self.page, cdp=self.cdp
        )

    def emit(self, session_id: int, *, data: bytes = FRAME_BYTES) -> None:
        """Queue a frame for the surface thread to deliver."""
        self.page.pending.put(_frame_params(session_id, data=data))


async def _until(predicate: Callable[[], bool], within: float = 3.0) -> bool:
    """Poll `predicate` while yielding to the loop, so the surface thread's
    `call_soon_threadsafe` callbacks get their turn to run."""
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


@pytest.fixture
def fake() -> FakeBrowser:
    return FakeBrowser()


@pytest.fixture
async def broker(tmp_path: Path, fake: FakeBrowser) -> AsyncIterator[BrowserBroker]:
    instance = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        launch_kwargs=dict(STOCK_KWARGS),
        opener=fake.opener,
    )
    try:
        yield instance
    finally:
        instance.shutdown()


async def test_nothing_launches_until_a_surface_is_asked_for(
    tmp_path: Path, fake: FakeBrowser
) -> None:
    broker = BrowserBroker(tmp_path / "data", asyncio.get_running_loop(), opener=fake.opener)
    assert fake.opened == []
    assert broker.live_slugs == frozenset()

    surface = broker.surface("pane-a")
    await surface.wait_ready()
    assert len(fake.opened) == 1
    # Persistent profile, one directory per slug, under the data dir.
    assert fake.opened[0][0] == tmp_path / "data" / "browser" / "pane-a" / "profile"
    # A second ask reuses the surface — one Chrome per slug, not per caller.
    assert broker.surface("pane-a") is surface
    assert len(fake.opened) == 1
    broker.shutdown()


async def test_screencast_starts_once_with_the_expected_params(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    await broker.surface("pane-a").wait_ready()
    assert fake.cdp.methods("Page.startScreencast") == [
        {
            "format": "jpeg",
            "quality": 60,
            "maxWidth": 1280,
            "maxHeight": 800,
            "everyNthFrame": 1,
        }
    ]


async def test_launch_kwargs_reach_the_launcher_unchanged(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    await broker.surface("pane-a").wait_ready()
    assert fake.opened[0][1] == STOCK_KWARGS


async def test_delivered_frame_is_acked_only_after_the_viewer_flushes(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.emit(11)
    assert await _until(lambda: not viewer.queue.empty())
    frame = viewer.queue.get_nowait()
    assert isinstance(frame, Frame)
    assert frame.jpeg == FRAME_BYTES and frame.session_id == 11
    assert (frame.width, frame.height) == (1280, 800)

    # Ordering: the frame is in the viewer's hands and still NOT acked. Acking
    # on delivery instead would let capture outrun the websocket.
    await asyncio.sleep(0.05)
    assert fake.cdp.acked == []

    surface.ack(frame.session_id)
    assert await _until(lambda: fake.cdp.acked == [11])


async def test_full_viewer_queue_drops_the_newest_and_acks_it_at_once(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.emit(21, data=b"first")
    assert await _until(viewer.queue.full)
    fake.emit(22, data=b"second")

    # The queued frame stays (it is already on its way out); the newest one goes
    # and is acked immediately, so the screencast never stalls.
    assert await _until(lambda: fake.cdp.acked == [22])
    assert viewer.queue.get_nowait().jpeg == b"first"
    await asyncio.sleep(0.05)
    assert fake.cdp.acked == [22]


async def test_frames_with_no_viewer_are_acked_and_dropped(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    await broker.surface("pane-a").wait_ready()

    fake.emit(31)
    fake.emit(32)
    assert await _until(lambda: fake.cdp.acked == [31, 32])


async def test_detaching_a_viewer_acks_what_it_never_sent(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """A socket that dies holding a frame must not leave the screencast waiting
    on an ack nobody is left to send."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.emit(41)
    assert await _until(viewer.queue.full)
    surface.set_viewer(None)
    assert await _until(lambda: fake.cdp.acked == [41])


async def test_navigate_and_evaluate_run_on_the_surface_thread(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    navigated = surface.navigate("https://example.test/page")
    evaluated = surface.evaluate("1 + 1")
    assert await asyncio.wrap_future(navigated) == "https://example.test/page"
    assert await asyncio.wrap_future(evaluated) == "evaluated:1 + 1"
    assert fake.page.calls == [("goto", "https://example.test/page"), ("evaluate", "1 + 1")]


async def test_resize_moves_the_frame_ceiling_and_clamps_it(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    await asyncio.wrap_future(surface.resize(960, 540))
    await asyncio.wrap_future(surface.resize(99999, 1))
    starts = fake.cdp.methods("Page.startScreencast")
    assert [(params["maxWidth"], params["maxHeight"]) for params in starts] == [
        (1280, 800),
        (960, 540),
        (3840, 160),
    ]


async def test_broker_never_enables_the_runtime_domain(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """Two belts. The recorded CDP traffic carries no `Runtime.*` call, and
    neither module contains one for some other path to reach."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    surface.set_viewer(Viewer())
    fake.emit(51)
    await asyncio.wrap_future(surface.navigate("https://example.test/"))
    await asyncio.wrap_future(surface.resize(800, 600))
    surface.shutdown()

    assert [method for method, _ in fake.cdp.sends if method.startswith("Runtime.")] == []

    sources = [Path(str(module.__file__)) for module in (broker_module, launch_module)]
    assert all(path.is_file() for path in sources)
    assert [
        path.name for path in sources if "Runtime.enable" in path.read_text(encoding="utf-8")
    ] == []


async def test_shutdown_tears_the_session_down_and_is_idempotent(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    surface.shutdown()
    assert fake.cdp.methods("Page.stopScreencast") == [{}]
    assert fake.cdp.detached == 1
    assert fake.context.closed == 1
    assert fake.playwright.stopped == 1

    surface.shutdown()
    assert fake.cdp.methods("Page.stopScreencast") == [{}]
    assert fake.cdp.detached == 1
    assert fake.context.closed == 1

    # A command after shutdown fails fast instead of queueing forever.
    with pytest.raises(BrowserLaunchError):
        surface.navigate("https://example.test/").result(timeout=1)


async def test_broker_shutdown_closes_every_surface(tmp_path: Path) -> None:
    fakes = {slug: FakeBrowser() for slug in ("pane-a", "pane-b")}

    def opener(profile_dir: Path, launch_kwargs: dict[str, Any]) -> SurfaceSession:
        return fakes[profile_dir.parent.name].opener(profile_dir, launch_kwargs)

    broker = BrowserBroker(tmp_path / "data", asyncio.get_running_loop(), opener=opener)
    for slug in fakes:
        await broker.surface(slug).wait_ready()
    assert broker.live_slugs == frozenset(fakes)

    broker.shutdown()
    assert broker.live_slugs == frozenset()
    assert all(f.context.closed == 1 and f.playwright.stopped == 1 for f in fakes.values())
    broker.shutdown()  # idempotent
    assert all(f.context.closed == 1 for f in fakes.values())


async def test_launch_failure_reaches_the_caller(tmp_path: Path) -> None:
    def boom(_profile_dir: Path, _launch_kwargs: dict[str, Any]) -> SurfaceSession:
        raise RuntimeError("Chromium is not installed")

    broker = BrowserBroker(tmp_path / "data", asyncio.get_running_loop(), opener=boom)
    surface = broker.surface("pane-a")
    with pytest.raises(BrowserLaunchError, match="Chromium is not installed"):
        await surface.wait_ready()
    broker.shutdown()


async def test_a_slug_can_never_escape_its_profile_directory(broker: BrowserBroker) -> None:
    for bad in ("../escape", "pane/a", "", "Pane", "a" * 65):
        with pytest.raises(ValueError, match="invalid browser surface name"):
            broker.surface(bad)
