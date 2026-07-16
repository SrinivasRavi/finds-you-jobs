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

__all__ = [
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
