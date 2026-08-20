"""Covers: the browser-session broker's contract (`sidecar/app/browser`).

No real Chrome (except the two `test_real_*` cases). A fake CDP session, page,
context and Playwright handle go in through the broker's `opener` seam, so the
tests drive the real surface thread, the real cross-thread marshalling and the
real ack accounting — everything the broker actually owns — with only the
browser itself faked.

The three things worth breaking a build over:

- **Exactly one ack per frame, at capture.** Chrome captures the next frame
  only once the previous one is acked, so a missing ack stalls the stream
  forever and a double ack lets capture outrun the viewer.
- **The ack never waits for the run loop.** A driver op is one long lane
  action; an ack the run loop has to drain freezes the stream for its whole
  length (the watchability defect, fixed 2026-08-15).
- **Keep the newest**: overflow evicts the older queued frame — a viewer of a
  live surface always wants the freshest capture, and acked frames carry no
  debt.
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
from sidecar.app.browser.broker import BrowserSurface, SurfaceSession

FRAME_BYTES = b"\xff\xd8\xff\xe0-not-really-a-jpeg"
# A de-headlessed Chrome UA, standing in for the throwaway-launch resolution so
# no test spends a real Chrome. The guardrails nail this onto the launch flag.
FAKE_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)
# What the fake CDP hands the new driver primitives back: a PNG-magic-prefixed
# capture and a fixed isolated-world context id.
SHOT_BYTES = b"\x89PNG\r\n\x1a\n-fake-capture"
ISOLATED_CONTEXT_ID = 4242


def _cdp_reply(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """The replies the new driver primitives read back off CDP. Every other send
    the surface makes ignores its return, so those stay an empty dict."""
    if method == "Page.captureScreenshot":
        return {"data": base64.b64encode(SHOT_BYTES).decode("ascii")}
    if method == "Page.getFrameTree":
        return {"frameTree": {"frame": {"id": "main-frame"}}}
    if method == "Page.createIsolatedWorld":
        return {"executionContextId": ISOLATED_CONTEXT_ID}
    if method == "Runtime.evaluate":
        return {"result": {"value": f"isolated:{params.get('expression')}"}}
    return {}


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
        return _cdp_reply(method, params or {})

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
        self.saved: list[str] = []

    def close(self) -> None:
        self.closed += 1

    def storage_state(self, *, path: str) -> dict[str, Any]:
        """The persistent context's explicit-flush hook `persist_profile` calls;
        records where a snapshot was sealed."""
        self.saved.append(path)
        return {}


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


class CrashingPage(FakePage):
    """A page whose run-loop park raises, standing in for a Chrome that died
    under a live surface: the surface thread's loop exits and the surface has to
    be marked dead. Startup still succeeds, so `wait_ready` returns — the whole
    point of the zombie the broker has to evict."""

    def wait_for_timeout(self, _ms: float) -> None:
        raise RuntimeError("Chrome crashed under a live surface")


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
        opener=fake.opener,
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=0.3,
    )
    try:
        yield instance
    finally:
        instance.shutdown()


async def test_nothing_launches_until_a_surface_is_asked_for(
    tmp_path: Path, fake: FakeBrowser
) -> None:
    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        opener=fake.opener,
        user_agent_resolver=lambda: FAKE_UA,
    )
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


async def test_the_launcher_gets_the_guardrailed_kwargs(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    await broker.surface("pane-a").wait_ready()
    kwargs = fake.opened[0][1]
    assert kwargs["headless"] is True
    assert kwargs["channel"] == "chrome"
    assert kwargs["chromium_sandbox"] is True
    # The automation switches are dropped, the de-headlessed UA is a launch flag,
    # and the blink flag that clears navigator.webdriver rides along.
    assert "--enable-automation" in kwargs["ignore_default_args"]
    assert f"--user-agent={FAKE_UA}" in kwargs["args"]
    assert "--disable-blink-features=AutomationControlled" in kwargs["args"]


async def test_frames_are_acked_at_capture_and_delivered(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """The ack goes out the moment the frame event runs on the surface thread —
    never deferred to a queue the run loop drains, which is what froze the
    stream for the length of every lane action (module docstring)."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.emit(11)
    assert await _until(lambda: fake.cdp.acked == [11])
    assert await _until(lambda: not viewer.queue.empty())
    frame = viewer.queue.get_nowait()
    assert isinstance(frame, Frame)
    assert frame.jpeg == FRAME_BYTES and frame.session_id == 11
    assert (frame.width, frame.height) == (1280, 800)


async def test_full_viewer_queue_keeps_the_newest_frame(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """Overflow evicts the OLDER queued frame: frames carry no ack debt now, and
    a viewer of a live surface always wants the freshest capture."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.emit(21, data=b"first")
    assert await _until(viewer.queue.full)
    fake.emit(22, data=b"second")

    assert await _until(lambda: fake.cdp.acked == [21, 22])
    assert await _until(viewer.queue.full)
    assert viewer.queue.get_nowait().jpeg == b"second"


async def test_frames_with_no_viewer_are_acked_and_dropped(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    fake.emit(31)
    fake.emit(32)
    assert await _until(lambda: fake.cdp.acked == [31, 32])
    # Nothing was held back for a later viewer: an attach starts clean.
    viewer = Viewer()
    surface.set_viewer(viewer)
    await asyncio.sleep(0.05)
    assert viewer.queue.empty()


async def test_detach_stops_delivery_without_stalling_capture(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """A socket that dies mid-frame costs nothing: its frame was acked at
    capture, so Chrome keeps capturing and only delivery stops."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.emit(41)
    assert await _until(viewer.queue.full)
    surface.detach(viewer)
    fake.emit(42)
    assert await _until(lambda: fake.cdp.acked == [41, 42])
    # The detached viewer got nothing new; 41 (already delivered) is all it has.
    assert viewer.queue.get_nowait().session_id == 41
    assert viewer.queue.empty()


async def test_frames_keep_flowing_while_a_lane_action_holds_the_surface_thread(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """THE watchability invariant (maintainer priority, 2026-08-15): a driver op
    is ONE long lane action — a referral send navigates and types for tens of
    seconds without returning to the run loop — and the frames Chrome captures
    mid-action must be acked mid-action, or the live view freezes on a stale
    frame for the whole op. The frame handler fires from inside the action's own
    page calls (Playwright dispatches events there), so the ack must complete
    before the action ends, never wait for the run loop."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    acked_mid_action: list[int] = []

    def long_action(session: SurfaceSession) -> str:
        # Chrome hands two frames to the handler while the action still runs,
        # exactly as a real send's typing repaints the page.
        session.cdp.handlers["Page.screencastFrame"](_frame_params(81, data=b"early"))
        session.cdp.handlers["Page.screencastFrame"](_frame_params(82, data=b"late"))
        acked_mid_action.extend(fake.cdp.acked)
        return "done"

    assert await asyncio.wrap_future(surface.run_on_lane(long_action)) == "done"
    # Both frames were acked BEFORE the action returned — capture never stalled.
    assert acked_mid_action == [81, 82]
    # The offers were queued to the loop ahead of the action's own completion
    # wake-up, so by now the slot deterministically holds the freshest frame.
    assert viewer.queue.get_nowait().session_id == 82
    assert viewer.queue.empty()


async def test_navigate_and_evaluate_run_on_the_surface_thread(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    # Geometry first: it releases the navigation, which fails closed without it.
    await asyncio.wrap_future(surface.set_geometry(1280, 800, 2.0))
    navigated = surface.navigate("https://example.test/page")
    evaluated = surface.evaluate("1 + 1")
    assert await asyncio.wrap_future(navigated) == "https://example.test/page"
    assert await asyncio.wrap_future(evaluated) == "evaluated:1 + 1"
    assert fake.page.calls == [("goto", "https://example.test/page"), ("evaluate", "1 + 1")]


async def test_navigation_publishes_the_committed_url_to_the_viewer(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """The viewer hears about the committed URL from the surface's own watcher,
    not only from the websocket's navigate echo."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    await asyncio.wrap_future(surface.set_geometry(1280, 800, 2.0))
    await asyncio.wrap_future(surface.navigate("https://example.test/page"))
    assert await _until(lambda: not viewer.urls.empty())
    assert viewer.urls.get_nowait() == "https://example.test/page"


async def test_spontaneous_url_change_is_published_without_a_navigate(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """A driver-driven navigation or an in-page SPA route change moves
    `page.url` with no viewer command — the watcher must still publish it."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.page.url = "https://example.test/spa-route"
    assert await _until(lambda: not viewer.urls.empty())
    assert viewer.urls.get_nowait() == "https://example.test/spa-route"

    # Only CHANGES are published — the same URL never repeats on the wire.
    await asyncio.sleep(0.05)
    assert viewer.urls.empty()


def test_offer_url_drops_the_old_for_the_newest() -> None:
    """URL overflow keeps the NEWEST (the opposite of frames): a superseded URL
    is worthless, and there is no ack to account for."""
    viewer = Viewer()
    BrowserSurface._offer_url(viewer, "https://example.test/old")  # noqa: SLF001
    BrowserSurface._offer_url(viewer, "https://example.test/new")  # noqa: SLF001
    assert viewer.urls.qsize() == 1
    assert viewer.urls.get_nowait() == "https://example.test/new"


async def test_blank_urls_are_never_published(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """`about:blank` (the fresh surface) and an empty URL are "no page yet",
    not destinations — the viewer must not see them."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    viewer = Viewer()
    surface.set_viewer(viewer)

    fake.page.url = "about:blank"
    await asyncio.sleep(0.05)
    assert viewer.urls.empty()

    fake.page.url = "https://example.test/real"
    assert await _until(lambda: not viewer.urls.empty())
    assert viewer.urls.get_nowait() == "https://example.test/real"


async def test_late_viewer_is_seeded_with_the_current_url(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """A viewer that attaches after the page is already somewhere (a reconnect,
    a tab re-mount) is told where it is at once — never "no page yet" over a
    live page."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    fake.page.url = "https://example.test/already-here"
    # No viewer yet: the URL lands in the surface's own record.
    assert await _until(lambda: surface._page_url != "")  # noqa: SLF001

    viewer = Viewer()
    surface.set_viewer(viewer)
    assert viewer.urls.get_nowait() == "https://example.test/already-here"
    # The same record is what the websocket quotes in its attach status, so a
    # client can tell "no page yet" from "page already here" without racing the
    # URL pump.
    assert surface.page_url == "https://example.test/already-here"


async def test_run_on_lane_runs_an_arbitrary_callable_on_the_surface_thread(
    broker: BrowserBroker,
) -> None:
    """The public lane hook: a driver's own callable runs on the surface thread
    and its result comes back through the future. Same machinery as `_submit`,
    so it fails fast once the surface is shut down."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    assert await asyncio.wrap_future(surface.run_on_lane(lambda s: 6 * 7)) == 42

    surface.shutdown()
    with pytest.raises(BrowserLaunchError):
        surface.run_on_lane(lambda s: 1).result(timeout=1)


async def test_screenshot_returns_decoded_image_bytes(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    shot = await asyncio.wrap_future(surface.screenshot())
    assert shot == SHOT_BYTES
    assert fake.cdp.methods("Page.captureScreenshot") == [{}]


async def test_persist_profile_seals_a_storage_state_snapshot(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    await asyncio.wrap_future(surface.persist_profile())
    assert fake.context.saved == [str(surface.storage_state_path)]
    assert surface.storage_state_path.parent.is_dir()


async def test_visibility_reads_state_through_the_main_world_evaluate(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    result = await asyncio.wrap_future(surface.visibility())
    method, expression = fake.page.calls[-1]
    assert method == "evaluate"
    assert "document.visibilityState" in expression
    assert "document.hasFocus()" in expression
    assert result == f"evaluated:{expression}"


async def test_evaluate_isolated_uses_a_fresh_world_and_enables_no_runtime(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """The proven three-send sequence, in order: frame tree, a fresh isolated
    world under a fixed non-vendor name, then an evaluate pinned to that world's
    context id — and never a `Runtime` enable."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    assert await asyncio.wrap_future(surface.evaluate_isolated("1 + 1")) == "isolated:1 + 1"

    driver_sends = [
        method
        for method, _ in fake.cdp.sends
        if method in {"Page.getFrameTree", "Page.createIsolatedWorld", "Runtime.evaluate"}
    ]
    assert driver_sends == [
        "Page.getFrameTree",
        "Page.createIsolatedWorld",
        "Runtime.evaluate",
    ]
    created = fake.cdp.methods("Page.createIsolatedWorld")[0]
    assert created["worldName"] == "fyj_driver"
    evaluated = fake.cdp.methods("Runtime.evaluate")[0]
    assert evaluated["contextId"] == ISOLATED_CONTEXT_ID
    assert evaluated["returnByValue"] is True
    assert "Runtime.enable" not in {method for method, _ in fake.cdp.sends}


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


async def test_set_geometry_overrides_the_device_metrics(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    await asyncio.wrap_future(surface.set_geometry(1512, 982, 2.0))
    assert fake.cdp.methods("Emulation.setDeviceMetricsOverride") == [
        {
            "width": 1512,
            "height": 982,
            "deviceScaleFactor": 2.0,
            "screenWidth": 1512,
            "screenHeight": 982,
            "mobile": False,
        }
    ]


async def test_the_first_navigation_fails_closed_without_geometry(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """No geometry, no navigation: the page must never lay out at a generic size.
    The fixture's short `geometry_timeout` is what bounds this wait."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    with pytest.raises(BrowserLaunchError, match="display geometry"):
        await asyncio.wrap_future(surface.navigate("https://example.test/"))
    assert fake.page.calls == []  # goto never ran


async def test_the_user_agent_is_resolved_once_and_shared(tmp_path: Path) -> None:
    """One throwaway launch per process: the first surface resolves the UA, every
    later surface reuses the cached string on its own launch flag."""
    calls = 0

    def resolver() -> str:
        nonlocal calls
        calls += 1
        return FAKE_UA

    fakes = {slug: FakeBrowser() for slug in ("pane-a", "pane-b")}

    def opener(profile_dir: Path, launch_kwargs: dict[str, Any]) -> SurfaceSession:
        return fakes[profile_dir.parent.name].opener(profile_dir, launch_kwargs)

    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        opener=opener,
        user_agent_resolver=resolver,
    )
    for slug in fakes:
        await broker.surface(slug).wait_ready()

    assert calls == 1
    for f in fakes.values():
        assert f"--user-agent={FAKE_UA}" in f.opened[0][1]["args"]
    broker.shutdown()


async def test_broker_never_enables_the_runtime_domain(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """Two belts. The recorded CDP traffic carries no `Runtime.*` call, and
    neither module contains one for some other path to reach."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()
    surface.set_viewer(Viewer())
    fake.emit(51)
    await asyncio.wrap_future(surface.set_geometry(1280, 800, 2.0))
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

    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        opener=opener,
        user_agent_resolver=lambda: FAKE_UA,
    )
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

    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        opener=boom,
        user_agent_resolver=lambda: FAKE_UA,
    )
    surface = broker.surface("pane-a")
    with pytest.raises(BrowserLaunchError, match="Chromium is not installed"):
        await surface.wait_ready()
    broker.shutdown()


async def test_a_slug_can_never_escape_its_profile_directory(broker: BrowserBroker) -> None:
    for bad in ("../escape", "pane/a", "", "Pane", "a" * 65):
        with pytest.raises(ValueError, match="invalid browser surface name"):
            broker.surface(bad)


async def test_a_dead_surface_is_evicted_and_relaunched(tmp_path: Path) -> None:
    """A surface whose thread crashes is a corpse: `surface(slug)` must not hand
    it back with its ready flag still set. It evicts the dead one and launches a
    fresh Chrome in its place, so a reconnect recovers without an app restart."""
    crashed = FakeBrowser()
    crashed.page = CrashingPage(crashed.cdp)
    healthy = FakeBrowser()
    launches = iter((crashed, healthy))

    def opener(profile_dir: Path, launch_kwargs: dict[str, Any]) -> SurfaceSession:
        return next(launches).opener(profile_dir, launch_kwargs)

    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        opener=opener,
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=0.3,
    )
    try:
        dead = broker.surface("pane-a")
        # Startup succeeded, so wait_ready returns; the crash is in the run loop.
        await dead.wait_ready()
        assert await _until(lambda: dead.is_dead)

        fresh = broker.surface("pane-a")
        assert fresh is not dead  # evicted, not handed back
        await fresh.wait_ready()
        assert not fresh.is_dead

        # The relaunched surface is genuinely live: a frame flows end to end.
        viewer = Viewer()
        fresh.set_viewer(viewer)
        healthy.emit(71)
        assert await _until(lambda: not viewer.queue.empty())
        assert viewer.queue.get_nowait().session_id == 71
    finally:
        broker.shutdown()


async def test_a_stale_detach_never_mutes_the_viewer_that_took_over(
    broker: BrowserBroker, fake: FakeBrowser
) -> None:
    """Two sockets on one slug. The second attach takes the surface over; when
    the first later closes, its detach is checked by identity and does nothing,
    so it can't mute the second — and every frame is acked exactly once, at
    capture."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    first = Viewer()
    surface.set_viewer(first)
    fake.emit(61)
    assert await _until(first.queue.full)  # frame 61 sits in the first viewer

    # A second socket attaches on the same slug and takes over. The first
    # viewer keeps its already-delivered frame (no ack debt to settle).
    second = Viewer()
    surface.set_viewer(second)

    # New frames now go to the current viewer, the second one.
    fake.emit(62)
    assert await _until(second.queue.full)
    assert first.queue.get_nowait().session_id == 61

    # The first socket now closes and detaches by identity. Because the second
    # viewer took over, this stale detach clears nothing: the second stays
    # current and its in-flight frame is left untouched.
    surface.detach(first)
    assert second.queue.get_nowait().session_id == 62

    # The second viewer still receives after the stale detach.
    fake.emit(63)
    assert await _until(second.queue.full)
    assert second.queue.get_nowait().session_id == 63

    # Exactly once each, in order, at capture.
    assert await _until(lambda: fake.cdp.acked == [61, 62, 63])


async def test_real_surface_driving_primitives_over_cdp(tmp_path: Path) -> None:
    """The driving primitives against a REAL Chrome, on a `data:` page — no
    network. Proves screenshot, run_on_lane, visibility, and an isolated-world
    read that the page's own main world can't see. Skips when the browser binary
    is absent, the pattern the referral real-Chrome tests use."""
    pytest.importorskip("playwright.sync_api")
    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=5.0,
    )
    surface = broker.surface("pane-a")
    try:
        try:
            await surface.wait_ready(timeout_seconds=60)
        except BrowserLaunchError as exc:  # pragma: no cover — CI without Chrome
            pytest.skip(f"real Chrome unavailable: {exc}")

        await asyncio.wrap_future(surface.set_geometry(1280, 800, 1.0))
        await asyncio.wrap_future(
            surface.navigate("data:text/html,<html><body><h1>hi</h1></body></html>")
        )

        # run_on_lane: a caller's own callable executes on the surface thread.
        lane_result = await asyncio.wrap_future(
            surface.run_on_lane(lambda s: s.page.evaluate("() => 7 * 7"))
        )
        assert lane_result == 49

        # screenshot: real, non-empty image bytes (PNG magic).
        shot = await asyncio.wrap_future(surface.screenshot())
        assert len(shot) > 0
        assert shot[:8] == b"\x89PNG\r\n\x1a\n"

        # visibility: a live headless surface reports itself visible.
        vis = await asyncio.wrap_future(surface.visibility())
        assert vis["visibilityState"] == "visible"
        assert isinstance(vis["hasFocus"], bool)

        # evaluate_isolated computes in its own world ...
        assert await asyncio.wrap_future(surface.evaluate_isolated("1 + 1")) == 2

        # ... and is genuinely isolated: a main-world global is invisible to it.
        await asyncio.wrap_future(surface.evaluate("window.__fyj_probe = 123"))
        assert await asyncio.wrap_future(surface.evaluate("window.__fyj_probe")) == 123
        assert (
            await asyncio.wrap_future(
                surface.evaluate_isolated("typeof window.__fyj_probe")
            )
            == "undefined"
        )

        # persist_profile: an explicit storage-state snapshot lands on disk.
        await asyncio.wrap_future(surface.persist_profile())
        assert surface.storage_state_path.is_file()
    finally:
        broker.shutdown()


async def test_real_screencast_stays_live_through_a_long_lane_action(
    tmp_path: Path,
) -> None:
    """The watchability fix against REAL Chrome: one lane action holds the
    surface thread while it clicks and types into a page — the exact shape of a
    referral send — and screencast frames must keep arriving at the viewer
    THROUGHOUT. Under the old ack-after-flush design the ack queue drained only
    between lane actions, so the stream froze after a single frame for the
    length of every driver op (the maintainer watched a DM send as a still
    image, 2026-08-15). A `data:` page and local typing only; no network."""
    pytest.importorskip("playwright.sync_api")
    broker = BrowserBroker(
        tmp_path / "data",
        asyncio.get_running_loop(),
        user_agent_resolver=lambda: FAKE_UA,
        geometry_timeout=5.0,
    )
    surface = broker.surface("pane-a")
    try:
        try:
            await surface.wait_ready(timeout_seconds=60)
        except BrowserLaunchError as exc:  # pragma: no cover — CI without Chrome
            pytest.skip(f"real Chrome unavailable: {exc}")

        viewer = Viewer()
        surface.set_viewer(viewer)
        await asyncio.wrap_future(surface.set_geometry(1280, 800, 1.0))
        await asyncio.wrap_future(
            surface.navigate(
                "data:text/html,<html><body><textarea id='box' rows='8' cols='40'>"
                "</textarea></body></html>"
            )
        )

        def drive(session: Any) -> str:
            # The driver's shape: focus, then type character by character with
            # real key events and pauses, all inside ONE lane action that never
            # returns to the run loop until the whole message is in.
            session.page.locator("#box").click()
            for ch in "watch me type this, live":
                session.page.keyboard.type(ch)
                session.page.wait_for_timeout(40)
            return session.page.locator("#box").input_value()

        wrapped = asyncio.ensure_future(asyncio.wrap_future(surface.run_on_lane(drive)))
        frames_during = 0
        while not wrapped.done():
            try:
                frame = await asyncio.wait_for(viewer.queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            assert frame.jpeg[:2] == b"\xff\xd8"  # a real JPEG
            frames_during += 1
        assert await wrapped == "watch me type this, live"
        # More than one frame arrived while the action still ran: the typing was
        # genuinely watchable. The old design could deliver at most one.
        assert frames_during > 1
    finally:
        broker.shutdown()
