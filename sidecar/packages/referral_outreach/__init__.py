"""Referral Outreach package — the LinkedIn networking core.

finds-you-jobs-owned facade (AGPL-3.0-only) over a trimmed, GPLv3
OpenOutreach-derived browser core under `upstream/`. Called DIRECTLY in-process
(no subprocess firewall — retired in this AGPL rebuild; see `provenance.md` and
`docs/internal/referral-outreach.md` §2). GPLv3 + AGPLv3 are compatible for this
combination; the `upstream/` files retain their GPL notices.
"""

from __future__ import annotations

from .client import EventSink, ReferralAutomation
from .fake import FakeReferralAutomation
from .types import (
    AccountRef,
    AuthenticationError,
    BrowserFailure,
    ConnectionRequest,
    ContactProbeRequest,
    ContactProbeResult,
    DirectMessageRequest,
    DiscoveredContact,
    DiscoverRequest,
    DiscoverResult,
    InviteCapReached,
    ProfileUnavailable,
    Quota,
    RateLimited,
    ReferralError,
    ResumeResult,
    SendResult,
    SessionCaptureRequest,
    SessionCaptureResult,
    SessionExpired,
    SessionStatus,
    SessionStatusRequest,
)

# The enforced cap tables (NFR-LI-02). Re-exported so the app derives every
# user-visible cap from the SAME numbers the send path enforces — the app-side
# duplicate table (`dto._TIER_CAPS`) once drifted to 2-3× the enforced values
# (posture doc §4 fix 7). Read-only for the host: it displays these, never
# re-implements or overrides them.
from .upstream.pacing import (
    CEILINGS,
    DEFAULT_MEMBERSHIP,
    DEFAULT_RISK_PCT,
    DEFAULT_TIER,
    MEMBERSHIPS,
    OVERRIDABLE,
    TIERS,
    PacingProfile,
    clamp_risk,
    default_overrides,
    resolve_membership,
    resolve_profile,
    resolve_tier,
)

__all__ = [
    "CEILINGS",
    "DEFAULT_MEMBERSHIP",
    "DEFAULT_RISK_PCT",
    "DEFAULT_TIER",
    "MEMBERSHIPS",
    "OVERRIDABLE",
    "TIERS",
    "PacingProfile",
    "clamp_risk",
    "default_overrides",
    "resolve_membership",
    "resolve_profile",
    "resolve_tier",
    "AccountRef",
    "AuthenticationError",
    "BrowserFailure",
    "ConnectionRequest",
    "ContactProbeRequest",
    "ContactProbeResult",
    "DirectMessageRequest",
    "DiscoverRequest",
    "DiscoverResult",
    "DiscoveredContact",
    "EventSink",
    "FakeReferralAutomation",
    "InviteCapReached",
    "ProfileUnavailable",
    "Quota",
    "RateLimited",
    "ReferralAutomation",
    "ReferralError",
    "ResumeResult",
    "SendResult",
    "SessionCaptureRequest",
    "SessionCaptureResult",
    "SessionExpired",
    "SessionStatus",
    "SessionStatusRequest",
]
