"""Facade error mapping over the GPLv3 `upstream/` core.

finds-you-jobs-owned (AGPL-3.0-only). This module holds the seam's documented
translation from an upstream error onto the facade's typed errors (`types.py`;
`docs/internal/referral-outreach.md` section 3.1). The second concrete
`ReferralAutomation` that used to live here is gone (duplication audit D-M2): it
had zero callers and had already rotted away from the worker signatures the
app's real driver (`sidecar/modules/networker/driver.py`) keeps current.
"""

from __future__ import annotations

from .types import (
    AuthenticationError,
    BrowserFailure,
    InviteCapReached,
    ProfileUnavailable,
    RateLimited,
    ReferralError,
)


def _translate_error(exc: Exception) -> ReferralError:
    """Map an upstream error onto a facade-typed error, keeping only its safe
    message (never cookies/tokens/state). Classified by the upstream error TYPE,
    so the GPL `upstream.errors` types stay an implementation detail of this
    file without the mapping having to track message wording: a 999 bot-block
    matched none of the rate-limit tokens the previous text match scanned for,
    so it fell through to `BrowserFailure` and never entered backoff.

    `SessionExpired` has no upstream error to map from — expiry surfaces as
    `worker.session_status()["status"] == "expired"`, and a live 401 is
    `AuthenticationError` (session expired, invalid, or blocked alike).
    """
    # Imported lazily so importing this module never pulls in the GPL browser
    # core (matching `modules/networker/driver.py`).
    from .upstream import errors as upstream

    message = str(exc)
    if isinstance(exc, upstream.RateLimited):
        return RateLimited(message)
    if isinstance(exc, (upstream.ReachedConnectionLimit, upstream.CapExceeded)):
        return InviteCapReached(message)
    if isinstance(exc, upstream.AuthenticationError):
        return AuthenticationError(message)
    if isinstance(exc, upstream.ProfileInaccessibleError):
        return ProfileUnavailable(message)
    return BrowserFailure(message)
