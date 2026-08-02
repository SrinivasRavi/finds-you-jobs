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

from sidecar.packages.referral_outreach import types as facade
from sidecar.packages.referral_outreach.driver import _translate_error
from sidecar.packages.referral_outreach.types import RateLimited as FacadeRateLimited
from sidecar.packages.referral_outreach.upstream import errors as upstream_errors
from sidecar.packages.referral_outreach.upstream.client import (
    PlaywrightLinkedinAPI,
    _FetchResponse,
)
from sidecar.packages.referral_outreach.upstream.errors import RateLimited


def _res(status: int) -> _FetchResponse:
    return _FetchResponse({"status": status, "ok": 200 <= status < 300, "body": ""})


@pytest.mark.parametrize("status", [429, 999])
def test_throttle_statuses_raise_rate_limited(status: int) -> None:
    with pytest.raises(RateLimited) as exc:
        PlaywrightLinkedinAPI.raise_if_throttled(_res(status))
    assert str(status) in str(exc.value)


@pytest.mark.parametrize("status", [200, 401, 403, 404, 500, 503])
def test_other_statuses_are_left_to_their_own_handlers(status: int) -> None:
    # Deliberately narrow: 401 is auth, 403/404 is inaccessible, 500/503 are
    # genuine transients worth the bounded OSError retry — LinkedIn serves 503
    # for ordinary shed load, and one blip must not trigger a 24 h full stop
    # across every meter. Only real throttle statuses escape the retry loop.
    PlaywrightLinkedinAPI.raise_if_throttled(_res(status))


def test_rate_limited_is_not_an_oserror_so_it_escapes_the_retry_loop() -> None:
    """`_retry_io` retries `OSError`. If `RateLimited` were one we would retry
    into the block — the exact bug this replaced."""
    assert not issubclass(RateLimited, OSError)


@pytest.mark.parametrize("status", [429, 999])
def test_a_throttle_status_translates_to_the_facade_rate_limit_type(status: int) -> None:
    """End-to-end: what `raise_if_throttled` raises must land on the facade's
    `RateLimited`. The facade used to match on message text, so a 999 matched
    none of the rate-limit tokens and fell through to `BrowserFailure` —
    it never entered backoff."""
    with pytest.raises(RateLimited) as exc:
        PlaywrightLinkedinAPI.raise_if_throttled(_res(status))
    assert isinstance(_translate_error(exc.value), FacadeRateLimited)


@pytest.mark.parametrize(
    ("upstream_error", "facade_error"),
    [
        (upstream_errors.RateLimited, facade.RateLimited),
        (upstream_errors.ReachedConnectionLimit, facade.InviteCapReached),
        (upstream_errors.CapExceeded, facade.InviteCapReached),
        (upstream_errors.AuthenticationError, facade.AuthenticationError),
        (upstream_errors.ProfileInaccessibleError, facade.ProfileUnavailable),
        (upstream_errors.SkipProfile, facade.BrowserFailure),
        (upstream_errors.VoyagerError, facade.BrowserFailure),
        (RuntimeError, facade.BrowserFailure),
    ],
)
def test_translation_is_typed_not_word_matched(
    upstream_error: type[Exception], facade_error: type[facade.ReferralError]
) -> None:
    """Classification reads the upstream error TYPE, so a message that carries
    none of the old tokens still lands on the right facade type (and a wording
    change upstream can no longer silently re-route an error)."""
    translated = _translate_error(upstream_error("no tokens in this message"))
    assert isinstance(translated, facade_error)
    assert str(translated) == "no tokens in this message"
