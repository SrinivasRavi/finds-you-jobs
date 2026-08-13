"""Live browser surface over a websocket (`GET /api/browser/screencast`).

Binary messages are one JPEG screencast frame each. Text messages are JSON
control in both directions: the server sends `status` and `error`, the client
sends `viewport` (once, on open, carrying its real display geometry), `navigate`
and `resize`. Which surface a socket attaches to is a `?surface=` slug the client
passes, never a name this file knows
(`docs/internal/plugin-architecture.md` section 8.1 rule 5).

Auth is checked here rather than in the middleware, and both halves of that are
deliberate. `BearerAuthMiddleware` returns early for any non-`http` scope, so a
websocket never reaches it; and a browser's websocket handshake can't set an
`Authorization` header anyway, which is why the token rides in `?token=` the way
the SSE stream's already does. Loopback-only surface (NFR-SEC-03).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from ..auth import token_ok
from ..browser import BrowserSurface, Viewer
from ..logging_setup import get_logger

router = APIRouter()

# 1008 is "policy violation" (bad token, unusable surface name); 1011 is "the
# server couldn't do what you asked" (no Chrome, launch failed).
POLICY_CLOSE = 1008
SERVER_ERROR_CLOSE = 1011

# The surface a client gets when it doesn't name one. A generic slug: core has
# no site of its own to default to.
DEFAULT_SURFACE = "default"

# How long the control reader waits for a navigation to commit before it gives
# up on that command. `page.goto`'s own default timeout is 30 s, so 35 s covers
# a hung page while still releasing the reader if the surface thread has died: a
# crashed surface never resolves the future, and an unbounded await would hang
# the reader on it forever.
NAVIGATE_TIMEOUT_SECONDS = 35.0


@router.websocket("/api/browser/screencast")
async def screencast(websocket: WebSocket) -> None:
    """Stream one surface's frames to one viewer until either side hangs up."""
    expected = getattr(websocket.app.state, "token", "") or ""
    if not token_ok(websocket.query_params.get("token"), expected):
        await websocket.close(code=POLICY_CLOSE)
        return
    broker = getattr(websocket.app.state, "browser", None)
    if broker is None:
        await websocket.close(code=SERVER_ERROR_CLOSE)
        return

    slug = websocket.query_params.get("surface") or DEFAULT_SURFACE
    await websocket.accept()
    try:
        surface = broker.surface(slug)
    except ValueError as exc:
        await _send_error(websocket, str(exc))
        await websocket.close(code=POLICY_CLOSE)
        return
    try:
        # The launch runs on the surface's own thread; this only awaits it.
        await surface.wait_ready()
    except Exception as exc:  # noqa: BLE001 — verbatim to the client, then close
        get_logger().exception("browser surface %r unavailable", slug)
        await _send_error(websocket, f"{type(exc).__name__}: {exc}")
        await websocket.close(code=SERVER_ERROR_CLOSE)
        return

    viewer = Viewer()
    surface.set_viewer(viewer)
    await websocket.send_json({"type": "status", "state": "streaming", "surface": slug})
    frames = asyncio.create_task(_pump_frames(websocket, surface, viewer))
    control = asyncio.create_task(_read_control(websocket, surface))
    try:
        # Either side ending ends the session: a dead socket stops the pump, a
        # client disconnect stops the reader.
        await asyncio.wait({frames, control}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        surface.set_viewer(None)
        for task in (frames, control):
            task.cancel()
        # `wait` here, never `gather(return_exceptions=True)`: gather would
        # swallow a cancellation aimed at THIS coroutine and return as if the
        # socket had closed cleanly, which leaves the ASGI server believing a
        # cancelled task finished normally.
        await asyncio.wait({frames, control})


async def _pump_frames(
    websocket: WebSocket, surface: BrowserSurface, viewer: Viewer
) -> None:
    """One JPEG per binary message.

    The ack is Chrome's permission to capture the next frame, so it is sent only
    once this frame's send has flushed — acking earlier would let capture outrun
    the socket. It is sent even when the send fails, because the invariant is
    exactly one ack per frame and a stalled screencast would outlive this socket.
    """
    while True:
        frame = await viewer.queue.get()
        try:
            await websocket.send_bytes(frame.jpeg)
        finally:
            surface.ack(frame.session_id)


async def _read_control(websocket: WebSocket, surface: BrowserSurface) -> None:
    """Client → server control. Anything unparseable or unknown is ignored: a
    malformed message is a client bug, never a reason to drop a live surface."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None:  # binary from the client: nothing sends it, ignore it
            continue
        try:
            command = json.loads(text)
        except ValueError:
            continue
        if isinstance(command, dict):
            await _apply_command(websocket, surface, command)


async def _apply_command(
    websocket: WebSocket, surface: BrowserSurface, command: dict[str, Any]
) -> None:
    """Hand one control message to the surface. `resize` queues onto the surface
    thread and returns at once. `navigate` waits — bounded — for the page to
    commit, then reports the committed, post-redirect URL back as a `status`
    frame; a timeout or a dead surface is reported as an `error` and never
    crashes the reader."""
    kind = command.get("type")
    if kind == "viewport":
        # The frontend's own `window.screen` (its webview reports the real
        # monitor; a Playwright-driven browser reports its emulated one), applied
        # as the surface's display geometry. Sent the instant the socket opens, so
        # it lands before the first navigation, which fails closed without it.
        width, height, dpr = command.get("width"), command.get("height"), command.get("dpr")
        if (
            isinstance(width, int)
            and isinstance(height, int)
            and isinstance(dpr, (int, float))
        ):
            surface.set_geometry(width, height, dpr)
    elif kind == "navigate":
        url = command.get("url")
        if isinstance(url, str) and url:
            try:
                committed = await asyncio.wait_for(
                    asyncio.wrap_future(surface.navigate(url)),
                    timeout=NAVIGATE_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 — timeout or dead surface, told to the client
                await _send_error(websocket, f"{type(exc).__name__}: {exc}")
                return
            await websocket.send_json({"type": "status", "url": committed})
    elif kind == "resize":
        width, height = command.get("width"), command.get("height")
        if isinstance(width, int) and isinstance(height, int):
            surface.resize(width, height)


async def _send_error(websocket: WebSocket, message: str) -> None:
    """Best-effort `error` frame before a close. A client that already went away
    is not a failure worth raising."""
    if websocket.client_state is not WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json({"type": "error", "message": message})
    except Exception:  # noqa: BLE001, S110 — the close is what matters
        pass
