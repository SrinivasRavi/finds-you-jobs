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
was **manual within `MANUAL_OVERRIDE_COOLDOWN_DAYS`** never has its STATUS
auto-moved — but a frozen accepted/engagement row with a cached urn still gets
an unmetered thread-only probe for its display snapshot (a read-only refresh
fights no manual move); other frozen rows rotate unprobed.

**Read-budget economics (2026-08-16).** Only `sent` rows and urn-less rows pay
a charged profile read; accepted/engagement rows with a cached
`profile_payload.fsd_urn` sync from the sweep's ONE inbox read, unmetered —
so a spent profile-view budget can never silence the message-driven columns.
A budget/backoff/auth-refused row is surfaced (`stopped`), never rotated.

**Display persistence.** A probe that read the thread also writes
`profile_payload.last_thread_message = {text, direction, at, from_name}` — the
REAL last message the kanban card and contact modal show with Me/name
attribution (no schema change; it rides the same JSON column as `status_meta`).
A probe with no thread never blanks a previous sync's snapshot.

**Gating.** No-ops cleanly when Referral Outreach is OFF (`voyager_risk_marker_on`)
or the LinkedIn session is not `valid` — the schedule can stay enabled; the tick
just does nothing (zero LinkedIn traffic) until the user opts in + connects.

**License firewall.** Reaches `voyager_py` only through the silo's subprocess
driver (`DRIVER_FACTORY` → `DirectVoyagerDriver`, in-process; section 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
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


def payload_with_status_meta(
    contact: ContactRow, source: str, now: datetime,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The one place the `status_meta` stamp is written — this engine stamps
    `auto`, the contact PATCH route stamps `manual`, and `_is_manual_frozen`
    above reads both. Public so the route stamps the same shape the reader
    expects (a key/format drift silently disables the manual-wins cooldown).
    `base` lets a caller stamp onto a payload it is already composing (the
    sync engine's urn/thread riders); default is the row's stored payload."""
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

    # Partition the batch (2026-08-16, the read-budget redesign). A contact in
    # accepted/engagement with a CACHED fsd_profile urn (`profile_payload.
    # fsd_urn`, written back from an earlier full probe) needs no degree read:
    # its whole sync question is the 1:1 thread, answered by the sweep's one
    # inbox read — an unmetered thread-only probe that a spent profile-view
    # budget can never refuse. Only `sent` rows (is the invite accepted?) and
    # urn-less rows (the bootstrap read that learns the urn) pay a charged
    # profile read. Manual-frozen rows used to rotate unprobed, which held
    # their card's last message stale for the whole cooldown; a frozen
    # accepted/engagement row with a cached urn now rides the thread-only pass
    # with its TRANSITION suppressed (display refresh only — a read-only
    # snapshot fights no manual move). Everything eligible probes in ONE
    # browser session (2026-08-04 — the per-contact loop launched a full
    # Chromium 5 times for a 5-contact sweep).
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
                and contact.connection_status in ("accepted", "engagement")
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
            # A hard batch failure (driver crash / unparseable envelope) must not
            # kill the tick — log verbatim, rotate the whole batch, and SAY so in
            # the result_ref (`stopped: "batch_failed"`) instead of reporting a
            # clean zero that reads like "nothing to do".
            log.warning("contact_sync: batch probe failed: %s", exc)
            with ctx.db.repos() as repos:
                for entry in eligible:
                    repos.contacts.update(entry["contact_id"], last_touched_at=now)
            probes = []
            stopped = "batch_failed"

    # The worker stops the sweep on the first rate-limit/cap/auth refusal
    # (section 0.4: the first 429 stops the batch), and it reorders (thread-only
    # first), so probes are JOINED BY public_identifier — contacts without a
    # result were never probed and stay first in line for the next tick. The
    # auth stop is deliberate: a 401 is a dead SESSION, not one bad contact,
    # so every later probe would 401 identically and probing on just burns
    # authenticated reads. What must not happen is the stop hiding — it is
    # surfaced in the result_ref (`stopped` + `unprobed`), never swallowed. A
    # refused row is NOT rotated: nothing was learned from it, and the old
    # rotate-on-refusal made every budget-refused Sync press churn one card's
    # cursor for zero LinkedIn traffic.
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
                settings=settings, now=now,
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
