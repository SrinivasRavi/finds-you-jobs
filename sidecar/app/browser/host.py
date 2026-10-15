"""1 browser host for every Chrome the app drives (audit-2026-10-14.md section 8.9).

AGPL-3.0-only. 1 thread runs 1 event loop with 1 async Playwright driver. Async
callers (the Applier, the PDF render) await on it; sync callers (the LinkedIn
worker, D92) drive it through `SyncProxy`, which runs each Playwright call on the
host loop and waits for the answer. 1 launch function holds the Chrome-first and
sandbox fallbacks for all of them."""

from __future__ import annotations

import asyncio
import inspect
import threading
import weakref
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any, TypeVar

from ..logging_setup import get_logger

T = TypeVar("T")

# The Chrome channel tried first; tests set None to reach the bundled build.
PREFERRED_CHANNEL: str | None = "chrome"
_SHUTDOWN_TIMEOUT_S = 10.0


def _is_sandbox_launch_error(message: str) -> bool:
    # Chromium's sandbox failures name the sandbox or the missing kernel facility;
    # anything else must not silently drop the sandbox.
    msg = message.lower()
    return "sandbox" in msg or "namespace" in msg


async def launch(chromium: Any, profile_dir: str | None = None, **kwargs: Any) -> Any:
    """A browser, or a persistent context when `profile_dir` is set. Installed Chrome
    first, the bundled build second; the sandbox drops only on a sandbox failure (a
    Linux host without user namespaces)."""
    log = get_logger()
    channel = kwargs.pop("channel", PREFERRED_CHANNEL)
    sandbox = bool(kwargs.pop("chromium_sandbox", True))

    async def _open(use_channel: bool, use_sandbox: bool) -> Any:
        extra = {"channel": channel} if use_channel and channel else {}
        if profile_dir is None:
            return await chromium.launch(chromium_sandbox=use_sandbox, **extra, **kwargs)
        return await chromium.launch_persistent_context(
            profile_dir, chromium_sandbox=use_sandbox, **extra, **kwargs
        )

    async def _sandboxed(use_channel: bool) -> Any:
        try:
            return await _open(use_channel, sandbox)
        except Exception as exc:  # noqa: BLE001 — non-sandbox failures re-raise
            if not sandbox or not _is_sandbox_launch_error(str(exc)):
                raise
            log.warning("Chromium sandbox unavailable (%s) — retrying with it off", exc)
            return await _open(use_channel, False)

    try:
        return await _sandboxed(True)
    except Exception as exc:  # noqa: BLE001 — no installed Chrome → bundled build
        if not channel:
            raise
        log.info("installed Chrome unavailable (%s) — using bundled Chromium", exc)
        return await _sandboxed(False)


class BrowserHost:
    """The thread, its loop, and the Playwright driver, all started on first use."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="fyj-browser-host", daemon=True)
        self._started = False
        self._start_lock = threading.Lock()
        self._pw: Any = None
        self._pw_lock: asyncio.Lock | None = None
        # The same target always gets the same proxy, so `is` holds across calls.
        self._proxies: weakref.WeakKeyDictionary[Any, SyncProxy] = weakref.WeakKeyDictionary()
        self._proxies_lock = threading.Lock()  # workers and the loop both wrap

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        self._ensure_started()
        return self._loop

    def on_loop(self) -> bool:
        try:
            return asyncio.get_running_loop() is self._loop
        except RuntimeError:
            return False

    def _ensure_started(self) -> None:
        with self._start_lock:
            if not self._started:
                self._thread.start()
                self._started = True

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        """Run `coro` on the host loop from any other thread."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def run_async(self, coro: Coroutine[Any, Any, T]) -> T:
        """Await `coro` on the host loop from another loop."""
        if self.on_loop():
            return await coro
        return await asyncio.wrap_future(self.submit(coro))

    def run(self, fn: Callable[[], Any], timeout: float | None = None) -> Any:
        """Call `fn` on the host loop, await it if it returns a coroutine, and wait
        for the result on this thread. Refused on the host loop: it would wait on
        itself forever."""
        if self.on_loop():
            raise RuntimeError("a sync browser call ran on the browser host's own loop")

        async def _call() -> Any:
            value = fn()
            return await value if inspect.isawaitable(value) else value

        return self.submit(_call()).result(timeout)

    async def playwright(self) -> Any:
        """The 1 async Playwright driver, started on first use, on the host loop."""
        if self._pw_lock is None:
            self._pw_lock = asyncio.Lock()
        async with self._pw_lock:
            if self._pw is None:
                from playwright.async_api import async_playwright

                self._pw = await async_playwright().start()
        return self._pw

    def proxy(self, target: Any) -> Any:
        """`target` as sync calls on the host, if it is a Playwright object."""
        if isinstance(target, SyncProxy):
            return target
        if not _is_playwright(target):
            if isinstance(target, list):
                return [self.proxy(t) for t in target]
            return target
        with self._proxies_lock:
            existing = self._proxies.get(target)
            if existing is None:
                existing = self._proxies[target] = SyncProxy(target, self)
            return existing

    def shutdown(self) -> None:
        if not self._started:
            return

        async def _stop() -> None:
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

        try:
            self.submit(_stop()).result(_SHUTDOWN_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — shutting down regardless
            get_logger().debug("browser host stop failed", exc_info=True)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(_SHUTDOWN_TIMEOUT_S)


def _is_playwright(obj: Any) -> bool:
    return type(obj).__module__.startswith("playwright.")


class SyncProxy:
    """An async Playwright object driven with sync calls from a worker thread (D92).

    A method call runs on the host loop; its result comes back wrapped the same way.
    An event handler is called on the host loop with wrapped arguments, so it must
    only record what it is given: a sync call from there is refused, not deadlocked."""

    __slots__ = ("__weakref__", "_handlers", "_host", "_target")

    def __init__(self, target: Any, host: BrowserHost) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_host", host)
        object.__setattr__(self, "_handlers", {})

    @property
    def target(self) -> Any:
        """The async object behind this proxy, for code already on the host loop."""
        return self._target

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        if not callable(attr):
            return self._host.proxy(attr)
        host, handlers = self._host, self._handlers

        def call(*args: Any, **kwargs: Any) -> Any:
            if name in ("on", "once", "remove_listener") and len(args) == 2:
                event, handler = args
                if name == "remove_listener":
                    wrapped = handlers.pop((event, handler), handler)
                else:
                    wrapped = handlers[(event, handler)] = _handler(host, handler)
                args = (event, wrapped)
            args = tuple(_unwrap(a) for a in args)
            kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
            return host.proxy(host.run(lambda: attr(*args, **kwargs)))

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._target, name, _unwrap(value))

    def __eq__(self, other: object) -> bool:
        return self._target == _unwrap(other)

    def __hash__(self) -> int:
        return hash(self._target)

    def __repr__(self) -> str:
        return f"SyncProxy({self._target!r})"


def _unwrap(value: Any) -> Any:
    return value.target if isinstance(value, SyncProxy) else value


def _handler(host: BrowserHost, handler: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any) -> Any:
        return handler(*(host.proxy(a) for a in args))

    return wrapped


_HOST: BrowserHost | None = None
_HOST_LOCK = threading.Lock()


def get_host() -> BrowserHost:
    """The process's 1 browser host."""
    global _HOST
    with _HOST_LOCK:
        if _HOST is None:
            _HOST = BrowserHost()
        return _HOST


def shutdown_host() -> None:
    global _HOST
    with _HOST_LOCK:
        host, _HOST = _HOST, None
    if host is not None:
        host.shutdown()
