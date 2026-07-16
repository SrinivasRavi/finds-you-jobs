# voyager_py/errors.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Forked from OpenOutreach `linkedin/exceptions.py` @ a7a9101, with the
# names finds-you-jobs's worker layer needs. RateLimited is added here (the
# upstream signalled rate-limit via ReachedConnectionLimit / IOError on the
# messaging path); we name it explicitly so the CLI can emit a distinct
# `rate_limited` outcome that the host maps to voyager-owned backoff (FR-NW-05,
# NFR-LI-03).
"""Typed voyager errors. Messages carry the verbatim underlying cause; the CLI
serialises them into the JSON error envelope, never swallowing them."""

from __future__ import annotations


class VoyagerError(Exception):
    """Base class for all voyager worker failures."""


class AuthenticationError(VoyagerError):
    """LinkedIn returned 401 — session expired, invalid, or blocked."""


class ProfileInaccessibleError(VoyagerError):
    """Profile is private, deleted, or restricted (HTTP 403/404)."""


class SkipProfile(VoyagerError):
    """The profile cannot be acted on at this stage — caller skips it."""


class ReachedConnectionLimit(VoyagerError):
    """A weekly/daily invitation cap surfaced (LinkedIn's own limit UI)."""


class RateLimited(VoyagerError):
    """LinkedIn returned a rate-limit / restriction signal. The worker pauses
    (voyager-owned backoff) and the host surfaces this verbatim (FR-NW-05)."""


class BrowserUnresponsiveError(OSError):
    """The Python-side watchdog fired because Playwright did not return in time.

    Subclasses OSError so the retry loop in `client.py` picks it up
    automatically (mirrors upstream's IOError-based tenacity retry)."""


class CapExceeded(VoyagerError):
    """A send was refused *by voyager_py's own caps* before touching the
    network (NFR-LI-02: caps owned + enforced inside this subprocess)."""
