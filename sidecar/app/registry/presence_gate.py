"""The presence gate for logged-in LinkedIn runs (invariant 3, Our Claim 9).

finds-you-jobs-owned (AGPL-3.0-only). LinkedIn traffic happens only with the
user present. Under a headless streamed surface there's no OS window to hide, so
"present" is read from three signals that all have to hold:

- the run was user-initiated (an explicit click, never a background timer),
- a screencast viewer is attached to the surface (the broker's own tracking, a
  signal the page cannot fake),
- the surface reports itself visible (`document.visibilityState == "visible"`).

`document.hasFocus()` is deliberately NOT trusted. Playwright's focus emulation
makes every page claim focus whether it holds it or not, and a genuinely hidden
tab still returns `hasFocus() == True` (Our Finding 14). The honest signal is
`visibilityState`, which reports "hidden" truthfully (Our Finding 13), so the
gate reads only that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..logging_setup import get_logger

# `document.visibilityState` when the surface is foreground.
VISIBLE = "visible"
# How long the gate waits to read the surface's own visibility before it fails
# closed. A live surface answers a one-line evaluate in milliseconds.
VISIBILITY_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class PresenceVerdict:
    """The gate's decision plus, when absent, the one reason it refused (surfaced
    verbatim on the operation row)."""

    present: bool
    reason: str = ""


class PresenceAbsent(Exception):
    """The presence gate refused: the user is not present for this run, so no
    LinkedIn traffic may start (invariant 3). Surfaced verbatim on the op row."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class PresenceSurface(Protocol):
    """The slice of a browser surface the gate reads."""

    @property
    def has_viewer(self) -> bool: ...

    def visibility(self) -> Any:  # a concurrent.futures.Future[dict]
        ...


def decide_presence(
    *, user_initiated: bool, viewer_attached: bool, visibility_state: str | None
) -> PresenceVerdict:
    """The gate's whole logic, over the three signals: present iff the run was
    user-initiated AND a viewer is attached AND the surface is visible. Absent on
    the first failing signal, with the reason. Pure, so the closure is provable
    without a browser."""
    if not user_initiated:
        return PresenceVerdict(False, "the run was not user-initiated")
    if not viewer_attached:
        return PresenceVerdict(False, "no screencast viewer is attached to the surface")
    if visibility_state != VISIBLE:
        return PresenceVerdict(
            False, f"the surface is not visible (visibilityState={visibility_state!r})"
        )
    return PresenceVerdict(True)


def read_surface_presence(
    surface: PresenceSurface,
    *,
    user_initiated: bool,
    timeout: float = VISIBILITY_TIMEOUT_SECONDS,
) -> PresenceVerdict:
    """Read the live signals off a surface and decide. Reads only
    `visibilityState`, never `hasFocus()` (Our Finding 14). Any failure reading
    visibility fails closed — an unreadable surface is treated as not visible."""
    viewer_attached = bool(getattr(surface, "has_viewer", False))
    try:
        state = surface.visibility().result(timeout=timeout)
        visibility_state = state.get("visibilityState") if isinstance(state, dict) else None
    except Exception:  # noqa: BLE001 — an unreadable surface fails closed, never open
        get_logger().warning("presence gate: reading surface visibility failed", exc_info=True)
        visibility_state = None
    return decide_presence(
        user_initiated=user_initiated,
        viewer_attached=viewer_attached,
        visibility_state=visibility_state,
    )
