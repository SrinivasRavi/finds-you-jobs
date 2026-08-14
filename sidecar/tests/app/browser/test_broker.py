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
    surface.detach(viewer)
    assert await _until(lambda: fake.cdp.acked == [41])


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
    so it can't mute the second — and every frame is acked exactly once across
    the swap."""
    surface = broker.surface("pane-a")
    await surface.wait_ready()

    first = Viewer()
    surface.set_viewer(first)
    fake.emit(61)
    assert await _until(first.queue.full)  # frame 61 sits in the first viewer

    # A second socket attaches on the same slug. It takes over, and the first
    # viewer's in-flight frame is acked exactly once as it is dropped.
    second = Viewer()
    surface.set_viewer(second)
    assert await _until(lambda: fake.cdp.acked == [61])
    assert first.queue.empty()

    # New frames now go to the current viewer, the second one.
    fake.emit(62)
    assert await _until(second.queue.full)
    assert first.queue.empty()

    # The first socket now closes and detaches by identity. Because the second
    # viewer took over, this stale detach clears nothing: the second stays
    # current and its in-flight frame is left untouched, never re-acked.
    surface.detach(first)
    assert second.queue.get_nowait().session_id == 62
    surface.ack(62)

    # The second viewer still receives after the stale detach.
    fake.emit(63)
    assert await _until(second.queue.full)
    assert second.queue.get_nowait().session_id == 63
    surface.ack(63)

    # Exactly once each, in order: the stale detach added no duplicate ack.
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
