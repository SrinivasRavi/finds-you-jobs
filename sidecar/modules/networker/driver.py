"""Voyager driver — the DIRECT in-process seam behind the `VoyagerDriver` protocol.

The orchestrator (`networker.py`) talks only to the `VoyagerDriver` interface,
so discover/send/status are tested against an in-memory fake, while
`DirectVoyagerDriver` calls the GPLv3 OpenOutreach-derived worker
(`sidecar.packages.referral_outreach.upstream.worker`) DIRECTLY, in-process.

This replaces the prior repository's `SubprocessVoyagerDriver`, which spawned
`python -m voyager_py <command>` and parsed JSON over stdout to keep GPL code
off an MIT host. finds-you-jobs is AGPL-3.0-only, so GPLv3 + AGPLv3 combine
directly and the subprocess firewall is retired
(`docs/internal/referral-outreach.md` §2). The `upstream.worker` functions
already return the exact dict envelopes this protocol expects, so this driver is
a thin faithful adapter — it translates a worker `VoyagerError` into the
module's typed `NetworkerError`, exactly as the subprocess driver did.

The typed `ReferralAutomation` facade (`sidecar.packages.referral_outreach`) is
the package's narrow public contract for external callers; the app's Networker
path uses this dict driver, which matches the module's existing seam with zero
reconciliation drift.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .types import NetworkerError


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
    def send_connection(
        self, public_identifier: str, note: str, tier: str | None, *, dry_run: bool
    ) -> dict: ...
    def send_dm(
        self, public_identifier: str, message: str, tier: str | None, *, dry_run: bool
    ) -> dict: ...
    def status(self, public_identifier: str, *, dry_run: bool) -> dict: ...
    def contact_sync(self, public_identifier: str, *, dry_run: bool) -> dict: ...
    def quota(self, tier: str | None) -> dict: ...
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
    """In-process driver: one `upstream.worker.<op>()` call per method.

    Config the host owns and passes in: the saved cookie file (`storage_state`),
    the persistent Chromium profile dir (`user_data_dir`), the pacing-ledger dir
    (`state_dir`), the account tier, and `headed`. The session-store encryption
    key is read by `upstream.secure_store` from the `FYJ_SESSION_KEY` env var
    (NFR-SEC-01) — the host sets it in the process env before a browser op.
    `dry_run` on any call forwards to the worker so no browser/network is touched.
    """

    def __init__(
        self,
        *,
        storage_state: str | None = None,
        user_data_dir: str | None = None,
        state_dir: str | None = None,
        tier: str | None = None,
        headed: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        self.storage_state = storage_state
        self.user_data_dir = user_data_dir
        self.state_dir = state_dir
        self.tier = tier
        self.headed = headed
        # Applied to os.environ for the duration of a call (e.g. FYJ_SESSION_KEY,
        # which `upstream.secure_store` reads to seal/open the storage-state).
        self.env = env
        self.invocations = 0

    def _worker(self):  # type: ignore[no-untyped-def]
        # Imported lazily so a caller that only builds the driver never imports
        # the GPL browser core (and its playwright dep) until an op runs.
        from sidecar.packages.referral_outreach.upstream import worker

        return worker

    def _call(self, fn: Callable[..., dict], /, **kwargs: Any) -> dict:
        """Run a worker op with `self.env` applied to the process env, translating
        a worker `VoyagerError` into the module's typed `NetworkerError`."""
        from sidecar.packages.referral_outreach.upstream.errors import VoyagerError

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
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
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
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            dry_run=dry_run,
        )

    def send_connection(
        self, public_identifier: str, note: str, tier: str | None, *, dry_run: bool
    ) -> dict:
        return self._call(
            self._worker().send_connection,
            public_identifier=public_identifier,
            note=note,
            tier=tier if tier is not None else self.tier,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            dry_run=dry_run,
        )

    def send_dm(
        self, public_identifier: str, message: str, tier: str | None, *, dry_run: bool
    ) -> dict:
        return self._call(
            self._worker().send_dm,
            public_identifier=public_identifier,
            message=message,
            tier=tier if tier is not None else self.tier,
            state_dir=self.state_dir,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            dry_run=dry_run,
        )

    def status(self, public_identifier: str, *, dry_run: bool) -> dict:
        return self._call(
            self._worker().status,
            public_identifier=public_identifier,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            dry_run=dry_run,
        )

    def contact_sync(self, public_identifier: str, *, dry_run: bool) -> dict:
        """Read-only contact-status probe (FR-NW-15): degree + last-message
        direction/timestamp. No caps decrement (a read, not a send)."""
        return self._call(
            self._worker().contact_sync,
            public_identifier=public_identifier,
            storage_state=self.storage_state,
            user_data_dir=self.user_data_dir,
            headed=self.headed,
            dry_run=dry_run,
        )

    def quota(self, tier: str | None) -> dict:
        return self._call(
            self._worker().quota,
            tier=tier if tier is not None else self.tier,
            state_dir=self.state_dir,
        )

    def resume(self) -> dict:
        """Clear the pacing backoff pause (FR-NW-05 manual resume). Local ledger
        only — no browser, no network."""
        return self._call(
            self._worker().resume, tier=self.tier, state_dir=self.state_dir
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
        """Open a **headed** browser and wait (up to `timeout_s`) for the user to
        finish logging in, then persist the storage-state. The password is never
        handled here.

        `cancel_check` is accepted for protocol symmetry; a blocking in-process
        headed login is bounded by `timeout_s` rather than an interrupting poll
        (a token-based cancel is a follow-on — see `referral-outreach.md` §3.2).
        """
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
