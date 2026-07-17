# jobapplier.upstream.exceptions — AGPL-3.0 subtree (see LICENSE).
# SPDX-License-Identifier: AGPL-3.0-only
#
# Adapted from Skyvern @ 28db09cb (skyvern/exceptions.py). Only the exception
# classes the carried observation core references are taken: the SkyvernException
# base and FailedToTakeScreenshot (raised by the viewport-screenshot helper in
# page_utils.py). The bodies are carried verbatim.
#
# Trims: none beyond the class-level selection above.
"""Exceptions referenced by the Skyvern-derived observation core."""

from __future__ import annotations


class SkyvernException(Exception):
    def __init__(self, message: str | None = None):
        self.message = message
        super().__init__(message)

    @property
    def user_facing_type_name(self) -> str:
        # Class name safe to render in a user-facing message. Subclasses whose real class
        # name carries sensitive info (e.g. a remote-browser vendor identity) override this
        # so the concrete name stays in logs/monitoring but never reaches end users.
        return type(self).__name__


class FailedToTakeScreenshot(SkyvernException):
    def __init__(self, error_message: str) -> None:
        super().__init__(f"Failed to take screenshot. Error message: {error_message}")
