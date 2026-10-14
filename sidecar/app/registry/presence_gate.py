"""The presence gate for logged-in LinkedIn runs (invariant 3, Our Claim 9).

AGPL-3.0-only. LinkedIn traffic happens only when the user asked: every gated
run must carry the `user_initiated` marker only a click sets, and that click is
the presence signal. Requiring an attached viewer and a visible surface was
dropped on 2026-08-14: it gated on attention, not presence; the Browser tab
stays optional observability."""

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
