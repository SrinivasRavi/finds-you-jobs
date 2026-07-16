"""The `ReferralAutomation` facade contract (`docs/internal/referral-outreach.md` §3.1).

finds-you-jobs-owned (AGPL-3.0-only). This is the narrow, typed, in-process
surface the app's Networker module calls. The concrete browser-driving
implementation over the GPLv3 `upstream/` OpenOutreach core lands with the
direct-outreach commits (`docs/internal/roadmap.md` §7.2 #10–11); this
provenance commit ships the contract, the typed values, and a deterministic
fake so callers and tests can be written against a stable seam.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from .types import (
    AccountRef,
    ConnectionRequest,
    ContactProbeRequest,
    ContactProbeResult,
    DirectMessageRequest,
    DiscoverRequest,
    DiscoverResult,
    Quota,
    ResumeResult,
    SendResult,
    SessionCaptureRequest,
    SessionCaptureResult,
    SessionStatus,
    SessionStatusRequest,
)

# A typed progress-event sink the facade calls during a long operation (e.g. the
# headed-login capture). Payloads are plain dicts; the app forwards them to SSE.
EventSink = Callable[[dict], None]


class ReferralAutomation(Protocol):
    """The one surface the app uses to reach the LinkedIn browser core.

    Every request/result is a plain dataclass (`types.py`); no browser, DB, or
    web types cross this line. Concrete implementations own their own Chromium
    lifecycle (launched per bounded operation, except a headed login that stays
    open while the user logs in) and enforce pacing/caps internally — the app
    mirrors the reported quota, never re-implements it."""

    def capture_session(
        self, request: SessionCaptureRequest, on_event: EventSink
    ) -> SessionCaptureResult: ...

    def session_status(self, request: SessionStatusRequest) -> SessionStatus: ...

    def discover(self, request: DiscoverRequest) -> DiscoverResult: ...

    def send_connection(self, request: ConnectionRequest) -> SendResult: ...

    def send_dm(self, request: DirectMessageRequest) -> SendResult: ...

    def probe_contact(self, request: ContactProbeRequest) -> ContactProbeResult: ...

    def quota(self, account: AccountRef) -> Quota: ...

    def resume_after_backoff(self, account: AccountRef) -> ResumeResult: ...
