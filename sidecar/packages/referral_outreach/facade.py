"""Host-facing facade over the GPLv3 `upstream/` core (seam F-P10).

AGPL-3.0-only. Core never imports `upstream.*`; it imports from here, the 1 file
inside the GPL package that does. A thin pass-through: `Pacer`, `PacingProfile`,
`VoyagerError` and the typed worker errors (for the exception router). `worker`
stays behind the lazy `worker_module()`, so reading pacing types never loads
the browser chain (`test_upstream_import_boundary` enforces the rule)."""

from __future__ import annotations

from .upstream.errors import (
    AuthenticationError,
    BrowserUnresponsiveError,
    CapExceeded,
    ProfileInaccessibleError,
    RateLimited,
    ReachedConnectionLimit,
    SkipProfile,
    VoyagerError,
)
from .upstream.pacing import Pacer, PacingProfile


def worker_module():
    """The GPL browser worker module, imported on first use (an op running).

    Kept lazy so the pacing-only callers and the driver-build path stay clear of
    the worker chain (and its playwright dependency) until a browser op runs."""
    from .upstream import worker

    return worker


def referral_surface_slug() -> str:
    """The runtime slug naming the referral surface and its broker profile dir
    (`session.SURFACE_SLUG`, owned in the GPL package so core never spells the
    vendor), so the headed login and the broker surface share 1 profile. A
    function-local import, so reading it loads Playwright only when a real op runs."""
    from .upstream.session import SURFACE_SLUG

    return SURFACE_SLUG


__all__ = [
    "AuthenticationError",
    "BrowserUnresponsiveError",
    "CapExceeded",
    "Pacer",
    "PacingProfile",
    "ProfileInaccessibleError",
    "RateLimited",
    "ReachedConnectionLimit",
    "SkipProfile",
    "VoyagerError",
    "referral_surface_slug",
    "worker_module",
]
