"""The browser-session broker: one persistent headless surface per slug.

A surface is a real Chrome profile held open behind a CDP session, published to
the app as a stream of JPEG screencast frames. It is vendor-agnostic by
construction: a surface is named by a slug the caller passes at runtime, so no
site, portal, or provider is named anywhere in this package
(`docs/internal/plugin-architecture.md` section 8.1 rule 5).

**Threading.** Playwright's sync API is greenlet-bound to the thread that
created it, and none of it may ever run on the serving event loop — the shell
health-polls every 2 s with a 2 s timeout and kills the backend on the third
strike, so one blocking Playwright call on the loop can take the whole sidecar
down (CLAUDE.md, async-first). Each surface therefore owns one OS thread that
launches, drives, and tears down its own Chrome, and is the only thread that
ever touches a Playwright object. The loop hands work in through two
thread-safe queues (acks, commands) and hears back through
`loop.call_soon_threadsafe`, the same cross-thread marshalling `EventHub` uses.

**Back-pressure.** Chrome captures the next screencast frame only once the
previous one is acked, so the ack is the flow-control valve. The invariant:
every frame gets exactly one ack. A frame that ships is acked after the
websocket send flushes, so capture can never outrun the socket. A frame that is
dropped is acked at once, so the stream never stalls. On overflow we drop the
NEWEST frame, the opposite of the SSE hub's drop-oldest, because a viewer of a
live surface wants the frame it is already sending finished and then the
freshest capture after it, never a stale one held in a queue.
"""

from __future__ import annotations

import asyncio
import base64
import queue
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any, NamedTuple

from ..logging_setup import get_logger
from .launch import minimal_launch_kwargs, open_persistent_context, resolve_user_agent

# Screencast wire settings. JPEG at 60 keeps a full-page frame in the tens of
# kilobytes, which a loopback websocket ships without thinking about it.
SCREENCAST_FORMAT = "jpeg"
SCREENCAST_QUALITY = 60
DEFAULT_MAX_WIDTH = 1280
DEFAULT_MAX_HEIGHT = 800
# A viewer pane can ask for a different frame ceiling; clamp it to something a
# JPEG-per-frame stream can carry.
MIN_FRAME_EDGE = 160
MAX_FRAME_EDGE = 3840

# Each pass the surface thread parks inside Playwright for this long, which is
# what lets the sync `.on` handlers fire, and bounds command latency.
THREAD_TICK_MS = 8
# How long a caller waits for Chrome to come up before calling it a failure.
LAUNCH_TIMEOUT_SECONDS = 60.0
# How long `shutdown` waits for a surface thread to finish its teardown.
SHUTDOWN_TIMEOUT_SECONDS = 5.0
# How long the first navigation waits for the frontend's display geometry before
# it fails closed. A conforming viewer sends geometry the instant its websocket
# opens, long before it can drive a navigation, so this only ever elapses when
# geometry never arrives — and then we error rather than lay a page out at a
# generic size that would betray the surface (Our Finding on real display).
GEOMETRY_TIMEOUT_SECONDS = 10.0

# A slug names a surface AND a directory under the data dir, so it stays a
# single safe path segment.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class BrowserLaunchError(RuntimeError):
    """The surface never reached a live page: no Chrome, a locked profile, a
    launch that timed out. Raised to the caller that asked for the surface."""


class Frame(NamedTuple):
    """One decoded screencast frame. `session_id` is what the ack quotes back."""

    jpeg: bytes
    session_id: int
    width: int
    height: int


class Viewer:
    """The single consumer attached to a surface (one websocket).

    A one-slot queue on purpose: the viewer is showing live pixels, so holding a
    backlog would only mean showing older ones later.
    """

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=1)


class SurfaceSession(NamedTuple):
    """What one launch yields — the four objects the surface thread drives."""

    playwright: Any
    context: Any
    page: Any
    cdp: Any


Opener = Callable[[Path, dict[str, Any]], SurfaceSession]


def open_session(profile_dir: Path, launch_kwargs: dict[str, Any]) -> SurfaceSession:
    """Default opener. Runs on the surface thread, never on the loop.

    `new_cdp_session` is the only CDP path available here:
    `launch_persistent_context` hands back a context with no Browser object
    behind it, so there is nothing else to attach to.
    """
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    try:
        context = open_persistent_context(playwright, str(profile_dir), **launch_kwargs)
    except Exception:
        playwright.stop()
        raise
    page = context.pages[0] if context.pages else context.new_page()
    return SurfaceSession(
        playwright=playwright, context=context, page=page, cdp=context.new_cdp_session(page)
    )


class BrowserSurface:
    """One slug's persistent browser, its thread, and its frame plumbing."""

    def __init__(
        self,
        slug: str,
        profile_dir: Path,
        loop: asyncio.AbstractEventLoop | None,
        launch_kwargs: Callable[[], dict[str, Any]],
        *,
        opener: Opener = open_session,
        geometry_timeout: float = GEOMETRY_TIMEOUT_SECONDS,
    ) -> None:
        self.slug = slug
        self._profile_dir = Path(profile_dir)
        self._loop = loop
        # A callable, not a dict: building the kwargs can spend a throwaway Chrome
        # launch (de-headlessing the UA), which must run on the surface thread,
        # never the serving loop. It is invoked once, inside `_run`.
        self._launch_kwargs = launch_kwargs
        self._opener = opener
        self._geometry_timeout = geometry_timeout
        self._log = get_logger()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        # Set from the surface thread the instant its run loop exits, whether
        # cleanly or on a crash. The broker reads it (via `is_dead`) to tell a
        # corpse from a live surface and relaunch in place of one that died.
        self._dead = threading.Event()
        # Set from the surface thread, awaited on the serving loop.
        self._ready = asyncio.Event()
        # Set on the surface thread once the frontend's display geometry has been
        # applied; the first navigation waits on it, fail-closed.
        self._geometry_ready = threading.Event()
        self._error: BaseException | None = None
        self._acks: queue.SimpleQueue[int] = queue.SimpleQueue()
        self._commands: queue.SimpleQueue[
            tuple[Future[Any], Callable[[SurfaceSession], Any]]
        ] = queue.SimpleQueue()
        self._viewer: Viewer | None = None
        self._max_width = DEFAULT_MAX_WIDTH
        self._max_height = DEFAULT_MAX_HEIGHT

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the surface thread and return at once. Chrome comes up over
        there; `wait_ready` is how a caller learns whether it did."""
        if self._thread is not None or self._stopping.is_set():
            return
        self._thread = threading.Thread(
            target=self._run, name=f"fyj-surface-{self.slug}", daemon=True
        )
        self._thread.start()

    async def wait_ready(self, timeout_seconds: float = LAUNCH_TIMEOUT_SECONDS) -> None:
        """Await the launch without occupying the loop. Raises
        `BrowserLaunchError` if the surface never came up."""
        try:
            await asyncio.wait_for(self._ready.wait(), timeout_seconds)
        except TimeoutError as exc:
            raise BrowserLaunchError(
                f"browser surface {self.slug!r} did not come up within {timeout_seconds:.0f}s"
            ) from exc
        if self._error is not None:
            raise BrowserLaunchError(str(self._error)) from self._error

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        """Stop the screencast, close the profile, join the thread. Idempotent —
        the second call finds no thread and returns."""
        self._stopping.set()
        thread, self._thread = self._thread, None
        self._viewer = None
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            self._log.warning(
                "browser surface %r did not stop within %.1fs", self.slug, timeout
            )

    @property
    def is_dead(self) -> bool:
        """True once the surface thread has exited on its own — a crashed Chrome,
        a dead page — rather than through `shutdown`. The broker evicts and
        relaunches a dead surface instead of handing back a corpse whose command
        drainer and screencast are gone; an intentional shutdown (`_stopping`
        set) is not dead in this sense, so `shutdown` never triggers a relaunch.
        """
        return self._dead.is_set() and not self._stopping.is_set()

    # -- the surface thread ------------------------------------------------

    def _run(self) -> None:
        session: SurfaceSession | None = None
        try:
            # Compute the launch kwargs here, on the surface thread: it may spend a
            # throwaway Chrome launch to de-headless the UA (`resolve_user_agent`).
            session = self._opener(self._profile_dir, self._launch_kwargs())
            session.cdp.on("Page.screencastFrame", self._on_frame)
            # The continuous screencast (everyNthFrame:1) is also the surface's
            # requestAnimationFrame cadence source: Chrome services rAF while it is
            # capturing. It must never be paused while a surface is live, or the
            # page's animation loop stalls with it.
            session.cdp.send("Page.startScreencast", self._screencast_params())
        except Exception as exc:  # noqa: BLE001 — reported through `wait_ready`
            self._error = exc
            self._log.exception("browser surface %r failed to start", self.slug)
            self._signal_ready()
            if session is not None:
                self._teardown(session)
            return
        self._signal_ready()
        try:
            while not self._stopping.is_set():
                self._drain_acks(session)
                self._drain_commands(session)
                # Parking inside Playwright is what lets its sync event
                # handlers run, so the frame callbacks fire from here.
                session.page.wait_for_timeout(THREAD_TICK_MS)
        except Exception:  # noqa: BLE001 — a dead page ends the surface, not the app
            self._log.exception("browser surface %r stopped", self.slug)
        finally:
            self._teardown(session)
            # The run loop is over, cleanly or crashed. Mark it so the broker
            # can tell this surface apart from a live one and relaunch a fresh
            # Chrome for the slug instead of handing back this corpse.
            self._dead.set()

    def _screencast_params(self) -> dict[str, Any]:
        return {
            "format": SCREENCAST_FORMAT,
            "quality": SCREENCAST_QUALITY,
            "maxWidth": self._max_width,
            "maxHeight": self._max_height,
            "everyNthFrame": 1,
        }

    def _drain_acks(self, session: SurfaceSession) -> None:
        while True:
            try:
                session_id = self._acks.get_nowait()
            except queue.Empty:
                return
            try:
                session.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            except Exception:  # noqa: BLE001 — the session may already be gone
                self._log.debug("screencast ack dropped", exc_info=True)

    def _drain_commands(self, session: SurfaceSession) -> None:
        while True:
            try:
                future, action = self._commands.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(action(session))
            except Exception as exc:  # noqa: BLE001 — travels to the caller
                future.set_exception(exc)

    def _teardown(self, session: SurfaceSession | None) -> None:
        if session is None:
            return
        for label, action in (
            ("stop screencast", lambda: session.cdp.send("Page.stopScreencast", {})),
            ("detach cdp", session.cdp.detach),
            ("close context", session.context.close),
            ("stop playwright", session.playwright.stop),
        ):
            try:
                action()
            except Exception:  # noqa: BLE001 — teardown reports, never raises
                self._log.debug(
                    "browser surface %r: %s failed during teardown",
                    self.slug, label, exc_info=True,
                )

    # -- frames ------------------------------------------------------------

    def _on_frame(self, params: dict[str, Any]) -> None:
        """CDP `Page.screencastFrame`, on the surface thread. Decode here (the
        loop should never spend time on base64), then hand the bytes over."""
        try:
            jpeg = base64.b64decode(params["data"])
            session_id = int(params["sessionId"])
        except Exception:  # noqa: BLE001 — one unreadable frame is not fatal
            self._log.warning("unreadable screencast frame", exc_info=True)
            return
        metadata = params.get("metadata") or {}
        self._call_on_loop(
            self._offer,
            Frame(
                jpeg=jpeg,
                session_id=session_id,
                width=int(metadata.get("deviceWidth") or 0),
                height=int(metadata.get("deviceHeight") or 0),
            ),
        )

    def _offer(self, frame: Frame) -> None:
        """Deliver one frame to the viewer, on the serving loop. Exactly one of
        the three paths acks: no viewer, full queue, or the viewer's send."""
        viewer = self._viewer
        if viewer is None:
            self.ack(frame.session_id)
            return
        try:
            viewer.queue.put_nowait(frame)
        except asyncio.QueueFull:
            # Drop the NEWEST: the queued frame is already on its way out and
            # the next capture will be fresher than this one is.
            self.ack(frame.session_id)

    def ack(self, session_id: int) -> None:
        """Release Chrome to capture the next frame. Exactly one per delivered
        frame, whether that frame shipped or was dropped."""
        self._acks.put(session_id)

    def set_viewer(self, viewer: Viewer) -> None:
        """Attach the single viewer, on the serving loop. A new attach takes the
        surface over from whatever viewer was current; the outgoing one is
        drained and acked so a frame it never sent can't leave the screencast
        waiting forever on an ack nobody is left to send.
        """
        previous, self._viewer = self._viewer, viewer
        if previous is not None and previous is not viewer:
            self._drain_and_ack(previous)

    def detach(self, viewer: Viewer) -> None:
        """Detach `viewer`, on the serving loop, but only if it is still the
        current one. A viewer a newer attach already replaced detaches nothing
        here, so one socket closing can never mute the client that took the
        surface over. The outgoing viewer is drained and acked, the same
        fail-safe the takeover in `set_viewer` uses.
        """
        if self._viewer is not viewer:
            return
        self._viewer = None
        self._drain_and_ack(viewer)

    def _drain_and_ack(self, viewer: Viewer) -> None:
        """Ack every frame still sitting in the outgoing viewer's queue, so a
        socket that dies mid-frame never leaves the screencast stalled on an ack
        nobody is left to send."""
        while True:
            try:
                frame = viewer.queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self.ack(frame.session_id)

    # -- commands ----------------------------------------------------------

    def navigate(self, url: str) -> Future[Any]:
        """Point the surface at `url` and hand back the committed, post-redirect
        main-frame URL through the future. The URL is a runtime argument; core
        holds no destination of its own.

        Fail-closed on display geometry: the first navigation waits until the
        frontend's real screen metrics have been applied, and errors rather than
        lay the page out at a generic 1280x720 that would betray the surface. The
        wait resolves instantly once geometry is set (a latch), so only the first
        navigation ever blocks on it, and only the surface thread does — geometry
        is applied by an earlier queued command, so it is already set by the time
        this drains in order.
        """

        def _go(session: SurfaceSession) -> str:
            if not self._geometry_ready.wait(self._geometry_timeout):
                raise BrowserLaunchError(
                    f"browser surface {self.slug!r} was asked to navigate before "
                    "the frontend sent its display geometry"
                )
            session.page.goto(url, wait_until="domcontentloaded")
            return session.page.url

        return self._submit(_go)

    def set_geometry(self, width: int, height: int, dpr: float) -> Future[Any]:
        """Lay the page out at the frontend's real display, read from its own
        `window.screen` and sent over the control channel. Overrides the device
        metrics so the page believes it is that size, then releases the first
        navigation. Sent BEFORE the first navigate by a conforming viewer, so it
        drains ahead of it and the navigation never waits."""

        def _apply(session: SurfaceSession) -> None:
            session.cdp.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": width,
                    "height": height,
                    "deviceScaleFactor": dpr,
                    "screenWidth": width,
                    "screenHeight": height,
                    "mobile": False,
                },
            )
            self._geometry_ready.set()

        return self._submit(_apply)

    def resize(self, width: int, height: int) -> Future[Any]:
        """Move the screencast's frame ceiling to fit the viewer's pane.

        This resizes the CAPTURE, not the page: re-issuing `startScreencast`
        with new caps changes how large a JPEG Chrome hands back and leaves the
        page's own viewport exactly as launched. Making the page LAY OUT at the
        pane's size needs a display-geometry override, which this stock baseline
        deliberately does not do.
        """
        self._max_width = max(MIN_FRAME_EDGE, min(width, MAX_FRAME_EDGE))
        self._max_height = max(MIN_FRAME_EDGE, min(height, MAX_FRAME_EDGE))
        return self._submit(
            lambda s: s.cdp.send("Page.startScreencast", self._screencast_params())
        )

    def evaluate(self, expression: str) -> Future[Any]:
        """Evaluate `expression` in the page and return its value through the
        future. Playwright's own evaluate path, so we never hand-roll a
        `Runtime` CDP call of our own."""
        return self._submit(lambda s: s.page.evaluate(expression))

    def _submit(self, action: Callable[[SurfaceSession], Any]) -> Future[Any]:
        """Queue work for the surface thread. Returns immediately with a future
        the caller can wrap (`asyncio.wrap_future`) or ignore."""
        future: Future[Any] = Future()
        if self._stopping.is_set():
            future.set_exception(
                BrowserLaunchError(f"browser surface {self.slug!r} is shut down")
            )
            return future
        self._commands.put((future, action))
        return future

    # -- cross-thread ------------------------------------------------------

    def _call_on_loop(self, fn: Callable[..., Any], *args: Any) -> None:
        """Marshal onto the serving loop (the `EventHub` pattern). Runs inline
        when no loop is running, which happens only in unit tests."""
        loop = self._loop
        if loop is None or not loop.is_running():
            fn(*args)
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:  # noqa: S110 — loop closed mid-shutdown; nothing to deliver
            pass

    def _signal_ready(self) -> None:
        self._call_on_loop(self._ready.set)


class BrowserBroker:
    """Get-or-create browser surfaces by slug.

    Nothing launches at boot. The first `surface(slug)` call is what spends a
    Chrome process, so an install that never opens a surface never pays for one.
    """

    def __init__(
        self,
        data_dir: Path | str,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        opener: Opener = open_session,
        user_agent_resolver: Callable[[], str] = resolve_user_agent,
        geometry_timeout: float = GEOMETRY_TIMEOUT_SECONDS,
    ) -> None:
        self._root = Path(data_dir) / "browser"
        self._loop = loop
        self._opener = opener
        self._user_agent_resolver = user_agent_resolver
        self._geometry_timeout = geometry_timeout
        # The de-headlessed UA, resolved once (a throwaway launch) and reused by
        # every later surface. Guarded because two surfaces can launch at once.
        self._user_agent: str | None = None
        self._ua_lock = threading.Lock()
        self._surfaces: dict[str, BrowserSurface] = {}
        self._lock = threading.Lock()

    def _guardrailed_launch_kwargs(self) -> dict[str, Any]:
        """The launch config for a real surface. Called on a surface thread: the
        first call spends one throwaway Chrome launch to read and de-headless the
        UA, then caches it so later surfaces reuse the string for free."""
        with self._ua_lock:
            if self._user_agent is None:
                self._user_agent = self._user_agent_resolver()
            user_agent = self._user_agent
        return minimal_launch_kwargs(user_agent)

    def profile_dir(self, slug: str) -> Path:
        """Where a slug's persistent Chrome profile lives. Rejects anything that
        isn't a single safe path segment."""
        if not _SLUG_RE.match(slug):
            raise ValueError(f"invalid browser surface name: {slug!r}")
        return self._root / slug / "profile"

    def surface(self, slug: str) -> BrowserSurface:
        """The surface for `slug`, launching it on first ask. A surface whose
        thread has died (a crashed Chrome) is a corpse: it is evicted and a
        fresh one launched in its place, so a reconnect recovers without an app
        restart. The caller awaits `wait_ready()` to find out whether the launch
        succeeded."""
        profile_dir = self.profile_dir(slug)
        with self._lock:
            existing = self._surfaces.get(slug)
            if existing is not None and not existing.is_dead:
                return existing
            # No surface yet, or a dead one to replace. Its thread has already
            # torn its own Chrome down, so there is nothing to join here; drop it
            # and launch fresh. Chrome still comes up lazily, on this call.
            surface = BrowserSurface(
                slug,
                profile_dir,
                self._loop,
                self._guardrailed_launch_kwargs,
                opener=self._opener,
                geometry_timeout=self._geometry_timeout,
            )
            self._surfaces[slug] = surface
        surface.start()
        return surface

    @property
    def live_slugs(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._surfaces)

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        """Tear every surface down. Idempotent."""
        with self._lock:
            surfaces = list(self._surfaces.values())
            self._surfaces.clear()
        for surface in surfaces:
            surface.shutdown(timeout=timeout)
