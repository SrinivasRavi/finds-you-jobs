"""Contact-status sync engine (US-NW-12 / FR-NW-15, 2026-07-15 — maintainer-approved).

A periodic, gentle, READ-ONLY sweep that reconciles each tracked contact's kanban
column with its real LinkedIn state, so the Networking board self-advances instead
of relying only on send-time flips + manual drags. Runs on the `contact_sync`
schedule (default every 12 h), batched small (≤ `BATCH_LIMIT`/run) to keep the
user's own account safe. The whole sweep shares ONE browser session
(`probe_batch` → the worker's `contact_sync_states`) — per-contact pacing,
charges, and error isolation are enforced inside the worker.

**The transitions** (probe = the voyager `contact-sync` read: degree + the 1:1
thread's last-message direction/timestamp):

  a. Sent → Accepted     — now 1st-degree, our message is last (accepted, no reply).
  b. Sent → Engagement   — now 1st-degree, their message is last (accepted + replied).
  c. Accepted → Engagement — their message becomes last (our turn to reply).
  d. → Ghosted           — Engagement thread quiet past `engagement_ghosted_days`;
                           a Sent/Accepted-but-never-replied stall past the separate
                           `sent_ghosted_days` window.

**Manual wins (US-NW-12 acceptance).** `Converted` is the user's sacred referral
record: it is never in the syncable set, so auto never enters or exits it. Every
auto move stamps `profile_payload.status_meta = {source: "auto", changed_at}`; a
manual drag stamps `source: "manual"` (the PATCH route). A contact whose last move
was **manual within `MANUAL_OVERRIDE_COOLDOWN_DAYS`** is rotated but not probed —
auto never immediately fights a fresh manual move.

**Gating.** No-ops cleanly when Referral Outreach is OFF (`voyager_risk_marker_on`)
or the LinkedIn session is not `valid` — the schedule can stay enabled; the tick
just does nothing (zero LinkedIn traffic) until the user opts in + connects.

**License firewall.** Reaches `voyager_py` only through the silo's subprocess
driver (`DRIVER_FACTORY` → `DirectVoyagerDriver`, in-process; section 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sidecar.modules.networker import ProbeResult, probe_batch
from sidecar.modules.networker.types import NetworkerError

from ..db.base import now_utc
from ..lifecycle import MANUAL_OVERRIDE_COOLDOWN_DAYS, resolve_lifecycle
from ..logging_setup import get_logger
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
) -> SyncDecision:
    """Pure transition rule (a–d). `current` is one of sent|accepted|engagement
    (the only syncable columns). Returns a SyncDecision — never raises, never
    moves out of converted/ghosted (those never reach here)."""
    engagement_ghosted = settings["engagement_ghosted_days"]
    sent_ghosted = settings["sent_ghosted_days"]

    if current == "sent":
        if probe.is_first_degree:
            # Accepted. Engagement iff they've already replied (their msg last).
            if probe.last_message_direction == "them":
                return SyncDecision(new_status="engagement", set_accepted_at=True)  # (b)
            return SyncDecision(new_status="accepted", set_accepted_at=True)  # (a)
        # Still pending — a never-accepted invite that stalls past the window ghosts.
        age = _days_since(sent_at, now)
        if age is not None and age > sent_ghosted:
            return SyncDecision(new_status="ghosted")  # (d, sent path)
        return SyncDecision()

    if current == "accepted":
        if probe.last_message_direction == "them":
            return SyncDecision(new_status="engagement")  # (c)
        # Accepted but no reply either way — a stalled thread ghosts on the same
        # (longer) window as a never-accepted invite.
        reference = accepted_at or sent_at
        age = _days_since(reference, now)
        if age is not None and age > sent_ghosted:
            return SyncDecision(new_status="ghosted")  # (d, accepted-never-replied)
        return SyncDecision()

    if current == "engagement":
        # No activity beyond the (shorter) engagement window → Ghosted.
        activity = probe.last_message_at or accepted_at or sent_at
        age = _days_since(activity, now)
        if age is not None and age > engagement_ghosted:
            return SyncDecision(new_status="ghosted")  # (d, engagement path)
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


def payload_with_status_meta(contact: ContactRow, source: str, now: datetime) -> dict[str, Any]:
    """The one place the `status_meta` stamp is written — this engine stamps
    `auto`, the contact PATCH route stamps `manual`, and `_is_manual_frozen`
    above reads both. Public so the route stamps the same shape the reader
    expects (a key/format drift silently disables the manual-wins cooldown)."""
    return {
        **(contact.profile_payload or {}),
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
    internal_calls = 0
    stopped = ""

    # Partition the batch: manual-frozen rows rotate without a probe; the rest
    # are probed together in ONE browser session (2026-08-04 — the old loop
    # drove one driver op per contact, and every op builds its own paced
    # session, so a 5-contact sweep launched and quit a full Chromium 5 times).
    eligible: list[tuple[str, Any, str, datetime | None, datetime | None]] = []
    for contact_id in contact_ids:
        with ctx.db.repos() as repos:
            contact = repos.contacts.get(contact_id)
            if contact is None:
                continue
            if _is_manual_frozen(contact, now):
                # Rotate (bump last_touched_at) without a probe — auto never fights
                # a fresh manual move, and the row still cycles out of the queue.
                repos.contacts.update(contact_id, last_touched_at=now)
                frozen += 1
                continue
            eligible.append((
                contact_id, _net_contact_from_row(contact),
                contact.connection_status, contact.sent_at, contact.accepted_at,
            ))

    probes: list[ProbeResult] = []
    if eligible:
        driver = DRIVER_FACTORY(profile)
        try:
            probes = probe_batch([e[1] for e in eligible], driver=driver)
        except NetworkerError as exc:
            # A hard batch failure (driver crash / unparseable envelope) must not
            # kill the tick — log verbatim, rotate the whole batch, report zero
            # (exactly what the per-contact loop did when every probe raised).
            log.warning("contact_sync: batch probe failed: %s", exc)
            with ctx.db.repos() as repos:
                for contact_id, *_rest in eligible:
                    repos.contacts.update(contact_id, last_touched_at=now)
            probes = []

    # The worker stops the sweep on the first rate-limit/cap/auth refusal
    # (section 0.4: the first 429 stops the batch), so `probes` may be shorter than
    # `eligible` — the untouched tail stays first in line for the next tick.
    for (contact_id, _net_contact, current, sent_at, accepted_at), probe in zip(
        eligible, probes, strict=False
    ):
        if probe.error in ("rate_limited", "cap_or_backoff", "auth_error"):
            # The sweep stopped here. Rotate this row and surface the reason —
            # no transition is decided on an empty refused probe.
            log.warning(
                "contact_sync: sweep stopped at %s: %s (%s)",
                contact_id, probe.error, probe.reason,
            )
            with ctx.db.repos() as repos:
                repos.contacts.update(contact_id, last_touched_at=now)
            stopped = probe.error
            break
        if probe.error:
            # A hard probe failure (403/404/parse) must not kill the sweep —
            # log verbatim, rotate the row, move on (gentle).
            log.warning(
                "contact_sync: probe failed for %s: %s", contact_id, probe.reason
            )
            with ctx.db.repos() as repos:
                repos.contacts.update(contact_id, last_touched_at=now)
            continue
        probed += 1
        internal_calls += 1

        decision = decide_transition(
            current, probe, sent_at=sent_at, accepted_at=accepted_at,
            settings=settings, now=now,
        )
        with ctx.db.repos() as repos:
            contact = repos.contacts.get(contact_id)
            if contact is None:
                continue
            fields: dict[str, Any] = {}
            # Always refresh degree from a successful probe (cheap, keeps the card
            # honest); the probe carries it whether or not the status moved.
            if probe.degree is not None:
                fields["connection_degree"] = probe.degree
                fields["is_first_degree"] = probe.is_first_degree
            if decision.new_status and decision.new_status != current:
                fields["connection_status"] = decision.new_status
                fields["profile_payload"] = payload_with_status_meta(contact, "auto", now)
                if decision.set_accepted_at and contact.accepted_at is None:
                    fields["accepted_at"] = now
                transitions[f"{current}->{decision.new_status}"] = (
                    transitions.get(f"{current}->{decision.new_status}", 0) + 1
                )
            else:
                # No move — still touch it so the round-robin cursor advances.
                fields["last_touched_at"] = now
            repos.contacts.update(contact_id, **fields)

    if ctx.publish is not None and transitions:
        from ..events import make_event

        ctx.publish(make_event("networker", {
            "id": ctx.operation_id, "phase": "synced",
            "transitions": transitions, "probed": probed,
        }))
    return OperationOutcome(
        result_ref={
            "synced": probed, "frozen": frozen, "batch": len(contact_ids),
            "transitions": transitions,
            # Present only when the worker cut the sweep short (rate_limited /
            # cap_or_backoff / auth_error) — surfaced, never swallowed.
            **({"stopped": stopped} if stopped else {}),
        },
        usage={"internal_calls": internal_calls},
    )


def contact_sync_entrypoints() -> dict[str, Any]:
    """The contact-status sync kind → entrypoint (registered in operations.py)."""
    return {"contact_sync": contact_sync_entrypoint}
