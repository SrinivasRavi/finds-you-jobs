"""Who may start a send, a LinkedIn read or a Submit (audit-2026-10-14.md section 8.10).

AGPL-3.0-only. A click (`requested_by="user"`) always runs. A background caller
(`"app"`: the scheduler or the autopilot) runs an action only if the user switched
that action from "confirm each" to autopilot in Settings. The caller is the op
row's `requested_by`, set by the code that created the row; a snapshot field
proves nothing, because a retry copies it."""

from __future__ import annotations

from typing import Any

# The op kinds that send, submit or read a logged-in LinkedIn session, so each
# one calls `authorize` before it touches a browser.
GATED_KINDS: frozenset[str] = frozenset({"send", "discover", "view_page", "apply"})
# The rest. `contact_sync` and `linkedin_search` read LinkedIn under their own
# switches (the sync schedule, `linkedin_search_enabled`), as before this gate.
UNGATED_KINDS: frozenset[str] = frozenset({
    # `autopilot` only enqueues; each op it enqueues authorizes itself.
    "autopilot", "archive_stale_contacts", "cleanup_trash", "contact_sync", "cover", "draft",
    "extract", "linkedin_login", "linkedin_search", "scan", "score", "tailor",
})

_LABEL = {
    "invite": "a LinkedIn invite",
    "dm": "a LinkedIn message",
    "apply": "an application Submit",
    "linkedin_read": "a LinkedIn search",
}


class NotAuthorized(Exception):
    """A background caller asked for an action the user keeps on "confirm each".
    The reason lands on the op row verbatim."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def refusal(prefs: Any, action: str, requested_by: str) -> str | None:
    """Why this caller may not run `action`, or None if it may. Pure."""
    if requested_by == "user":
        return None
    if requested_by != "app":
        return f"unknown caller {requested_by!r}"
    if action == "view":
        return "the live view opens only on a click"
    if action == "linkedin_read":
        allowed = prefs.autopilot_invite or prefs.autopilot_dm
    elif action in ("invite", "dm", "apply"):
        allowed = getattr(prefs, f"autopilot_{action}")
    else:
        return f"unknown action {action!r}"
    if allowed:
        return None
    return f"autopilot is off for {_LABEL[action]}, so it waits for your click"


def authorize(db: Any, action: str, requested_by: str) -> None:
    """Raise `NotAuthorized` unless this caller may run `action`. A click never
    reads the database."""
    if requested_by == "user":
        return
    if db is None:
        raise NotAuthorized("no database to read the autopilot settings from")
    with db.repos() as repos:
        reason = refusal(repos.preferences.get_or_create(), action, requested_by)
    if reason is not None:
        raise NotAuthorized(reason)
