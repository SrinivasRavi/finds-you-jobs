"""Contact-status sync (US-NW-12 / FR-NW-15): a read-only sweep that moves each
contact's column to match LinkedIn, in 1 browser session, user-initiated only.

Transitions: Sent to Accepted (1st degree, ours last) or to a pending column
(theirs last); quiet threads ghost past their windows. Manual wins: Converted
is never touched, and a recent manual move freezes the status (a frozen row
with a urn still refreshes its display). Only `sent` and urn-less rows pay a
charged read; refused rows surface as `stopped`. A read thread writes
`last_thread_message`. No-ops while Referral Outreach is off or the session
isn't valid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sidecar.modules.networker import ProbeResult, probe_batch
from sidecar.modules.networker.types import NetworkerError

from ..db.base import now_utc
from ..db.models import CONTACT_ENGAGED_STATUSES
from ..lifecycle import MANUAL_OVERRIDE_COOLDOWN_DAYS, resolve_lifecycle
from ..logging_setup import get_logger
from .exception_router import route_cause
from .networker_ops import DRIVER_FACTORY, _net_contact_from_row, resolve_pacing_profile
from .operations import OperationContext, OperationOutcome

if TYPE_CHECKING:
    from ..db.models import Contact as ContactRow

# Small per-run batch — the sync is the user's OWN account hitting LinkedIn, so
# it stays gentle (≤ 20 read-probes/tick, on a 6–12 h cadence). NFR-LI-*.
BATCH_LIMIT = 20


@dataclass
class SyncDecision:
    """Pure result of evaluating one contact against its probe: the new kanban
    status (None = stay put) + timestamp columns to stamp alongside it."""

    new_status: str | None = None
    set_accepted_at: bool = False
    # Stamp the sticky `profile_payload.first_replied_at` (S-N5). Once set,
    # `accepted` ("connected, never replied") is unreachable for this contact,
    # which is the invariant that keeps an answered thread from being demoted.
    set_first_replied_at: bool = False


def _days_since(ts: datetime | float | None, now: datetime) -> float | None:
    """Days between `ts` (a datetime column OR an epoch-seconds probe value) and
    `now`. None when `ts` is absent."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        moment = datetime.fromtimestamp(ts, tz=now.tzinfo)
    else:
        moment = ts
    return (now - moment).total_seconds() / 86400.0


def decide_transition(
    current: str,
    probe: ProbeResult,
    *,
    sent_at: datetime | None,
    accepted_at: datetime | None,
    settings: dict[str, int],
    now: datetime,
    has_replied: bool = False,
) -> SyncDecision:
    """Pure transition rule over the syncable columns; never raises. Split by whose
    message is last (S-N5): `pending_our_response` never ghosts (the silence is
    ours); `pending_their_response` can. `has_replied` makes `accepted` unreachable
    once someone has written back."""
    engagement_ghosted = settings["engagement_ghosted_days"]
    sent_ghosted = settings["sent_ghosted_days"]
    they_wrote_last = probe.last_message_direction == "them"

    if current == "sent":
        if probe.is_first_degree:
            if they_wrote_last:
                return SyncDecision(
                    new_status="pending_our_response",
                    set_accepted_at=True,
                    set_first_replied_at=True,
                )
            # Connected, and they have written before even if we answered since.
            if has_replied:
                return SyncDecision(
                    new_status="pending_their_response", set_accepted_at=True
                )
            return SyncDecision(new_status="accepted", set_accepted_at=True)
        # Still pending — a never-accepted invite that stalls past the window ghosts.
        age = _days_since(sent_at, now)
        if age is not None and age > sent_ghosted:
            return SyncDecision(new_status="ghosted")
        return SyncDecision()

    if current == "accepted":
        if they_wrote_last:
            return SyncDecision(
                new_status="pending_our_response", set_first_replied_at=True
            )
        if has_replied:
            # They replied at some point and ours is last: the wait is theirs.
            return SyncDecision(new_status="pending_their_response")
        # Accepted but no reply either way — a stalled thread ghosts on the same
        # (longer) window as a never-accepted invite.
        reference = accepted_at or sent_at
        age = _days_since(reference, now)
        if age is not None and age > sent_ghosted:
            return SyncDecision(new_status="ghosted")
        return SyncDecision()

    if current == "pending_our_response":
        if probe.last_message_direction == "me":
            return SyncDecision(new_status="pending_their_response")
        # Held on purpose: we owe the reply, so this never ages into Ghosted.
        return SyncDecision()

    if current == "pending_their_response":
        if they_wrote_last:
            return SyncDecision(
                new_status="pending_our_response", set_first_replied_at=True
            )
        activity = probe.last_message_at or accepted_at or sent_at
        age = _days_since(activity, now)
        if age is not None and age > engagement_ghosted:
            return SyncDecision(new_status="ghosted")
        return SyncDecision()

    return SyncDecision()


def _status_meta(contact: ContactRow) -> dict[str, Any]:
    payload = contact.profile_payload or {}
    meta = payload.get("status_meta")
    return meta if isinstance(meta, dict) else {}


def _is_manual_frozen(contact: ContactRow, now: datetime) -> bool:
    """True when the last status move was a MANUAL drag within the cooldown — auto
    must not fight it yet (manual wins)."""
    meta = _status_meta(contact)
    if meta.get("source") != "manual":
        return False
    changed = meta.get("changed_at")
    if not isinstance(changed, str):
        return True  # manual with no timestamp — protect it (never override blindly)
    try:
        moment = datetime.fromisoformat(changed)
    except ValueError:
        return True
    return (now - moment).total_seconds() / 86400.0 < MANUAL_OVERRIDE_COOLDOWN_DAYS


def payload_with_status_meta(
    contact: ContactRow, source: str, now: datetime,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The 1 writer of the `status_meta` stamp: this engine stamps `auto`, the PATCH
    route `manual`, and `_is_manual_frozen` reads both, so a format drift can't
    disable the cooldown. `base` stamps onto a payload being composed."""
    return {
        **(dict(contact.profile_payload or {}) if base is None else base),
        "status_meta": {"source": source, "changed_at": now.isoformat()},
    }


def contact_sync_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """One sync tick: probe ≤ BATCH_LIMIT syncable contacts and apply a–d."""
    if ctx.db is None:
        raise RuntimeError("contact_sync requires a database context")
    log = get_logger()
    now = now_utc()

    with ctx.db.repos() as repos:
        prefs = repos.preferences.get_or_create()
        enabled = bool(prefs.voyager_risk_marker_on)
        session = repos.linkedin_session.get()
        session_valid = session is not None and session.status == "valid"
        settings = resolve_lifecycle(prefs)
        profile = resolve_pacing_profile(repos)

    # Gate: OFF or disconnected → clean no-op (zero LinkedIn traffic).
    if not enabled or not session_valid:
        reason = "networking_disabled" if not enabled else "no_valid_session"
        return OperationOutcome(
            result_ref={"synced": 0, "skipped": reason, "transitions": {}}
        )

    with ctx.db.repos() as repos:
        batch = repos.contacts.list_syncable(limit=BATCH_LIMIT)
        contact_ids = [c.id for c in batch]

    transitions: dict[str, int] = {}
    probed = 0
    frozen = 0
    failed = 0
    internal_calls = 0
    stopped = ""

    # Partition the batch. An accepted or engagement contact with a cached fsd urn
    # needs only its thread, answered by the sweep's 1 unmetered inbox read; only
    # `sent` rows and urn-less rows pay a charged profile read. A manually frozen row
    # with a urn rides the thread pass with its transition suppressed. Everything
    # eligible probes in 1 browser session.
    eligible: list[dict[str, Any]] = []
    for contact_id in contact_ids:
        with ctx.db.repos() as repos:
            contact = repos.contacts.get(contact_id)
            if contact is None:
                continue
            payload = contact.profile_payload or {}
            cached_urn = str(payload.get("fsd_urn") or "")
            manual_frozen = _is_manual_frozen(contact, now)
            thread_only = bool(
                cached_urn
                and (
                    contact.connection_status == "accepted"
                    or contact.connection_status in CONTACT_ENGAGED_STATUSES
                )
            )
            if manual_frozen and not thread_only:
                # Rotate (bump last_touched_at) without a probe — auto never
                # fights a fresh manual move, and a metered probe would buy
                # nothing it may act on.
                repos.contacts.update(contact_id, last_touched_at=now)
                frozen += 1
                continue
            net_contact = _net_contact_from_row(contact)
            eligible.append({
                "contact_id": contact_id,
                "net_contact": net_contact,
                "pid": net_contact.public_identifier,
                "current": contact.connection_status,
                "sent_at": contact.sent_at,
                "accepted_at": contact.accepted_at,
                "has_replied": bool(
                    (contact.profile_payload or {}).get("first_replied_at")
                ),
                "urn": cached_urn,
                "thread_only": thread_only,
                # Frozen rows refresh their display snapshot only — the manual
                # column choice stands until the cooldown lapses.
                "display_only": manual_frozen,
            })

    probes: list[ProbeResult] = []
    if eligible:
        driver = DRIVER_FACTORY(profile)
        try:
            probes = probe_batch(
                [e["net_contact"] for e in eligible],
                driver=driver,
                urns={e["pid"]: e["urn"] for e in eligible if e["urn"]},
                thread_only={e["pid"] for e in eligible if e["thread_only"]},
            )
        except NetworkerError as exc:
            # A hard batch failure mustn't kill the tick: log it, rotate the batch, and say
            # so in result_ref (`stopped: "batch_failed"`) rather than a clean 0. S-C14: the
            # router names the cause before the batch rotates.
            routed = route_cause(exc, engine=ctx.engine.engine if ctx.engine else None)
            log.warning(
                "contact_sync: batch probe failed (%s): %s", routed.diagnosis, exc
            )
            with ctx.db.repos() as repos:
                for entry in eligible:
                    repos.contacts.update(entry["contact_id"], last_touched_at=now)
            probes = []
            stopped = "batch_failed"

    # The worker stops on the first rate-limit, cap or auth refusal and reorders, so
    # probes join by public_identifier; unprobed contacts stay first next tick. A 401
    # is a dead session, so probing on just burns reads; the stop is surfaced in
    # result_ref (`stopped`, `unprobed`). A refused row isn't rotated: nothing was
    # learned from it.
    by_pid = {p.public_identifier: p for p in probes}
    for entry in eligible:
        probe = by_pid.get(entry["pid"])
        if probe is None:
            continue  # unprobed tail of a stopped sweep
        contact_id = entry["contact_id"]
        current = entry["current"]
        if probe.error in ("rate_limited", "cap_or_backoff", "auth_error"):
            # The sweep stopped here — surface the reason; the row stays
            # untouched (first in line when the budget/session recovers).
            log.warning(
                "contact_sync: sweep stopped at %s: %s (%s)",
                contact_id, probe.error, probe.reason,
            )
            stopped = probe.error
            continue
        if probe.error:
            # A per-contact failure (403/404/parse) must not kill the sweep —
            # log verbatim, rotate the row, move on (gentle) — and it is
            # COUNTED (`failed` in the result_ref), so a sweep that skipped
            # half its batch never reads as a clean `synced: N`.
            log.warning(
                "contact_sync: probe failed for %s: %s", contact_id, probe.reason
            )
            failed += 1
            with ctx.db.repos() as repos:
                repos.contacts.update(contact_id, last_touched_at=now)
            continue
        probed += 1
        internal_calls += 1

        decision = (
            SyncDecision()
            if entry["display_only"]
            else decide_transition(
                current, probe,
                sent_at=entry["sent_at"], accepted_at=entry["accepted_at"],
                settings=settings, now=now, has_replied=entry["has_replied"],
            )
        )
        with ctx.db.repos() as repos:
            contact = repos.contacts.get(contact_id)
            if contact is None:
                continue
            fields: dict[str, Any] = {}
            payload = dict(contact.profile_payload or {})
            payload_changed = False
            # Always refresh degree from a successful probe (cheap, keeps the card
            # honest); a thread-only probe carries None (no read this sweep) and
            # the stored degree stands.
            if probe.degree is not None:
                fields["connection_degree"] = probe.degree
                fields["is_first_degree"] = probe.is_first_degree
            # Cache the urn a full probe resolved — what makes every later
            # sweep's thread question free for this contact.
            if probe.target_urn and payload.get("fsd_urn") != probe.target_urn:
                payload["fsd_urn"] = probe.target_urn
                payload_changed = True
            # Display persistence (see the module docstring): a probe that read
            # the thread stores its REAL last message beside status_meta. Only
            # a read direction writes — an absent thread keeps the previous
            # snapshot rather than blanking the card.
            if probe.last_message_direction in ("me", "them"):
                payload["last_thread_message"] = {
                    "text": probe.last_message_text,
                    "direction": probe.last_message_direction,
                    "at": (
                        datetime.fromtimestamp(
                            probe.last_message_at, tz=UTC
                        ).isoformat()
                        if probe.last_message_at is not None
                        else None
                    ),
                    "from_name": probe.last_message_from or None,
                }
                payload_changed = True
            if decision.new_status and decision.new_status != current:
                fields["connection_status"] = decision.new_status
                payload = payload_with_status_meta(contact, "auto", now, base=payload)
                payload_changed = True
                if decision.set_accepted_at and contact.accepted_at is None:
                    fields["accepted_at"] = now
            # Sticky, and set whether or not the column moved: the stamp is what
            # makes `accepted` unreachable once they have written back (S-N5).
            if decision.set_first_replied_at and not payload.get("first_replied_at"):
                stamp = (
                    datetime.fromtimestamp(probe.last_message_at, tz=UTC).isoformat()
                    if probe.last_message_at is not None
                    else now.isoformat()
                )
                payload["first_replied_at"] = stamp
                payload_changed = True
            if decision.new_status and decision.new_status != current:
                transitions[f"{current}->{decision.new_status}"] = (
                    transitions.get(f"{current}->{decision.new_status}", 0) + 1
                )
            else:
                # No move — still touch it so the round-robin cursor advances.
                fields["last_touched_at"] = now
            if payload_changed:
                fields["profile_payload"] = payload
            repos.contacts.update(contact_id, **fields)

    if ctx.publish is not None and transitions:
        from ..events import make_event

        ctx.publish(make_event("networker", {
            "id": ctx.operation_id, "phase": "synced",
            "transitions": transitions, "probed": probed,
        }))
    # Contacts that were eligible but never got a probe result — a stopped sweep's
    # tail (they stay first in line for the next tick). Zero on a full sweep.
    unprobed = max(0, len(eligible) - len(probes))
    return OperationOutcome(
        result_ref={
            "synced": probed, "failed": failed, "frozen": frozen,
            "batch": len(contact_ids), "transitions": transitions,
            # Present only when the sweep was cut short (rate_limited /
            # cap_or_backoff / auth_error / batch_failed) — surfaced with the
            # size of the untouched tail, never a clean `synced: N` that hides
            # an early stop.
            **({"stopped": stopped, "unprobed": unprobed} if stopped else {}),
        },
        usage={"internal_calls": internal_calls},
    )


def contact_sync_entrypoints() -> dict[str, Any]:
    """The contact-status sync kind → entrypoint (registered in operations.py)."""
    return {"contact_sync": contact_sync_entrypoint}
