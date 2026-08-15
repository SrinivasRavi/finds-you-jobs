"""The presence gate for logged-in LinkedIn runs (invariant 3, Our Claim 9).

finds-you-jobs-owned (AGPL-3.0-only). LinkedIn traffic happens only when the
user asked for it: every gated run must carry the `user_initiated` marker that
only an explicit click sets, so a background timer or scheduler can never start
one. That click IS the presence signal.

History (maintainer decision, 2026-08-14): the first live version also required
an attached screencast viewer and a visible surface. Lived experience killed
both signals the same evening they shipped: they gate on *attention*, not
presence, pinning the user to the Browser tab for the whole send while the
composer couldn't even render there. The click already proves the user is
present and chose this send; the Browser tab stays as optional observability,
never a requirement.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PresenceVerdict:
    """The gate's decision plus, when absent, the one reason it refused (surfaced
    verbatim on the operation row)."""

    present: bool
    reason: str = ""


class PresenceAbsent(Exception):
    """The presence gate refused: the run was not user-initiated, so no LinkedIn
    traffic may start (invariant 3). Surfaced verbatim on the op row."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def decide_presence(*, user_initiated: bool) -> PresenceVerdict:
    """The gate's whole logic: present iff the run was user-initiated (an
    explicit click, never a background timer). Pure, so the closure is provable
    without a browser."""
    if not user_initiated:
        return PresenceVerdict(False, "the run was not user-initiated")
    return PresenceVerdict(True)
