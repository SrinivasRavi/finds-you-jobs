"""Typed request/result values + errors for the Referral Outreach facade.

finds-you-jobs-owned (AGPL-3.0-only). These are the plain dataclasses the
`ReferralAutomation` facade speaks (`docs/internal/referral-outreach.md` §3.1) —
no SQLAlchemy, no FastAPI, no browser types cross this line. The
OpenOutreach-derived browser core lives under `upstream/` (GPLv3) and is reached
only through the facade's concrete implementation, never by callers.

Errors carry a safe `message` for the UI — never cookies, access tokens, or
saved storage state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Account / session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountRef:
    """Identifies the single local LinkedIn account (P1 is single-user)."""

    storage_state_path: str
    tier: str = "new"  # new | seasoned


@dataclass(frozen=True)
class SessionCaptureRequest:
    """Open a headed browser for the user to log in; capture the session. The
    facade never handles the password — the user logs in themselves."""

    storage_state_path: str
    login_url: str | None = None  # a LOCAL fixture override for tests
    timeout_s: float = 300.0


@dataclass
class SessionCaptureResult:
    ok: bool
    connected_as: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SessionStatusRequest:
    storage_state_path: str


@dataclass
class SessionStatus:
    """A LOCAL cookie-file check — never a live LinkedIn validation."""

    valid: bool
    expires_at: str = ""  # ISO 8601 or ""
    detail: str = ""


# ---------------------------------------------------------------------------
# Discovery / sending
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoverRequest:
    company_urn: str
    limit: int = 10


@dataclass
class DiscoveredContact:
    public_identifier: str
    full_name: str = ""
    headline: str = ""
    current_title: str = ""
    url: str = ""
    connection_degree: int | None = None


@dataclass
class DiscoverResult:
    contacts: list[DiscoveredContact] = field(default_factory=list)
    internal_calls: int = 0


@dataclass(frozen=True)
class ConnectionRequest:
    public_identifier: str
    note: str = ""
    dry_run: bool = False


@dataclass(frozen=True)
class DirectMessageRequest:
    public_identifier: str
    message: str
    dry_run: bool = False


@dataclass
class SendResult:
    ok: bool
    detail: str = ""
    quota: Quota | None = None


@dataclass(frozen=True)
class ContactProbeRequest:
    """Read-only connection-status probe (contact-status sync)."""

    public_identifier: str


@dataclass
class ContactProbeResult:
    connection_status: str  # e.g. not_connected | pending | connected
    detail: str = ""


# ---------------------------------------------------------------------------
# Pacing / quota
# ---------------------------------------------------------------------------


@dataclass
class Quota:
    """Live pacing state — enforced inside the package, mirrored by the app."""

    daily_sent: int = 0
    daily_cap: int = 0
    weekly_sent: int = 0
    weekly_cap: int = 0
    paused_until: str = ""  # ISO 8601 or "" when not backing off


@dataclass
class ResumeResult:
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Typed errors — safe messages only, never secrets
# ---------------------------------------------------------------------------


class ReferralError(Exception):
    """Base for facade errors. `message` is UI-safe (no cookies/tokens/state)."""


class AuthenticationError(ReferralError):
    """The session is not authenticated (no valid `li_at`)."""


class SessionExpired(ReferralError):
    """A previously valid session has expired."""


class RateLimited(ReferralError):
    """LinkedIn rate-limited the account; a backoff is now in effect."""


class InviteCapReached(ReferralError):
    """The configured rolling invite cap is exhausted for now."""


class ProfileUnavailable(ReferralError):
    """The target profile could not be loaded."""


class BrowserFailure(ReferralError):
    """The local browser automation failed (crash, timeout, disconnect)."""
