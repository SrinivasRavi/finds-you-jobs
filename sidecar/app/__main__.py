"""Sidecar entrypoint: `python -m sidecar.app`.

Binds 127.0.0.1 on a random free port, mints a bearer token, and prints the
handshake the shell reads:

    PORT=<n>
    TOKEN=<uuid>

as two flushed stdout lines (architecture §4.4 step 1). Then serves the FastAPI
app until `/shutdown`, the orphan watchdog, or a signal ends it.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import uuid

import uvicorn

from .logging_setup import get_logger, setup_flight_recorder
from .main import create_app


def find_free_port() -> int:
    """Ask the OS for a free loopback port, then release it for uvicorn to bind.

    A tiny bind→close→rebind race exists but is harmless on a single-user
    loopback machine; the shell waits on `/healthz` before using the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def emit_handshake(port: int, token: str) -> None:
    """Print the PORT/TOKEN lines the shell parses, flushed immediately."""
    sys.stdout.write(f"PORT={port}\n")
    sys.stdout.write(f"TOKEN={token}\n")
    sys.stdout.flush()


def _maybe_write_dev_handshake(port: int, token: str) -> None:
    """Dev-only convenience for the browser-dev path (`scripts/dev-web.mjs` uses
    stdout; this file target is opt-in via FYJ_WRITE_HANDSHAKE). Never written in
    the packaged app."""
    target = os.environ.get("FYJ_WRITE_HANDSHAKE")
    if not target:
        return
    with open(target, "w", encoding="utf-8") as handle:
        json.dump({"port": port, "token": token}, handle)


def main() -> int:
    setup_flight_recorder()
    log = get_logger()

    port = int(os.environ.get("FYJ_PORT", "0")) or find_free_port()
    token = os.environ.get("FYJ_API_TOKEN") or str(uuid.uuid4())
    original_ppid = os.getppid()

    log.info("sidecar booting: port=%d ppid=%d", port, original_ppid)
    app = create_app(token=token, original_ppid=original_ppid)

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
        # /shutdown must actually end the process even while an SSE client is
        # attached: uvicorn's default graceful shutdown waits forever for open
        # connections, and the app always holds the /api/events stream open.
        # 5 s stays inside the shell's 10 s drain window (§4.4 step 3), so the
        # sidecar exits on its own before the AM3 force-kill has to fire.
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)

    # The exit hook: /shutdown and the orphan watchdog both flip should_exit.
    app.state.request_shutdown = lambda: setattr(server, "should_exit", True)

    emit_handshake(port, token)
    _maybe_write_dev_handshake(port, token)

    try:
        server.run()
    except KeyboardInterrupt:
        # Windows console Ctrl-C reaches every process in the console group and
        # asyncio re-raises it here — a normal shutdown, not an error. Exit
        # quietly instead of spraying a traceback (observed 2026-07-18).
        log.info("sidecar stopped by Ctrl-C")
        return 0
    log.info("sidecar exited cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
