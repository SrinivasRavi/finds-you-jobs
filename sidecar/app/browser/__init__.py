"""Core browser-session broker.

A persistent headless real-Chrome surface per slug, driven over CDP and shipped
to the app as JPEG screencast frames (`api/screencast_ws.py`). Vendor-agnostic:
surfaces are named by runtime slugs, so no site is named in here
(`docs/internal/plugin-architecture.md` section 8.1 rule 5).

Public surface:

- `BrowserBroker` — get-or-create a surface by slug; built once in the app
  lifespan, torn down with it.
- `BrowserSurface` / `Viewer` / `Frame` — one surface, its single websocket
  consumer, and one decoded frame.
- `stock_launch_kwargs` — the whole launch configuration, deliberately stock.
"""

from __future__ import annotations

from .broker import (
    BrowserBroker,
    BrowserLaunchError,
    BrowserSurface,
    Frame,
    SurfaceSession,
    Viewer,
)
from .launch import stock_launch_kwargs

__all__ = [
    "BrowserBroker",
    "BrowserLaunchError",
    "BrowserSurface",
    "Frame",
    "SurfaceSession",
    "Viewer",
    "stock_launch_kwargs",
]
