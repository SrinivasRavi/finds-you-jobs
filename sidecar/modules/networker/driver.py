"""Voyager driver: the direct in-process seam behind `VoyagerDriver`.

The orchestrator (`networker.py`) talks only to the protocol, so tests use a
fake; `DirectVoyagerDriver` calls the GPLv3 worker
(`referral_outreach.upstream.worker`) in-process, since an AGPL host can combine
with it (referral-outreach.md section 2). A thin adapter: the worker's dicts
pass through, and `VoyagerError` becomes `NetworkerError`."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .types import NetworkerError

if TYPE_CHECKING:
    from sidecar.packages.referral_outreach.facade import PacingProfile


class VoyagerDriver(Protocol):
    """The surface `networker.py` needs. The in-process worker adapter and the
    test fake both satisfy it. Every method returns the raw voyager JSON dict;
    the orchestrator maps it onto typed results."""

    def resolve_company(
        self,
        name: str = "",
        *,
        url: str | None = None,
        prefer_domain: str | None = None,
        limit: int = 5,
        dry_run: bool = False,
    ) -> dict: ...
    def discover(
        self, company: str, limit: int, *, company_urn: str | None = None,
        page: int = 1, dry_run: bool
    ) -> dict: ...
    def search_jobs(
        self, keywords: str, location: str = "", *, start: int = 0,
        dry_run: bool = False
    ) -> dict: ...
    def send_connection(
        self, public_identifier: str, note: str, *, dry_run: bool,
        on_step: Callable[[str], None] | None = None,
        unconfirmed_since: float | None = None,
    ) -> dict: ...
    def send_dm(
        self, public_identifier: str, message: str, *, dry_run: bool,
        on_step: Callable[[str], None] | None = None,
        unconfirmed_since: float | None = None,
    ) -> dict: ...
    def status(self, public_identifier: str, *, dry_run: bool) -> dict: ...
    def contact_sync_states(
        self, entries: list[dict], *, dry_run: bool
    ) -> dict: ...
    def quota(self) -> dict: ...
    def resume(self) -> dict: ...
    def session_status(self) -> dict: ...
    def login(
        self,
        *,
        login_url: str | None,
        timeout_s: float,
        cancel_check: Callable[[], bool] | None,
    ) -> dict: ...
    def close(self) -> None: ...


class DirectVoyagerDriver:
    """In-process driver, 1 `upstream.worker.<op>()` call per method. The host passes
    `storage_state`, `user_data_dir`, `state_dir`, the pacing profile and `headed`;
    the session key comes from `FYJ_SESSION_KEY` (NFR-SEC-01). `surface_provider`
    runs page actions on the broker's lane instead of its own Chromium."""

    def __init__(
        self,
        *,
        storage_state: str | None = None,
        user_data_dir: str | None = None,
        state_dir: str | None = None,
        pacing_profile: PacingProfile | None = None,
        linkedin_plan: str = "free",
        headed: bool = False,
        env: dict[str, str] | None = None,
        surface_provider: Callable[[str], Any] | None = None,
    ) -> None:
        self.storage_state = storage_state
        self.user_data_dir = user_data_dir
        self.state_dir = state_dir
        # The membership × risk% × override basis (2026-08-01) — drives every cap
        # (the worker computes the numbers from it).
        self.pacing_profile = pacing_profile
        # free|premium — conditions the worker's personalized-note budget
        # (free-only allowance). 'free' is the conservative default.
        self.linkedin_plan = linkedin_plan
        self.headed = headed
        # Applied to os.environ for the duration of a call (e.g. FYJ_SESSION_KEY,
        # which `upstream.secure_store` reads to seal/open the storage-state).
        self.env = env
        # The host's hook to the core browser broker: given the referral surface
        # slug (a runtime argument owned inside the GPL package, never named
        # here), return that surface's ready handle. When set, the worker runs
        # every page-driving action on the broker's serialized lane instead of
        # launching its own Chromium; None keeps the self-launch path.
        self.surface_provider = surface_provider
        self.invocations = 0

    def _worker(self):  # type: ignore[no-untyped-def]
        # Through the F-P10 facade so this core module never imports `upstream.*`
        # directly. Still lazy (the accessor imports the worker on first use), so
        # a caller that only builds the driver never pulls the GPL browser core
        # (and its playwright dep) in until an op runs.
        from sidecar.packages.referral_outreach.facade import worker_module

        return worker_module()

    def _call(self, fn: Callable[..., dict], /, **kwargs: Any) -> dict:
        """Run a worker op with `self.env` applied to the process env, translating
        a worker `VoyagerError` into the module's typed `NetworkerError`."""
        from sidecar.packages.referral_outreach.facade import VoyagerError

        self.invocations += 1
        prior: dict[str, str | None] = {}
        if self.env:
            for key, value in self.env.items():
                prior[key] = os.environ.get(key)
                os.environ[key] = value
        try:
            return fn(**kwargs)
        except VoyagerError as e:
            raise NetworkerError("voyager", str(e)) from e
        finally:
            for key, old in prior.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old

    # --- VoyagerDriver surface ---

    def resolve_company(
        self,
        name: str = "",
        *,
        url: str | None = None,
        prefer_domain: str | None = None,
        limit: int = 5,
        dry_run: bool = False,
    ) -> dict:
        return self._call(
            self._worker().resolve_company,
            keywords=name,
            url=url,
            limit=limit,
            prefer_domain=prefer_domain,
            profile=self.pacing_profile,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def discover(
        self, company: str, limit: int, *, company_urn: str | None = None,
        page: int = 1, dry_run: bool = False
    ) -> dict:
        return self._call(
            self._worker().discover,
            company=company,
            limit=limit,
            page=page,
            company_urn=company_urn,
            profile=self.pacing_profile,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def search_jobs(
        self, keywords: str, location: str = "", *, start: int = 0,
        dry_run: bool = False
    ) -> dict:
        return self._call(
            self._worker().search_jobs,
            keywords=keywords,
            location=location,
            start=start,
            profile=self.pacing_profile,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def send_connection(
        self, public_identifier: str, note: str, *, dry_run: bool,
        on_step: Callable[[str], None] | None = None,
        unconfirmed_since: float | None = None,
    ) -> dict:
        return self._call(
            self._worker().send_connection,
            unconfirmed_since=unconfirmed_since,
            public_identifier=public_identifier,
            note=note,
            on_step=on_step,
            profile=self.pacing_profile,
            linkedin_plan=self.linkedin_plan,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def send_dm(
        self, public_identifier: str, message: str, *, dry_run: bool,
        on_step: Callable[[str], None] | None = None,
        unconfirmed_since: float | None = None,
    ) -> dict:
        return self._call(
            self._worker().send_dm,
            unconfirmed_since=unconfirmed_since,
            public_identifier=public_identifier,
            message=message,
            on_step=on_step,
            profile=self.pacing_profile,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def status(self, public_identifier: str, *, dry_run: bool) -> dict:
        return self._call(
            self._worker().status,
            public_identifier=public_identifier,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def contact_sync_states(
        self, entries: list[dict], *, dry_run: bool
    ) -> dict:
        """Batched read-only status probes in 1 browser session. Each entry is
        `{public_identifier, urn, thread_only}`: thread-only with a cached urn is free
        from the sweep's inbox read; the rest are charged full probes."""
        return self._call(
            self._worker().contact_sync_states,
            entries=entries,
            profile=self.pacing_profile,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            surface_provider=self.surface_provider,
            dry_run=dry_run,
        )

    def quota(self) -> dict:
        return self._call(
            self._worker().quota,
            profile=self.pacing_profile,
            state_dir=self.state_dir,
        )

    def resume(self) -> dict:
        """Clear the pacing backoff pause (FR-NW-05 manual resume). Local ledger
        only — no browser, no network."""
        return self._call(
            self._worker().resume, profile=self.pacing_profile,
            state_dir=self.state_dir
        )

    def session_status(self) -> dict:
        """LOCAL session validity — `li_at` presence/expiry from the saved
        storage-state file. No browser, no network (validate-without-LinkedIn)."""
        return self._call(
            self._worker().session_status, storage_state=self.storage_state
        )

    def login(
        self,
        *,
        login_url: str | None = None,
        timeout_s: float = 300.0,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict:
        """Open a headed browser and wait up to `timeout_s` for the user to log in, then
        save the storage state; the password is never handled here. `cancel_check` is
        for protocol symmetry (referral-outreach.md section 3.2)."""
        if not self.storage_state:
            raise NetworkerError(
                "voyager", "login requires a storage_state path to save the session"
            )
        return self._call(
            self._worker().login,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            login_url=login_url,
            timeout_s=timeout_s,
        )

    def close(self) -> None:
        # Each op opens/closes its own browser inside the worker call — nothing
        # persistent to tear down. Present for Protocol symmetry.
        return None


def default_driver(*, state_dir: str | None = None) -> DirectVoyagerDriver:
    """A no-config driver for the module CLI / standalone dogfood. The app builds
    its own driver with the app-data storage paths (see `registry/networker_ops`)."""
    root = Path(state_dir) if state_dir else None
    return DirectVoyagerDriver(state_dir=str(root) if root else None)
