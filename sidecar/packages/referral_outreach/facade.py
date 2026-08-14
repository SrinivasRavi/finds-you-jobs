"""Host-facing facade over the GPLv3 `upstream/` core (seam F-P10).

finds-you-jobs-owned (AGPL-3.0-only). The one-way import rule keeps core
(`sidecar.app.*`, `sidecar.modules.*`) off `upstream.*`. Core imports the few
symbols it needs from here, and only this file, which lives inside the GPL
package, reaches into `upstream.*`. A thin pass-through with no logic: the same
`Pacer`/`PacingProfile` classes, the same `VoyagerError`, and the same worker
module the app already drove directly, exposed under one AGPL-owned name.

`worker` stays behind `worker_module()`, a lazy accessor, and is never a
top-level re-export. Importing this facade for the pacing types (the caps/quota
display path) or to build the driver never pulls the browser worker chain in;
the worker is imported only when an op actually runs, exactly as the prior
direct `from .upstream import worker` did.

The typed worker errors (`AuthenticationError` … `CapExceeded`) are re-exported
here too, so the app-side exception router classifies them through this seam
rather than reaching into `upstream.errors` directly (the F-P10 one-way rule the
`test_upstream_import_boundary` guard enforces). They're plain exception classes
with no browser dependency, so re-exporting them adds no import cost.
"""

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
    """The runtime slug that names the referral browser surface AND its per-slug
    profile dir under the core broker's data root (`session.SURFACE_SLUG`, the
    single source of truth, owned inside the GPL package so core never spells the
    vendor). Exposed behind this F-P10 facade so the host can point the one-time
    login's persistent profile at the broker's `<data>/browser/<slug>/profile` —
    the Phase-5 profile reconciliation, which lets a session captured by the
    headed login be read back by a headless broker surface on the same slug.

    The import is function-local (it pulls `upstream.session`, hence Playwright)
    so reading the slug never loads the browser chain at facade-import time. It is
    resolved only when a driver is being built for a real op, which is about to
    touch a browser anyway."""
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
