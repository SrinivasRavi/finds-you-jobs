"""First-use Chromium download (A0.6 / A5b).

Playwright's Chromium is **not** bundled (architecture §4.5); the Applier's
typed `ApplyError` ("Chromium is not installed…") surfaces in the UI as a
friendly state with a Download action. This runs `playwright install chromium`
as a child subprocess and publishes coarse progress onto the SSE hub — never an
automatic, silent download (US-APP-01 stance). Progress is coarse by design
(started → done/failed); fine-grained byte progress is a polish item.
"""

from __future__ import annotations

import subprocess
import sys
import threading

from ..events import make_event
from ..logging_setup import get_logger

_INSTALL_TIMEOUT_S = 600
_lock = threading.Lock()
_running = False


def _publish_install(publish, state: str, message: str) -> None:  # type: ignore[no-untyped-def]
    if publish is not None:
        publish(make_event("browser_install", {"state": state, "message": message}))


def _run_install(publish) -> None:  # type: ignore[no-untyped-def]
    global _running
    log = get_logger()
    _publish_install(publish, "started", "Downloading Chromium for Playwright…")
    try:
        proc = subprocess.run(  # noqa: S603 — fixed args, no shell
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            timeout=_INSTALL_TIMEOUT_S,
        )
        if proc.returncode == 0:
            log.info("chromium install completed")
            _publish_install(publish, "done", "Chromium is ready — you can apply now.")
        else:
            tail = (proc.stderr or proc.stdout or "install failed").strip()[-500:]
            log.error("chromium install failed: %s", tail)
            _publish_install(publish, "failed", tail)
    except Exception as exc:  # noqa: BLE001 — verbatim to the UI
        log.exception("chromium install raised")
        _publish_install(publish, "failed", f"{type(exc).__name__}: {exc}")
    finally:
        with _lock:
            _running = False


def start_install(publish) -> str:  # type: ignore[no-untyped-def]
    """Kick off a background Chromium install. Returns "started" or, if one is
    already in flight, "already_running" (idempotent — no double download)."""
    global _running
    with _lock:
        if _running:
            return "already_running"
        _running = True
    threading.Thread(
        target=_run_install, args=(publish,), name="fyj-browser-install", daemon=True
    ).start()
    return "started"
