"""The browser-session broker: 1 persistent headless surface per slug.

A surface is a real Chrome profile behind a CDP session, streamed as JPEG frames
and named by a runtime slug (plugin-architecture.md section 8.1 rule 5). Each
surface owns 1 OS thread, the only one that touches its Playwright objects; the
event loop talks to it through thread-safe queues and `call_soon_threadsafe`
(async-first). Frames are acked at once inside the frame event, so capture keeps
running mid-action (acking after the socket send froze the view during sends).
Flow control is the viewer's 1-slot keep-newest queue."""

from __future__ import annotations

import asyncio
import base64
import queue
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, InvalidStateError
from pathlib import Path
from typing import Any, NamedTuple

from ..logging_setup import get_logger
from .launch import minimal_launch_kwargs, open_persistent_context, resolve_user_agent
from .process import kill_browser

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

# While a viewer watches, the surface thread parks inside Playwright for this long
# each pass, which is what lets the sync frame handlers fire.
THREAD_TICK_MS = 8
# With no viewer it sleeps on a wake event instead, and pumps Playwright's events
# this often. The 8 ms poll alone cost about 10% of a core (audit 8.5).
IDLE_PUMP_SECONDS = 1.0
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
# How long a surface may sit unwatched with no work in flight before the broker
# closes it. Each costs a thread and a real Chrome, and nothing used to reclaim
# one. Reopening relaunches lazily with the profile intact, so being wrong costs
# 1 relaunch. Generous: a watched or busy surface is never idle.
IDLE_SHUTDOWN_SECONDS = 300.0
# Every lane action has a deadline. Past it the surface's Chrome is killed, the only
# interrupt a stuck sync Playwright call honours, and the surface relaunches on its
# next use (architecture-audit-2026-10-14.md section 3.3). Callers that know their
# work runs longer (typing, paced reads) pass their own.
DEFAULT_ACTION_DEADLINE_SECONDS = 120.0

# How many Chromes may be live at once (backlog S-C20). Past it, launching a
# new surface first closes the longest-idle one; a surface in use is never
# closed, so the cap yields rather than take a pane from under the user.
MAX_SURFACES = 4

# A slug names a surface AND a directory under the data dir, so it stays a
# single safe path segment.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# A fixed, non-vendor name for the driver's private isolated world (section 8.1
# rule 5). The page's own scripts share the DOM with a named isolated world but
# not its JS globals, so a read the driver runs there stays invisible to them.
_ISOLATED_WORLD_NAME = "fyj_driver"


class BrowserLaunchError(RuntimeError):
    """The surface never reached a live page: no Chrome, a locked profile, a
    launch that timed out. Raised to the caller that asked for the surface."""


class BrowserActionTimeout(RuntimeError):
    """A lane action ran past its deadline, so the surface's Chrome was killed. The
    action's effect on the page is unknown: a write that raised this may have landed."""

    def __init__(self, slug: str, deadline_s: float) -> None:
        super().__init__(
            f"browser surface {slug!r}: an action ran past its {deadline_s:.0f}s deadline"
        )
        self.deadline_s = deadline_s


class Frame(NamedTuple):
    """One decoded screencast frame. `session_id` is Chrome's capture id (the
    ack already quoted it back, at capture, on the surface thread)."""

    jpeg: bytes
    session_id: int
    width: int
    height: int


class Viewer:
    """The single consumer of a surface (1 websocket). 1-slot queues that keep the
    newest: frames are acked at capture, so a superseded frame is worthless. `urls`
    carries every committed main-frame URL change, driver and SPA navigations too."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=1)
        self.urls: asyncio.Queue[str] = asyncio.Queue(maxsize=1)


class SurfaceSession(NamedTuple):
    """What one launch yields — the four objects the surface thread drives."""

    playwright: Any
    context: Any
    page: Any
    cdp: Any


Opener = Callable[[Path, dict[str, Any]], SurfaceSession]
Killer = Callable[[Path], int]


def open_session(profile_dir: Path, launch_kwargs: dict[str, Any]) -> SurfaceSession:
    """Default opener, on the surface thread. `new_cdp_session` is the only CDP path:
    a persistent context has no Browser object behind it."""
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
        killer: Killer = kill_browser,
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
        self._killer = killer
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
        # Idle accounting, read by the broker's reaper. `_inflight` counts work
        # submitted but not yet finished, so a single long action (a slow page
        # load, a paced profile read) holds the surface open even though nothing
        # new is being submitted meanwhile.
        self._last_activity = time.monotonic()
        self._inflight = 0
        self._activity_lock = threading.Lock()
        self._commands: queue.SimpleQueue[
            tuple[Future[Any], Callable[[SurfaceSession], Any], float]
        ] = queue.SimpleQueue()
        self._viewer: Viewer | None = None
        # Set by anything the idle surface thread must act on now: a command, a
        # viewer coming or going, a kill, a shutdown.
        self._wake = threading.Event()
        # Whether Chrome is capturing, owned by the surface thread.
        self._casting = False
        self._max_width = DEFAULT_MAX_WIDTH
        self._max_height = DEFAULT_MAX_HEIGHT
        # Committed main-frame URL tracking. `_last_url` is the surface thread's
        # change detector; `_page_url` is the serving-loop copy a late-attaching
        # viewer is seeded from, so a reconnect shows where the page already is.
        self._last_url = ""
        self._page_url = ""

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
        self._wake.set()
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
        """True once the surface thread exited on its own (a crashed Chrome, a dead
        page), not through `shutdown`. The broker relaunches a dead surface; an
        intentional shutdown is never dead in this sense."""
        return self._dead.is_set() and not self._stopping.is_set()

    @property
    def storage_state_path(self) -> Path:
        """Where `persist_profile` seals its storage-state snapshot: alongside
        the slug's persistent profile, under the surface directory the broker
        owns."""
        return self._profile_dir.parent / "storage_state.json"

    # -- the surface thread ------------------------------------------------

    def _run(self) -> None:
        session: SurfaceSession | None = None
        try:
            # Compute the launch kwargs here, on the surface thread: it may spend a
            # throwaway Chrome launch to de-headless the UA (`resolve_user_agent`).
            session = self._opener(self._profile_dir, self._launch_kwargs())
            session.cdp.on(
                "Page.screencastFrame",
                lambda params: self._on_frame(session, params),
            )
            # No screencast until a viewer attaches. rAF runs at 60/s without one
            # (measured, audit-2026-10-14.md section 8.5).
        except Exception as exc:  # noqa: BLE001 — reported through `wait_ready`
            self._error = exc
            self._log.exception("browser surface %r failed to start", self.slug)
            self._signal_ready()
            if session is not None:
                self._teardown(session)
            return
        self._signal_ready()
        try:
            # A deadline kill marks the surface dead from another thread; the loop
            # stops there rather than drive a Chrome that's gone.
            while not self._stopping.is_set() and not self._dead.is_set():
                self._wake.clear()
                self._sync_screencast(session)
                self._drain_commands(session)
                self._watch_url(session)
                if self._casting:
                    # Parking inside Playwright is what lets its sync event
                    # handlers run, so the frame callbacks fire from here.
                    session.page.wait_for_timeout(THREAD_TICK_MS)
                elif not self._wake.wait(IDLE_PUMP_SECONDS):
                    session.page.wait_for_timeout(1)  # deliver queued events
        except Exception:  # noqa: BLE001 — a dead page ends the surface, not the app
            self._log.exception("browser surface %r stopped", self.slug)
        finally:
            self._teardown(session)
            # The run loop is over, cleanly or crashed. Mark it so the broker
            # can tell this surface apart from a live one and relaunch a fresh
            # Chrome for the slug instead of handing back this corpse.
            self._dead.set()
            self._fail_pending()

    def _sync_screencast(self, session: SurfaceSession) -> None:
        """Capture exactly while a viewer is attached, on the surface thread."""
        wanted = self._viewer is not None
        if wanted and not self._casting:
            session.cdp.send("Page.startScreencast", self._screencast_params())
            self._casting = True
        elif not wanted and self._casting:
            session.cdp.send("Page.stopScreencast", {})
            self._casting = False

    def _screencast_params(self) -> dict[str, Any]:
        return {
            "format": SCREENCAST_FORMAT,
            "quality": SCREENCAST_QUALITY,
            "maxWidth": self._max_width,
            "maxHeight": self._max_height,
            "everyNthFrame": 1,
        }

    def _drain_commands(self, session: SurfaceSession) -> None:
        while not self._dead.is_set():
            try:
                future, action, deadline_s = self._commands.get_nowait()
            except queue.Empty:
                return
            if not future.set_running_or_notify_cancel():
                self._finished_one()
                continue
            timer = threading.Timer(deadline_s, self._on_deadline, args=(future, deadline_s))
            timer.daemon = True
            timer.start()
            try:
                _resolve(future, result=action(session))
            except Exception as exc:  # noqa: BLE001 — travels to the caller
                _resolve(future, error=exc)
            finally:
                timer.cancel()
                self._finished_one()

    def _on_deadline(self, future: Future[Any], deadline_s: float) -> None:
        """On a timer thread: the running action is past its deadline. Mark the surface
        dead first, so nothing new is queued on it and the broker relaunches, then kill
        its Chrome so the stuck call fails, then answer the caller."""
        self._log.error(
            "browser surface %r: lane action past its %.0fs deadline, killing Chrome",
            self.slug, deadline_s,
        )
        # Answer first: the stuck call fails the instant Chrome dies, and its own
        # error must not be what the caller sees.
        _resolve(future, error=BrowserActionTimeout(self.slug, deadline_s))
        self._dead.set()
        self._wake.set()
        self._fail_pending()
        try:
            self._killer(self._profile_dir)
        except Exception:  # noqa: BLE001, the caller already has its answer
            self._log.exception("browser surface %r: killing Chrome failed", self.slug)

    def kill_browser(self) -> None:
        """Kill this surface's Chrome from any thread. Whatever is running on the lane
        fails, queued work fails, and the next `surface(slug)` launches a fresh one."""
        self._dead.set()
        self._wake.set()
        self._fail_pending()
        self._killer(self._profile_dir)

    def _watch_url(self, session: SurfaceSession) -> None:
        """Emit the committed main-frame URL whenever it changes, once per tick on the
        surface thread. `page.url` is Playwright's locally tracked URL, so the read
        costs no CDP round-trip the page could observe. Publishes on change only."""
        url = session.page.url
        if url == self._last_url:
            return
        self._last_url = url
        # A blank page is "no page yet", not a destination worth reporting.
        if not url or url == "about:blank":
            return
        self._call_on_loop(self._publish_url, url)

    def _publish_url(self, url: str) -> None:
        """Record the current URL and offer it to the viewer, on the serving
        loop. Drop-OLD on overflow — the newest URL is the only true one."""
        self._page_url = url
        viewer = self._viewer
        if viewer is None:
            return
        self._offer_url(viewer, url)

    @staticmethod
    def _offer_url(viewer: Viewer, url: str) -> None:
        try:
            viewer.urls.put_nowait(url)
        except asyncio.QueueFull:
            try:
                viewer.urls.get_nowait()
            except asyncio.QueueEmpty:
                pass
            viewer.urls.put_nowait(url)

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

    def _on_frame(self, session: SurfaceSession, params: dict[str, Any]) -> None:
        """CDP `Page.screencastFrame` on the surface thread: decode here, ack at once so
        Chrome keeps capturing (the liveness fix, module docstring), then hand the bytes
        over. The nested `cdp.send` from a handler is a documented shape."""
        try:
            jpeg = base64.b64decode(params["data"])
            session_id = int(params["sessionId"])
        except Exception:  # noqa: BLE001 — one unreadable frame is not fatal
            self._log.warning("unreadable screencast frame", exc_info=True)
            return
        try:
            session.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
        except Exception:  # noqa: BLE001 — the session may already be gone
            self._log.debug("screencast ack dropped", exc_info=True)
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
        """Deliver one frame to the viewer, on the serving loop. No viewer →
        dropped. Full slot → the OLDER queued frame is evicted for this one
        (keep-newest): the frame is already acked, so nothing is owed on it, and
        the viewer of a live surface always wants the freshest capture."""
        viewer = self._viewer
        if viewer is None:
            return
        try:
            viewer.queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                viewer.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            viewer.queue.put_nowait(frame)

    def set_viewer(self, viewer: Viewer) -> None:
        """Attach the single viewer, on the serving loop. A new attach takes the
        surface over from whatever viewer was current; the outgoing one simply
        stops receiving (its queued frame is already acked, so nothing stalls).
        """
        self._viewer = viewer
        self._wake.set()
        # Seed the incoming viewer with where the page already is, so a
        # reconnect (or a tab re-mount) never reads "no page yet" over a live,
        # fully loaded page.
        if self._page_url:
            self._offer_url(viewer, self._page_url)

    def detach(self, viewer: Viewer) -> None:
        """Detach `viewer`, on the serving loop, but only if it is still the
        current one. A viewer a newer attach already replaced detaches nothing
        here, so one socket closing can never mute the client that took the
        surface over."""
        if self._viewer is not viewer:
            return
        self._viewer = None
        self._wake.set()
        # Start the idle clock HERE, not from the last submitted action. A
        # viewer can watch a static page for an hour without submitting
        # anything, and reaping the instant they close the pane would make a
        # close-then-reopen cost a Chrome relaunch.
        with self._activity_lock:
            self._last_activity = time.monotonic()

    @property
    def has_viewer(self) -> bool:
        """Whether a screencast viewer (one websocket) is attached right now.

        The broker's own tracking of the live consumer, set by `set_viewer` and
        cleared by `detach` — a signal a page cannot fake. The presence gate
        reads it to know the user is watching this surface (Our Claim 9)."""
        return self._viewer is not None

    @property
    def page_url(self) -> str:
        """The committed main-frame URL as last published to the serving loop —
        empty while the surface still sits on its launch `about:blank`. The
        websocket quotes it in the attach status so a client knows, without
        racing the URL pump, whether the surface has a page at all (the
        auto-open-home decision reads exactly this)."""
        return self._page_url

    # -- commands ----------------------------------------------------------

    def navigate(self, url: str) -> Future[Any]:
        """Point the surface at `url`; the future returns the committed post-redirect
        URL. Fail-closed on geometry: the first navigation waits for the frontend's real
        metrics and errors rather than lay out at 1280x720. A latch, so only the first
        navigation ever waits."""

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
        """Move the screencast's frame ceiling to fit the viewer's pane. Resizes the
        capture, not the page; making the page lay out at pane size needs a geometry
        override this baseline doesn't do."""
        self._max_width = max(MIN_FRAME_EDGE, min(width, MAX_FRAME_EDGE))
        self._max_height = max(MIN_FRAME_EDGE, min(height, MAX_FRAME_EDGE))

        def _recast(session: SurfaceSession) -> None:
            if self._casting:  # else the next attach starts at the new size
                session.cdp.send("Page.startScreencast", self._screencast_params())

        return self._submit(_recast)

    def evaluate(self, expression: str) -> Future[Any]:
        """Evaluate `expression` in the page and return its value through the
        future. Playwright's own evaluate path, so we never hand-roll a
        `Runtime` CDP call of our own."""
        return self._submit(lambda s: s.page.evaluate(expression))

    def run_on_lane(
        self,
        action: Callable[[SurfaceSession], Any],
        deadline_s: float = DEFAULT_ACTION_DEADLINE_SECONDS,
    ) -> Future[Any]:
        """The public name for the surface's single serialized operation lane:
        queue `action` for the surface thread and hand back its future. This is
        the integration hook an external driver submits its own callables onto,
        so "one account, one lane" stays a structural guarantee (section 5.4).
        Past `deadline_s` the future fails with `BrowserActionTimeout`."""
        return self._submit(action, deadline_s)

    def screenshot(self) -> Future[bytes]:
        """Capture the surface as image bytes for the exception agent, over CDP
        on the surface thread. `Page.captureScreenshot` hands back base64, which
        we decode here so the serving loop never spends time on it — the same
        discipline the screencast path keeps (`_on_frame`)."""

        def _shot(session: SurfaceSession) -> bytes:
            result = session.cdp.send("Page.captureScreenshot", {})
            return base64.b64decode(result["data"])

        return self._submit(_shot)

    def persist_profile(self) -> Future[None]:
        """Seal an explicit storage-state snapshot of the live session into the surface
        directory, mid-session. The persistent context already saves cookies on close;
        this is an extra checkpoint, not a second mechanism."""
        path = self.storage_state_path

        def _seal(session: SurfaceSession) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            session.context.storage_state(path=str(path))

        return self._submit(_seal)

    def visibility(self) -> Future[dict[str, Any]]:
        """Read the surface's own `document.visibilityState` and
        `document.hasFocus()` through Playwright's main-world evaluate
        (`evaluate`'s path), returned as `{"visibilityState": ..., "hasFocus":
        ...}`. The presence gate reads this to know whether the surface is
        foreground."""
        return self._submit(
            lambda s: s.page.evaluate(
                "() => ({ visibilityState: document.visibilityState, "
                "hasFocus: document.hasFocus() })"
            )
        )

    def evaluate_isolated(self, expression: str) -> Future[Any]:
        """Evaluate `expression` in a fresh isolated world, so the page's scripts can't
        observe the read. Over CDP: getFrameTree, createIsolatedWorld, then
        Runtime.evaluate on that context, with no Runtime-domain enable (section 12.3,
        Our Finding 4). A fresh world per call: a cached one goes stale on navigation."""

        def _eval(session: SurfaceSession) -> Any:
            tree = session.cdp.send("Page.getFrameTree")
            frame_id = tree["frameTree"]["frame"]["id"]
            world = session.cdp.send(
                "Page.createIsolatedWorld",
                {"frameId": frame_id, "worldName": _ISOLATED_WORLD_NAME},
            )
            result = session.cdp.send(
                "Runtime.evaluate",
                {
                    "expression": expression,
                    "contextId": world["executionContextId"],
                    "returnByValue": True,
                },
            )
            return result["result"]["value"]

        return self._submit(_eval)

    def _submit(
        self,
        action: Callable[[SurfaceSession], Any],
        deadline_s: float = DEFAULT_ACTION_DEADLINE_SECONDS,
    ) -> Future[Any]:
        """Queue work for the surface thread. Returns immediately with a future
        the caller can wrap (`asyncio.wrap_future`) or ignore."""
        future: Future[Any] = Future()
        if self._stopping.is_set() or self._dead.is_set():
            future.set_exception(
                BrowserLaunchError(f"browser surface {self.slug!r} is shut down")
            )
            return future
        with self._activity_lock:
            self._last_activity = time.monotonic()
            self._inflight += 1
        self._commands.put((future, action, deadline_s))
        self._wake.set()
        # The thread may have died between the check above and the put, after
        # its own final drain; nothing else would ever resolve this future.
        if self._dead.is_set():
            self._fail_pending()
        return future

    def _fail_pending(self) -> None:
        """Fail every queued action once the run loop is over. A dead surface used to
        leave them unresolved forever (architecture-audit-2026-10-14.md section 3.3)."""
        while True:
            try:
                future, _, _ = self._commands.get_nowait()
            except queue.Empty:
                return
            if future.set_running_or_notify_cancel():
                future.set_exception(
                    BrowserLaunchError(f"browser surface {self.slug!r} stopped")
                )
            self._finished_one()

    def touch(self) -> None:
        """Mark the surface as just taken. Without this stamp the gap before a caller's
        first action is reapable, and the reaper could close a Chrome already handed out."""
        with self._activity_lock:
            self._last_activity = time.monotonic()

    def _finished_one(self) -> None:
        """One queued action resolved, on the surface thread. Stamps activity at
        COMPLETION as well as submission, so a long action leaves the surface
        looking freshly used rather than 6 minutes stale."""
        with self._activity_lock:
            self._last_activity = time.monotonic()
            self._inflight = max(0, self._inflight - 1)

    @property
    def idle_seconds(self) -> float | None:
        """How long this surface has been of use to nobody, or None while a viewer
        watches or work is in flight. None, not 0.0: `0.0 >= max_idle` once closed a
        pane the user was watching."""
        if self.has_viewer:
            return None
        with self._activity_lock:
            if self._inflight > 0:
                return None
            return time.monotonic() - self._last_activity

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


def _resolve(
    future: Future[Any], *, result: Any = None, error: BaseException | None = None
) -> None:
    """Settle `future` unless the deadline timer or the action already did: the 2 race
    by design, and whichever lands first is the caller's answer."""
    try:
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)
    except InvalidStateError:
        pass


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
        killer: Killer = kill_browser,
    ) -> None:
        self._root = Path(data_dir) / "browser"
        self._loop = loop
        self._opener = opener
        self._user_agent_resolver = user_agent_resolver
        self._geometry_timeout = geometry_timeout
        self._killer = killer
        # The de-headlessed UA, resolved once (a throwaway launch) and reused by
        # every later surface. Guarded because two surfaces can launch at once.
        self._user_agent: str | None = None
        self._ua_lock = threading.Lock()
        self._surfaces: dict[str, BrowserSurface] = {}
        self._lock = threading.Lock()
        # Per-slug launch/teardown locks. Bounded by the number of slugs ever
        # asked for (2 today: "linkedin" and the dev surface), so they are kept
        # rather than reaped — a lock is 40 bytes and dropping one would
        # reintroduce exactly the race it exists to close.
        self._slug_locks: dict[str, threading.Lock] = {}
        self._log = get_logger()

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

    def _lifecycle_lock(self, slug: str) -> threading.Lock:
        """One lock per slug covering LAUNCH and TEARDOWN, held only across
        those. Chrome takes a singleton lock on its profile directory, so
        launching a second Chrome on a slug whose first one is still tearing
        down hangs the new launch. `self._lock` can't serialize that: it guards
        the map and must never be held across a thread join."""
        with self._lock:
            existing = self._slug_locks.get(slug)
            if existing is None:
                existing = self._slug_locks[slug] = threading.Lock()
            return existing

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
                # Stamped HERE, under the same lock `reap_idle` reads idle
                # through, so a reaper can never decide against a surface that
                # has already been handed out.
                existing.touch()
                return existing
        self._make_room(slug)
        # No surface yet, or a dead one to replace. Take the slug's lifecycle
        # lock: if a reaper is closing this slug right now, this blocks until
        # its Chrome has really gone and the profile directory is free.
        with self._lifecycle_lock(slug):
            with self._lock:
                existing = self._surfaces.get(slug)
                if existing is not None and not existing.is_dead:
                    existing.touch()
                    return existing
                # A dead surface's thread has already torn its own Chrome down,
                # so there is nothing to join here. Chrome comes up lazily, on
                # `start()` below.
                surface = BrowserSurface(
                    slug,
                    profile_dir,
                    self._loop,
                    self._guardrailed_launch_kwargs,
                    opener=self._opener,
                    geometry_timeout=self._geometry_timeout,
                    killer=self._killer,
                )
                self._surfaces[slug] = surface
            surface.start()
            return surface

    def _make_room(self, slug: str) -> None:
        """At `MAX_SURFACES`, close the longest-idle other surface, with the
        same pick-then-recheck locking `reap_idle` uses. Blocking (it joins
        the closed surface's thread): `surface()` callers are off the loop."""
        with self._lock:
            if slug in self._surfaces or len(self._surfaces) < MAX_SURFACES:
                return
            idle = [
                (idle_s, other, surface)
                for other, surface in self._surfaces.items()
                if (idle_s := surface.idle_seconds) is not None
            ]
        if not idle:
            self._log.warning("%d browser surfaces in use; launching %r past the cap",
                              MAX_SURFACES, slug)
            return
        _, victim, surface = max(idle, key=lambda t: t[0])
        with self._lifecycle_lock(victim):
            with self._lock:
                if self._surfaces.get(victim) is not surface or surface.idle_seconds is None:
                    return
                del self._surfaces[victim]
            self._log.info("closing idle browser surface %r to make room for %r", victim, slug)
            surface.shutdown()

    def live_surface(self, slug: str) -> BrowserSurface | None:
        """The live surface for `slug`, or None: a peek that never launches Chrome, for
        callers that only read state. A surface whose thread died counts as absent."""
        with self._lock:
            existing = self._surfaces.get(slug)
        if existing is None or existing.is_dead:
            return None
        return existing

    @property
    def live_slugs(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._surfaces)

    def reap_idle(self, max_idle: float = IDLE_SHUTDOWN_SECONDS) -> list[str]:
        """Close every surface nobody is watching and nothing is driving; returns the
        slugs. In-use surfaces return None from `idle_seconds`, so they're never reaped.
        Blocking (joins threads). 2 steps: pick candidates under the map lock, then
        close each under its own lifecycle lock after a re-check, so a concurrent
        `surface(slug)` can't launch a second Chrome on a locked profile."""
        closed: list[str] = []
        with self._lock:
            candidates = [
                (slug, surface)
                for slug, surface in self._surfaces.items()
                if (idle := surface.idle_seconds) is not None and idle >= max_idle
            ]
        for slug, surface in candidates:
            with self._lifecycle_lock(slug):
                with self._lock:
                    if self._surfaces.get(slug) is not surface:
                        continue  # replaced under us; leave the new one alone
                    idle = surface.idle_seconds
                    if idle is None or idle < max_idle:
                        continue  # taken in the gap (`surface()` stamps it)
                    del self._surfaces[slug]
                self._log.info(
                    "closing idle browser surface %r after %.0fs", slug, idle
                )
                surface.shutdown()
                closed.append(slug)
        return closed

    def shutdown(self, timeout: float = SHUTDOWN_TIMEOUT_SECONDS) -> None:
        """Tear every surface down. Idempotent."""
        with self._lock:
            surfaces = list(self._surfaces.values())
            self._surfaces.clear()
        for surface in surfaces:
            surface.shutdown(timeout=timeout)
