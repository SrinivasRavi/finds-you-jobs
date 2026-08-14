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
"""

from __future__ import annotations

from .upstream.errors import VoyagerError
from .upstream.pacing import Pacer, PacingProfile


def worker_module():
    """The GPL browser worker module, imported on first use (an op running).

    Kept lazy so the pacing-only callers and the driver-build path stay clear of
    the worker chain (and its playwright dependency) until a browser op runs."""
    from .upstream import worker

    return worker


__all__ = ["Pacer", "PacingProfile", "VoyagerError", "worker_module"]
