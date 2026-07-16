"""A deterministic fake `ReferralAutomation` — no browser, no network.

finds-you-jobs-owned (AGPL-3.0-only). This satisfies the `ReferralAutomation`
Protocol with canned typed results so the Networker module, its operation
entrypoints, and their tests can be written and verified before (and alongside)
the real browser-driving implementation, and so CI never touches LinkedIn.

The scripted responses are set on the instance; unset ones return safe defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .types import (
    AccountRef,
    ConnectionRequest,
    ContactProbeRequest,
    ContactProbeResult,
    DirectMessageRequest,
    DiscoveredContact,
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


@dataclass
class FakeReferralAutomation:
    """Scriptable in-memory facade. Records calls for assertions."""

    session_valid: bool = True
    connected_as: str = "Test User"
    contacts: list[DiscoveredContact] = field(default_factory=list)
    send_ok: bool = True
    probe_status: str = "not_connected"
    quota_value: Quota = field(default_factory=lambda: Quota(daily_cap=15, weekly_cap=100))
    calls: list[tuple[str, object]] = field(default_factory=list)

    def capture_session(self, request, on_event) -> SessionCaptureResult:  # type: ignore[no-untyped-def]
        self.calls.append(("capture_session", request))
        on_event({"phase": "capturing"})
        assert isinstance(request, SessionCaptureRequest)
        return SessionCaptureResult(
            ok=self.session_valid, connected_as=self.connected_as
        )

    def session_status(self, request: SessionStatusRequest) -> SessionStatus:
        self.calls.append(("session_status", request))
        return SessionStatus(valid=self.session_valid)

    def discover(self, request: DiscoverRequest) -> DiscoverResult:
        self.calls.append(("discover", request))
        return DiscoverResult(contacts=list(self.contacts[: request.limit]), internal_calls=1)

    def send_connection(self, request: ConnectionRequest) -> SendResult:
        self.calls.append(("send_connection", request))
        return SendResult(ok=self.send_ok, quota=self.quota_value)

    def send_dm(self, request: DirectMessageRequest) -> SendResult:
        self.calls.append(("send_dm", request))
        return SendResult(ok=self.send_ok, quota=self.quota_value)

    def probe_contact(self, request: ContactProbeRequest) -> ContactProbeResult:
        self.calls.append(("probe_contact", request))
        return ContactProbeResult(connection_status=self.probe_status)

    def quota(self, account: AccountRef) -> Quota:
        self.calls.append(("quota", account))
        return self.quota_value

    def resume_after_backoff(self, account: AccountRef) -> ResumeResult:
        self.calls.append(("resume_after_backoff", account))
        return ResumeResult(ok=True)
