"""In-memory test doubles for the Networker seams.

`FakeVoyagerDriver` satisfies the `VoyagerDriver` protocol without spawning the
voyager subprocess (mirrors the applier's FakePageDriver / scraper's
FakeFetcher). `FakeEngine` / `BoomEngine` stand in for the LLM. This keeps the
orchestration tests hermetic — no browser, no LinkedIn, no LLM, no network.
"""

from __future__ import annotations

from sidecar.modules.networker.types import NetworkerError, Usage


class FakeVoyagerDriver:
    """Canned voyager responses. Each method returns its canned dict (or raises a
    preset NetworkerError). `closed` proves the orchestrator tore it down."""

    def __init__(
        self,
        *,
        discover_result: dict | None = None,
        resolve_result: dict | None = None,
        connection_result: dict | None = None,
        dm_result: dict | None = None,
        status_result: dict | None = None,
        search_jobs_result: dict | None = None,
        contact_sync_result: dict | None = None,
        quota_result: dict | None = None,
        login_result: dict | None = None,
        session_status_result: dict | None = None,
        resume_result: dict | None = None,
        raise_on: str | None = None,
        error: NetworkerError | None = None,
    ) -> None:
        self._discover = discover_result or {"op": "discover", "ok": True, "contacts": []}
        # Default resolution auto-picks (a domain-website match) so orchestration
        # tests discover without a company-confirm step. Tests exercising the
        # confirm/ambiguous/empty paths pass their own `resolve_result`.
        self._resolve = resolve_result or {
            "op": "resolve-company", "ok": True, "companies": [
                {"urn": "urn:li:fsd_company:100", "company_id": "100", "name": "Northline",
                 "vanity": "northline", "website": "https://ex.co", "domain_match": True},
            ],
        }
        self._connection = connection_result or {"op": "send-connection", "ok": True, "sent": True}
        self._dm = dm_result or {"op": "send-dm", "ok": True, "sent": True}
        self._status = status_result or {"op": "status", "ok": True, "status": "qualified"}
        self._search_jobs = search_jobs_result or {
            "op": "search-jobs", "ok": True, "count": 0, "total": 0, "jobs": [],
        }
        self._contact_sync = contact_sync_result or {
            "op": "contact-sync", "ok": True, "degree": None, "is_first_degree": False,
            "last_message_direction": None, "last_message_at": None,
        }
        self._quota = quota_result or {"op": "quota", "ok": True, "quota": {"daily_remaining": 15}}
        self._login = login_result or {
            "op": "login", "ok": True, "connected": True, "connected_as": "Test User",
            "li_at_expires": None, "cookie_count": 3,
        }
        self._session_status = session_status_result or {
            "op": "session-status", "ok": True, "status": "valid",
            "present": True, "has_auth_cookie": True, "expired": False, "li_at_expires": None,
        }
        self._resume = resume_result or {
            "op": "resume", "ok": True, "quota": {"paused": False, "paused_until": 0.0},
        }
        self._raise_on = raise_on
        self._error = error or NetworkerError("voyager", "boom")
        self.calls: list[tuple] = []
        self.closed = False

    def _maybe_raise(self, name: str) -> None:
        if self._raise_on == name:
            raise self._error

    def resolve_company(
        self, name: str = "", *, url=None, prefer_domain=None, limit: int = 5, dry_run: bool = False
    ) -> dict:
        self.calls.append(("resolve_company", name, url, prefer_domain, limit, dry_run))
        self._maybe_raise("resolve_company")
        return self._resolve

    def discover(
        self, company: str, limit: int, *, company_urn=None, page: int = 1, dry_run: bool = False
    ) -> dict:
        self.calls.append(("discover", company, limit, company_urn, page, dry_run))
        self._maybe_raise("discover")
        return self._discover

    def search_jobs(self, keywords, location="", *, limit: int = 50, dry_run: bool = False) -> dict:
        self.calls.append(("search_jobs", keywords, location, limit, dry_run))
        self._maybe_raise("search_jobs")
        return self._search_jobs

    def send_connection(self, public_identifier, note, tier, *, dry_run) -> dict:
        self.calls.append(("send_connection", public_identifier, note, tier, dry_run))
        self._maybe_raise("send_connection")
        return self._connection

    def send_dm(self, public_identifier, message, tier, *, dry_run) -> dict:
        self.calls.append(("send_dm", public_identifier, message, tier, dry_run))
        self._maybe_raise("send_dm")
        return self._dm

    def status(self, public_identifier, *, dry_run) -> dict:
        self.calls.append(("status", public_identifier, dry_run))
        self._maybe_raise("status")
        return self._status

    def contact_sync(self, public_identifier, *, dry_run) -> dict:
        self.calls.append(("contact_sync", public_identifier, dry_run))
        self._maybe_raise("contact_sync")
        return self._contact_sync

    def quota(self, tier) -> dict:
        self.calls.append(("quota", tier))
        self._maybe_raise("quota")
        return self._quota

    def resume(self) -> dict:
        self.calls.append(("resume",))
        self._maybe_raise("resume")
        return self._resume

    def session_status(self) -> dict:
        self.calls.append(("session_status",))
        self._maybe_raise("session_status")
        return self._session_status

    def login(self, *, login_url=None, timeout_s=300.0, cancel_check=None) -> dict:
        self.calls.append(("login", login_url, timeout_s))
        if cancel_check is not None and cancel_check():
            raise NetworkerError("voyager", "login cancelled by the user")
        self._maybe_raise("login")
        return self._login

    def close(self) -> None:
        self.closed = True


_VALID_DRAFT = (
    "===MESSAGE===\n"
    "Hi Jane, I'm exploring the Senior Backend Engineer role at Acme and would love "
    "to connect.\n"
    "===NOTES===\n"
    "- Used the master's Go + distributed-systems experience.\n"
    "- All claims trace to the master profile.\n"
)


class FakeEngine:
    """Returns a canned draft-contract string + Usage. Records the prompts."""

    def __init__(self, raw: str = _VALID_DRAFT) -> None:
        self.raw = raw
        self.seen: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        self.seen.append((system_prompt, user_prompt))
        return self.raw, Usage(internal_calls=1, tokens_in=100, tokens_out=40,
                               usd=0.01, latency_ms=1200, model="fake")


class BoomEngine:
    """Raises verbatim — proves errors propagate, never half-succeed."""

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Usage]:
        raise NetworkerError("engine", "claude CLI exited 1: rate limit")
