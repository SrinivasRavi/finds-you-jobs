"""The concrete `ReferralAutomation` — drives the GPLv3 `upstream/` core in-process.

finds-you-jobs-owned (AGPL-3.0-only). This is the replacement for the prior
repository's `SubprocessVoyagerDriver`: instead of spawning `python -m
voyager_py <command>` and parsing one JSON object off stdout, it imports the
OpenOutreach-derived operation layer (`upstream/worker.py`) and calls it
directly, translating its dict envelopes into the facade's typed values
(`docs/internal/referral-outreach.md` §2/§3). No subprocess, no JSON-CLI.

Browser ops (`capture_session`, `discover`, `send_*`, `probe_contact`) launch
Chromium through the upstream session; `session_status`/`quota`/`resume` are
local-only (no browser, no network). The upstream layer owns pacing/caps/backoff
and returns the live quota; this facade never re-implements them.
"""

from __future__ import annotations

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


def _quota_from_dict(raw: dict | None) -> Quota | None:
    if not raw:
        return None
    return Quota(
        daily_sent=int(raw.get("daily_sent", 0)),
        daily_cap=int(raw.get("daily_cap", 0)),
        weekly_sent=int(raw.get("weekly_sent", 0)),
        weekly_cap=int(raw.get("weekly_cap", 0)),
        paused_until=str(raw.get("paused_until") or ""),
    )


def _translate_error(exc: Exception) -> ReferralError:
    """Map an upstream error onto a facade-typed error, keeping only its safe
    message (never cookies/tokens/state). Matched by message text so the GPL
    `upstream.errors` types stay an implementation detail of this file."""
    message = str(exc)
    low = message.lower()
    if "rate" in low or "429" in low or "too many" in low:
        return RateLimited(message)
    if "cap" in low or "limit reached" in low or "connection limit" in low:
        return InviteCapReached(message)
    if "expired" in low:
        return SessionExpired(message)
    if "auth" in low or "li_at" in low or "logged in" in low or "login" in low:
        return AuthenticationError(message)
    if "profile" in low and ("not found" in low or "unavailable" in low):
        return ProfileUnavailable(message)
    return BrowserFailure(message)


class VoyagerReferralAutomation:
    """Direct in-process facade over `upstream/worker.py`.

    `state_dir` is where the upstream pacing ledger lives (app-owned path). Each
    method opens/closes its own browser inside the upstream call; the facade
    itself holds no long-lived browser.
    """

    def __init__(
        self, *, storage_state_path: str, state_dir: str, headed: bool = False
    ) -> None:
        self._storage_state = storage_state_path
        self._state_dir = state_dir
        self._headed = headed

    # -- session (local; no browser except capture) ------------------------

    def capture_session(
        self, request: SessionCaptureRequest, on_event
    ) -> SessionCaptureResult:  # type: ignore[no-untyped-def]
        from .upstream import worker

        on_event({"phase": "opening_login_browser"})
        try:
            raw = worker.login(
                storage_state=request.storage_state_path,
                login_url=request.login_url,
                timeout_s=request.timeout_s,
            )
        except Exception as exc:  # noqa: BLE001 — translated to a typed facade error
            raise _translate_error(exc) from exc
        on_event({"phase": "captured"})
        return SessionCaptureResult(
            ok=bool(raw.get("ok")),
            connected_as=str(raw.get("connected_as") or ""),
            detail=str(raw.get("detail") or ""),
        )

    def session_status(self, request: SessionStatusRequest) -> SessionStatus:
        from .upstream import worker

        raw = worker.session_status(storage_state=request.storage_state_path)
        status = str(raw.get("status") or "never_set")
        return SessionStatus(
            valid=status == "valid",
            expires_at=str(raw.get("li_at_expires_at") or ""),
            detail=status,
        )

    # -- discovery / sending (browser) -------------------------------------

    def discover(self, request: DiscoverRequest) -> DiscoverResult:
        from .upstream import worker

        try:
            raw = worker.discover(
                request.company_urn,
                limit=request.limit,
                company_urn=request.company_urn,
                storage_state=self._storage_state,
                headed=self._headed,
            )
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc) from exc
        contacts = [
            DiscoveredContact(
                public_identifier=str(c.get("public_identifier") or ""),
                full_name=str(c.get("full_name") or ""),
                headline=str(c.get("headline") or ""),
                current_title=str(c.get("current_title") or ""),
                url=str(c.get("url") or ""),
                connection_degree=c.get("connection_degree"),
            )
            for c in (raw.get("contacts") or [])
        ]
        return DiscoverResult(contacts=contacts, internal_calls=1)

    def send_connection(self, request: ConnectionRequest) -> SendResult:
        from .upstream import worker

        try:
            raw = worker.send_connection(
                request.public_identifier,
                note=request.note,
                state_dir=self._state_dir,
                storage_state=self._storage_state,
                headed=self._headed,
                dry_run=request.dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc) from exc
        return SendResult(
            ok=bool(raw.get("ok")),
            detail=str(raw.get("detail") or ""),
            quota=_quota_from_dict(raw.get("quota")),
        )

    def send_dm(self, request: DirectMessageRequest) -> SendResult:
        from .upstream import worker

        try:
            raw = worker.send_dm(
                request.public_identifier,
                request.message,
                state_dir=self._state_dir,
                storage_state=self._storage_state,
                headed=self._headed,
                dry_run=request.dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc) from exc
        return SendResult(
            ok=bool(raw.get("ok")),
            detail=str(raw.get("detail") or ""),
            quota=_quota_from_dict(raw.get("quota")),
        )

    def probe_contact(self, request: ContactProbeRequest) -> ContactProbeResult:
        from .upstream import worker

        try:
            raw = worker.status(
                request.public_identifier,
                storage_state=self._storage_state,
                headed=self._headed,
            )
        except Exception as exc:  # noqa: BLE001
            raise _translate_error(exc) from exc
        return ContactProbeResult(
            connection_status=str(raw.get("status") or "unknown"),
            detail=str(raw.get("detail") or ""),
        )

    # -- pacing (local) ----------------------------------------------------

    def quota(self, account: AccountRef) -> Quota:
        from .upstream import worker

        raw = worker.quota(tier=account.tier, state_dir=self._state_dir)
        return _quota_from_dict(raw.get("quota")) or Quota()

    def resume_after_backoff(self, account: AccountRef) -> ResumeResult:
        from .upstream import worker

        raw = worker.resume(tier=account.tier, state_dir=self._state_dir)
        return ResumeResult(ok=bool(raw.get("ok")))
