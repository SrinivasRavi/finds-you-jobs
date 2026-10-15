"""The Applier's browser window: 1 Chrome window shared by every apply run, each
run in its own tab (docs/internal/applier/window.md).

The window lives on the browser host's loop (`app/browser/host.py`), the only
one that touches its Playwright objects. An apply op hands its whole coroutine
to that loop and blocks its runner thread on the result. Tabs open in the
background over CDP, so a new run never takes the user's focus (window.md
section 3)."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
import threading
from collections.abc import Coroutine
from concurrent.futures import Future
from typing import Any, TypeVar

from ..api.browser import is_browser_missing
from ..browser.host import get_host, launch
from ..logging_setup import get_logger

T = TypeVar("T")

# After the last run's tab is done, the window stays this long for the next
# run before it closes (window.md section 4.4).
CLOSE_GRACE_S = 30.0
_SHUTDOWN_TIMEOUT_S = 10.0
_SHOW_TIMEOUT_S = 5.0

BROWSER_MISSING_DETAIL = (
    "Applying needs Chrome or Chromium, and neither is on this computer. "
    "Download Chromium, then apply again."
)


class BrowserMissingError(RuntimeError):
    """No installed Chrome and no downloaded Chromium (A18). The run lands failed
    with a `browser_missing` blocker, which the Applier panel turns into Download."""


class ApplierWindow:
    def __init__(self, *, headless: bool) -> None:
        self.headless = headless
        self._host = get_host()
        self._loop = self._host.loop
        self._context: Any = None
        self._cdp: Any = None
        self._profile: str | None = None
        # The blank tab a launch opens, or the last run's tab kept after it ended,
        # handed to the next run so the window never closes between runs.
        self._spare: Any = None
        self._tabs: dict[str, Any] = {}
        self._lock: asyncio.Lock | None = None
        self._close_timer: asyncio.TimerHandle | None = None
        self._futures: set[Future[Any]] = set()

    # -- called from runner threads -------------------------------------------

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        """Run `coro` on the window's loop and wait for it. Never call from the loop."""
        future = self._host.submit(coro)
        self._futures.add(future)
        try:
            return future.result()
        finally:
            self._futures.discard(future)

    async def run_async(self, coro: Coroutine[Any, Any, T]) -> T:
        """Await `coro` on the window's loop from another loop."""
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    def on_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def show(self, run_id: str) -> bool:
        """Bring a run's tab to the front, because the user asked. False if it has none."""
        if run_id not in self._tabs:
            return False
        future = asyncio.run_coroutine_threadsafe(self._show(run_id), self._loop)
        try:
            return future.result(timeout=_SHOW_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — a closed tab or a slow window reads as "no tab"
            get_logger().debug("applier tab show failed", exc_info=True)
            return False

    def has_tab(self, run_id: str) -> bool:
        return run_id in self._tabs

    def shutdown(self) -> None:
        for future in list(self._futures):
            future.cancel()
        if self._loop.is_running():
            done = self._host.submit(self._close_window())
            try:
                done.result(timeout=_SHUTDOWN_TIMEOUT_S)
            except Exception:  # noqa: BLE001 — shutting down regardless
                get_logger().debug("applier window close failed", exc_info=True)

    # -- called on the loop ---------------------------------------------------

    async def playwright(self) -> Any:
        """The host's Playwright. The PDF render shares it."""
        return await self._host.playwright()

    async def open_tab(self, run_id: str) -> Any:
        """A tab for `run_id`, launching the window if it isn't up. Serialized:
        a background tab is matched to its run by the next `page` event."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._close_timer is not None:
                self._close_timer.cancel()
                self._close_timer = None
            context = self._context if self._context is not None else await self._launch()
            if self._spare is not None and not self._spare.is_closed():
                page, self._spare = self._spare, None
            else:
                async with context.expect_page() as info:
                    await self._cdp.send(
                        "Target.createTarget", {"url": "about:blank", "background": True}
                    )
                page = await info.value
            self._tabs[run_id] = page
            return page

    async def close_tab(self, run_id: str) -> None:
        """Done with a run's tab. The last one stays as the spare, blank, until the
        grace runs out, so back-to-back runs reuse the window."""
        page = self._tabs.pop(run_id, None)
        if page is None or page.is_closed():
            page = None
        if self._tabs:
            if page is not None:
                await page.close()
            return
        if page is not None and self._spare is None:
            try:
                await page.goto("about:blank")
                self._spare = page
            except Exception:  # noqa: BLE001 — a tab the user is closing
                get_logger().debug("applier spare tab reset failed", exc_info=True)
        elif page is not None:
            await page.close()
        if self._context is not None and self._close_timer is None:
            self._close_timer = self._loop.call_later(
                CLOSE_GRACE_S, lambda: asyncio.ensure_future(self._close_if_idle())
            )

    async def _launch(self) -> Any:
        pw = await self.playwright()
        # A fresh profile per window: nothing of one session outlives it
        # (window.md section 4.3), including a window the user quit.
        if self._profile is not None:
            shutil.rmtree(self._profile, ignore_errors=True)
        self._profile = tempfile.mkdtemp(prefix="fyj-applier-")
        kwargs: dict[str, Any] = {"headless": self.headless}
        if not self.headless:
            kwargs["no_viewport"] = True  # pages follow the window the user resizes
        try:
            context = await launch(pw.chromium, self._profile, **kwargs)
        except Exception as exc:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None
            if is_browser_missing(str(exc)):
                raise BrowserMissingError(BROWSER_MISSING_DETAIL) from exc
            raise
        context.on("close", lambda _ctx: self._forget_context(context))
        self._context = context
        self._cdp = await context.browser.new_browser_cdp_session()
        self._spare = context.pages[0] if context.pages else None
        return context

    def _forget_context(self, context: Any) -> None:
        """The user quit the window: the next run launches a new one."""
        if self._context is context:
            self._context = None
            self._cdp = None
            self._spare = None

    async def _show(self, run_id: str) -> bool:
        page = self._tabs.get(run_id)
        if page is None or page.is_closed():
            return False
        await page.bring_to_front()
        return True

    async def _close_if_idle(self) -> None:
        self._close_timer = None
        if self._lock is None:
            self._lock = asyncio.Lock()
        # Under the tab lock: a run starting mid-close would launch on the Playwright
        # this close is about to stop.
        async with self._lock:
            if not self._tabs:
                await self._close_window()

    async def _close_window(self) -> None:
        context, self._context = self._context, None
        self._cdp = None
        self._spare = None
        self._tabs.clear()
        if context is not None:
            try:
                await context.close()
            except Exception:  # noqa: BLE001 — already closed by the user
                get_logger().debug("applier window close raced", exc_info=True)
        if self._profile is not None:
            shutil.rmtree(self._profile, ignore_errors=True)
            self._profile = None


_windows: dict[bool, ApplierWindow] = {}
_windows_lock = threading.Lock()


def window_for(*, headless: bool) -> ApplierWindow:
    """The process's window, 1 per mode. Headless exists for tests and dev runs."""
    with _windows_lock:
        window = _windows.get(headless)
        if window is None:
            window = _windows[headless] = ApplierWindow(headless=headless)
        return window


def show_run(run_id: str) -> bool:
    """Bring a run's tab to the front, whichever window holds it."""
    with _windows_lock:
        windows = list(_windows.values())
    return any(w.show(run_id) for w in windows if w.has_tab(run_id))


def shutdown_windows() -> None:
    with _windows_lock:
        windows = list(_windows.values())
        _windows.clear()
    for window in windows:
        window.shutdown()
