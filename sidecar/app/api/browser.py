"""First-use Chromium download (A0.6 / A5b).

Playwright's Chromium is **not** bundled (architecture section 4.5); the Applier's
typed `ApplyError` ("Chromium is not installed…") surfaces in the UI as a
friendly state with a Download action. This runs `playwright install chromium`
as a child subprocess and publishes coarse progress onto the SSE hub — never an
automatic, silent download (US-APP-01 stance). Progress is coarse by design
(started → done/failed); fine-grained byte progress is a polish item.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from ..db.database import resolve_data_dir
from ..events import make_event
from ..logging_setup import get_logger

_INSTALL_TIMEOUT_S = 600
_lock = threading.Lock()
_running = False
# The last download's state, readable by a client that missed its event (A21).
_status: dict[str, str] = {"state": "idle", "message": ""}
_STATES = {"started": "running", "done": "done", "failed": "failed"}


# Playwright's own wording when the browser it wants isn't on disk.
_MISSING_MARKER = "Executable doesn't exist"


def is_browser_missing(error_text: str) -> bool:
    return _MISSING_MARKER in error_text


def pin_browsers_path() -> None:
    """A frozen Playwright looks for browsers inside the bundle, which is signed
    and can't change, so a bundled app keeps them in its app-data folder (A16).
    Unfrozen keeps Playwright's shared cache. Call before Playwright starts."""
    if getattr(sys, "frozen", False):
        os.environ.setdefault(
            "PLAYWRIGHT_BROWSERS_PATH", str(resolve_data_dir() / "ms-playwright")
        )


def install_command() -> tuple[list[str], dict[str, str]]:
    """Playwright's own driver and its env. Never `sys.executable -m playwright`:
    frozen, that's the sidecar, which ignores the args and boots again (A16)."""
    from playwright._impl._driver import compute_driver_executable, get_driver_env

    node, cli = compute_driver_executable()
    return [node, cli, "install", "chromium"], get_driver_env()


def install_status() -> dict[str, str]:
    with _lock:
        return dict(_status)


def _publish_install(publish, state: str, message: str) -> None:  # type: ignore[no-untyped-def]
    with _lock:
        _status.update(state=_STATES[state], message=message)
    if publish is not None:
        publish(make_event("browser_install", {"state": state, "message": message}))


def _run_install(publish) -> None:  # type: ignore[no-untyped-def]
    global _running
    log = get_logger()
    _publish_install(publish, "started", "Downloading Chromium for Playwright…")
    try:
        argv, env = install_command()
        proc = subprocess.run(  # noqa: S603 — fixed args, no shell
            argv,
            env=env,
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
        # Before the thread starts, so a status read right after this call never
        # sees the previous download's outcome.
        _status.update(state="running", message="")
    threading.Thread(
        target=_run_install, args=(publish,), name="fyj-browser-install", daemon=True
    ).start()
    return "started"
