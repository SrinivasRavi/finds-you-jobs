# voyager_py/tests/test_throttle_backoff.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""HTTP 429 / 999 must reach the pacer's backoff instead of being retried.

Both used to become a plain `OSError`, which `_retry_io` catches and retries
three times — so we re-requested straight into an explicit refusal, and
`RateLimited` was raised nowhere in the codebase, leaving the 24 h backoff
(NFR-LI-03) unreachable. 999 is LinkedIn's own non-standard anti-bot status.
See `docs/internal/linkedin-posture.md` §1.
"""

from __future__ import annotations

import pytest

from sidecar.packages.referral_outreach.driver import _translate_error
from sidecar.packages.referral_outreach.types import RateLimited as FacadeRateLimited
from sidecar.packages.referral_outreach.upstream.client import (
    PlaywrightLinkedinAPI,
    _FetchResponse,
)
from sidecar.packages.referral_outreach.upstream.errors import RateLimited


def _res(status: int) -> _FetchResponse:
    return _FetchResponse({"status": status, "ok": 200 <= status < 300, "body": ""})


@pytest.mark.parametrize("status", [429, 999, 503])
def test_throttle_statuses_raise_rate_limited(status: int) -> None:
    with pytest.raises(RateLimited) as exc:
        PlaywrightLinkedinAPI.raise_if_throttled(_res(status))
    assert str(status) in str(exc.value)


@pytest.mark.parametrize("status", [200, 401, 403, 404, 500])
def test_other_statuses_are_left_to_their_own_handlers(status: int) -> None:
    # Deliberately narrow: 401 is auth, 403/404 is inaccessible, 500 is a genuine
    # transient worth retrying. Only throttles escape the retry loop.
    PlaywrightLinkedinAPI.raise_if_throttled(_res(status))


def test_rate_limited_is_not_an_oserror_so_it_escapes_the_retry_loop() -> None:
    """`_retry_io` retries `OSError`. If `RateLimited` were one we would retry
    into the block — the exact bug this replaced."""
    assert not issubclass(RateLimited, OSError)


def test_a_999_message_translates_to_the_facade_rate_limit_type() -> None:
    """The facade matches on message text. A 999 matched none of the rate-limit
    tokens and fell through to `BrowserFailure`, so it never entered backoff."""
    translated = _translate_error(RateLimited("LinkedIn returned HTTP 999 (throttled/blocked)"))
    assert isinstance(translated, FacadeRateLimited)
