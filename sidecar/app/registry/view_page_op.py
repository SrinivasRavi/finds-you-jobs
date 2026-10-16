"""The `view_page` operation: a user's page view, queued like every lane op.

It waits its turn behind whatever drives the surface (policy rule 3,
`BROWSER_LANE_KINDS`) instead of navigating mid-op, and shows in the queue panel.
A view of the page already shown is `skipped`, compared by path (LinkedIn adds
query params; e2e serves a loopback origin). Vendor-agnostic: url and slug are
arguments."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from . import networker_ops
from .autonomy import authorize
from .operations import OperationContext, OperationOutcome

# One page-load has 45 s to commit — same bound the retired /api/browser/open
# route used.
_NAVIGATE_TIMEOUT_S = 45.0


def _same_page(current: str, target: str) -> bool:
    """True when `current` (the surface's committed URL) already shows `target`.

    Path-identity only: query/fragment are redirect noise, and the netloc is
    deliberately ignored (one vendor per surface; the e2e fixture origin serves
    the same paths). An empty `current` (blank/dead surface) never matches."""
    if not current:
        return False
    current_path = urlparse(current).path.rstrip("/") or "/"
    target_path = urlparse(target).path.rstrip("/") or "/"
    return current_path == target_path


def view_page_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Show the snapshot's `url` on its broker surface, once the lane is ours."""
    authorize(ctx.db, "view", ctx.requested_by)  # the live view opens on a click only
    snapshot = ctx.input_snapshot
    url = str(snapshot.get("url") or "")
    slug = str(snapshot.get("surface") or "default")

    provider = networker_ops.SURFACE_PROVIDER
    if provider is None:
        raise RuntimeError("browser broker unavailable")
    # Worker thread, so blocking on the surface's futures is the established
    # bridge contract (`build_surface_provider`) — the serving loop stays free.
    surface = provider(slug)

    result: dict[str, Any] = {"url": url, "skipped": False}
    contact_id = str(snapshot.get("contact_id") or "")
    if contact_id:
        result["contact_id"] = contact_id

    current = str(getattr(surface, "page_url", "") or "")
    if _same_page(current, url):
        result.update(url=current, skipped=True)
        return OperationOutcome(result_ref=result)

    # Viewer-style display geometry, required before a surface's first
    # navigation (navigate fails closed without it) — the caller's screen
    # metrics ride the snapshot from BrowserViewRequest.
    width, height, dpr = snapshot.get("width"), snapshot.get("height"), snapshot.get("dpr")
    if isinstance(width, int) and isinstance(height, int) and isinstance(dpr, (int, float)):
        surface.set_geometry(width, height, dpr)

    committed = surface.navigate(url).result(timeout=_NAVIGATE_TIMEOUT_S)
    result["url"] = str(committed)
    return OperationOutcome(result_ref=result)


def view_page_entrypoints() -> dict[str, Any]:
    """The page-view kind → entrypoint (registered in operations.py)."""
    return {"view_page": view_page_entrypoint}
