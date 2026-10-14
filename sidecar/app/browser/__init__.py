"""Core browser-session broker.

A persistent headless real-Chrome surface per slug, driven over CDP and streamed
as JPEG frames (`api/screencast_ws.py`). Vendor-agnostic: surfaces are runtime
slugs (plugin-architecture.md section 8.1 rule 5). Public: `BrowserBroker`,
`BrowserSurface` / `Viewer` / `Frame`, and the stock and guardrailed launch
kwargs."""

from __future__ import annotations

from .broker import (
    BrowserBroker,
    BrowserLaunchError,
    BrowserSurface,
    Frame,
    SurfaceSession,
    Viewer,
)
from .launch import minimal_launch_kwargs, stock_launch_kwargs

__all__ = [
    "BrowserBroker",
    "BrowserLaunchError",
    "BrowserSurface",
    "Frame",
    "SurfaceSession",
    "Viewer",
    "minimal_launch_kwargs",
    "stock_launch_kwargs",
]
