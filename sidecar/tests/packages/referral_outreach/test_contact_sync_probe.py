# voyager_py/tests/test_contact_sync_probe.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Covers the READ-ONLY contact-status sync probe's pure parsers (FR-NW-15 /
US-NW-12), locked to the GraphQL messenger inbox shape captured verbatim from
LinkedIn's own client (headed DevTools, 2026-08-15) after the legacy
LEGACY_INBOX REST finder proved dead on the live wire:

  - `parse_inbox_last_messages` — the sweep's ONE inbox page → {contact tail:
    InboxThread(last-sender tail, deliveredAt seconds, message text, other
    participant's display name)}, 1:1 threads only.
  - `inbox_direction_for` — one contact's direction + display pair (text,
    from_name) against that map.
  - `summarize_inbox_payload` — the capture's body-shape counts.
  - `parse_self_member_urns` — self identities off `/voyager/api/me` (the
    mailbox urn's source).
  - `redact_urn_string` / `redact_voyager_payload` — the capture redaction:
    shape survives, identities and message bodies do not.

Zero live LinkedIn traffic — the fixtures are SYNTHETIC, built to the captured
shape (no real payload data is kept anywhere in the repo).
"""

from __future__ import annotations

from sidecar.packages.referral_outreach.upstream.voyager import (
    inbox_direction_for,
    parse_inbox_last_messages,
    parse_self_member_urns,
    redact_urn_string,
    redact_voyager_payload,
    summarize_inbox_payload,
)

TARGET = "urn:li:fsd_profile:ACoAAABcontact"
TARGET_MEMBER = "ACoAAABcontact"
SELF_MEMBER = "ACoAAABme000"
THREAD = "2-FyjThReAdId=="


# ── synthetic fixture builders, mirroring the captured response shape ────────
#
#   data.<wrapper key varies by query>.elements: [ Conversation ]
#     .entityUrn: "urn:li:msg_conversation:(urn:li:fsd_profile:<self>,<thread>)"
#     .conversationParticipants[].hostIdentityUrn: "urn:li:fsd_profile:<id>"
#     .conversationParticipants[].participantType.member.distance: "SELF" | …
#     .messages.elements: [ Message ] NEWEST-first
#       .sender.hostIdentityUrn / .actor.hostIdentityUrn, .deliveredAt (ms)


def _participant(profile_id: str, distance: str, first: str = "Alex") -> dict:
    return {
        "hostIdentityUrn": f"urn:li:fsd_profile:{profile_id}",
        "backendUrn": "urn:li:member:98765" if distance == "SELF" else "urn:li:member:31337",
        "participantType": {"member": {
            "distance": distance,
            "firstName": {"text": first},
            "lastName": {"text": "Doe"},
        }},
    }


def _message(
    sender_profile_id: str, ts_ms: int,
    text: str = "Thanks for reaching out, happy to chat!",
) -> dict:
    return {
        "body": {"text": text},
        "deliveredAt": ts_ms,
        "sender": {"hostIdentityUrn": f"urn:li:fsd_profile:{sender_profile_id}"},
        "actor": {"hostIdentityUrn": f"urn:li:fsd_profile:{sender_profile_id}"},
    }


def _conversation(
    other_id: str,
    messages_newest_first: list[dict],
    *,
    other_distance: str = "DISTANCE_1",
    self_id: str = SELF_MEMBER,
    extra_participants: tuple = (),
) -> dict:
    return {
        "entityUrn": f"urn:li:msg_conversation:(urn:li:fsd_profile:{self_id},{THREAD})",
        "lastActivityAt": max(
            (m.get("deliveredAt", 0) for m in messages_newest_first), default=0
        ),
        "conversationParticipants": [
            _participant(other_id, other_distance),
            _participant(self_id, "SELF", first="Me"),
            *extra_participants,
        ],
        "messages": {"elements": list(messages_newest_first)},
    }


def _inbox_payload(conversations: list[dict]) -> dict:
    # The LIVE wrapper of the sync-token snapshot query (second live
    # confirmation, 2026-08-15) — though the parser must never depend on the
    # key (see test_wrapper_key_never_matters). The newSyncToken rides along
    # as the real response carries one; the probe ignores and never stores it.
    return {"data": {"messengerConversationsBySyncToken": {
        "metadata": {"newSyncToken": "FyjSyncTokenSentinel=="},
        "elements": list(conversations),
    }}}


# ── parse_inbox_last_messages + inbox_direction_for ──────────────────────────


def test_reply_last_reads_them_with_text_and_name():
    """The parse carries the DISPLAY pair too: the last message's text and the
    other participant's display name, off the same one response — what the
    host's card/modal shows with honest attribution."""
    payload = _inbox_payload([_conversation(TARGET_MEMBER, [
        _message(TARGET_MEMBER, 1_700_000_002_000),  # newest first
        _message(SELF_MEMBER, 1_700_000_001_000),
    ])])
    inbox = parse_inbox_last_messages(payload)
    assert inbox == {TARGET_MEMBER: (
        TARGET_MEMBER, 1_700_000_002.0,
        "Thanks for reaching out, happy to chat!", "Alex Doe",
    )}
    assert inbox_direction_for(inbox, TARGET) == (
        "them", 1_700_000_002.0, True,
        "Thanks for reaching out, happy to chat!", "Alex Doe",
    )


def test_own_message_last_reads_me():
    """The live-verified control case: SELF sent last ("how is life going
    on?"-shaped thread) → `me` → the card stays put. The display pair still
    rides: OUR text, and the other participant's name (the thread's, not the
    sender's)."""
    payload = _inbox_payload([_conversation(TARGET_MEMBER, [
        _message(SELF_MEMBER, 1_700_000_003_000, text="How is life going on?"),
        _message(TARGET_MEMBER, 1_700_000_001_000),
    ])])
    inbox = parse_inbox_last_messages(payload)
    assert inbox_direction_for(inbox, TARGET) == (
        "me", 1_700_000_003.0, True, "How is life going on?", "Alex Doe",
    )


def test_no_thread_is_honest_absence():
    inbox = parse_inbox_last_messages(_inbox_payload([]))
    assert inbox == {}
    assert inbox_direction_for(inbox, TARGET) == (None, None, False, "", None)
    assert inbox_direction_for({}, None) == (None, None, False, "", None)


def test_group_chats_are_skipped():
    payload = _inbox_payload([_conversation(
        TARGET_MEMBER, [_message(TARGET_MEMBER, 1_700_000_002_000)],
        extra_participants=(_participant("ACoAAABthird", "DISTANCE_2"),),
    )])
    assert parse_inbox_last_messages(payload) == {}


def test_no_self_marker_is_skipped_never_guessed():
    """2 participants but neither marked SELF — who is who can't be told, so
    the thread yields no entry (an honest skip, never a guessed direction)."""
    conv = _conversation(TARGET_MEMBER, [_message(TARGET_MEMBER, 1_700_000_002_000)])
    conv["conversationParticipants"] = [
        _participant(TARGET_MEMBER, "DISTANCE_1"),
        _participant(SELF_MEMBER, "DISTANCE_1"),
    ]
    assert parse_inbox_last_messages(_inbox_payload([conv])) == {}


def test_thread_with_no_messages_yields_no_entry():
    assert parse_inbox_last_messages(
        _inbox_payload([_conversation(TARGET_MEMBER, [])])
    ) == {}


def test_newest_first_and_unreadable_sender_is_none_never_me():
    """`messages.elements[0]` IS the last message (newest-first, live-verified);
    a message whose sender can't be read yields direction None — an honest
    unknown, never a claimed `me`. Its unreadable body degrades to "" the same
    way."""
    last = {"deliveredAt": 1_700_000_009_000}  # no sender, no actor, no body
    payload = _inbox_payload([_conversation(TARGET_MEMBER, [
        last, _message(SELF_MEMBER, 1_700_000_001_000),
    ])])
    inbox = parse_inbox_last_messages(payload)
    assert inbox == {TARGET_MEMBER: ("", 1_700_000_009.0, "", "Alex Doe")}
    assert inbox_direction_for(inbox, TARGET) == (
        None, 1_700_000_009.0, True, "", "Alex Doe",
    )


def test_actor_is_the_fallback_sender_spelling():
    last = {
        "deliveredAt": 1_700_000_002_000,
        "actor": {"hostIdentityUrn": f"urn:li:fsd_profile:{TARGET_MEMBER}"},
    }
    payload = _inbox_payload([_conversation(TARGET_MEMBER, [last])])
    assert inbox_direction_for(parse_inbox_last_messages(payload), TARGET) == (
        "them", 1_700_000_002.0, True, "", "Alex Doe",
    )


def test_wrapper_key_never_matters():
    conv = _conversation(TARGET_MEMBER, [_message(TARGET_MEMBER, 1_700_000_002_000)])
    for payload in (
        {"data": {"messengerConversationsByCategoryQuery": {"elements": [conv]}}},
        {"data": {"someFutureWrapper": {"nested": {"elements": [conv]}}}},
        {"elements": [conv]},
    ):
        assert TARGET_MEMBER in parse_inbox_last_messages(payload)


def test_duplicate_threads_newest_wins_regardless_of_order():
    older = _conversation(TARGET_MEMBER, [_message(SELF_MEMBER, 1_700_000_001_000)])
    newer = _conversation(TARGET_MEMBER, [
        _message(TARGET_MEMBER, 1_700_000_002_000, text="The newer thread"),
    ])
    for order in ([older, newer], [newer, older]):
        inbox = parse_inbox_last_messages(_inbox_payload(order))
        assert inbox[TARGET_MEMBER] == (
            TARGET_MEMBER, 1_700_000_002.0, "The newer thread", "Alex Doe",
        )


def test_parser_defensive_on_garbage():
    assert parse_inbox_last_messages(None) == {}
    assert parse_inbox_last_messages("nope") == {}
    assert parse_inbox_last_messages({"data": "nope"}) == {}
    assert parse_inbox_last_messages(
        {"data": {"x": {"elements": [None, 5, {"conversationParticipants": "nope"}]}}}
    ) == {}


# ── summarize_inbox_payload (the capture's body-shape counts) ────────────────


def test_summary_counts_conversations_and_one_to_one():
    one = _conversation(TARGET_MEMBER, [_message(TARGET_MEMBER, 1_700_000_002_000)])
    group = _conversation(
        TARGET_MEMBER, [_message(TARGET_MEMBER, 1_700_000_003_000)],
        extra_participants=(_participant("ACoAAABthird", "DISTANCE_2"),),
    )
    assert summarize_inbox_payload(_inbox_payload([one, group])) == {
        "conversations": 2, "one_to_one": 1,
    }
    assert summarize_inbox_payload(None) == {"conversations": 0, "one_to_one": 0}


# ── parse_self_member_urns (`/voyager/api/me`, any decoration) ───────────────


def test_self_urns_collected_across_me_shapes():
    me_json = {
        "data": {"*miniProfile": "urn:li:fs_miniProfile:ACoAAABme000", "plainId": 98765},
        "included": [{
            "$type": "com.linkedin.voyager.identity.shared.MiniProfile",
            "entityUrn": "urn:li:fs_miniProfile:ACoAAABme000",
            "objectUrn": "urn:li:member:98765",
            "dashEntityUrn": "urn:li:fsd_profile:ACoAAABme000",
            "publicIdentifier": "me-myself",
        }],
    }
    urns = parse_self_member_urns(me_json)
    assert "urn:li:fs_miniProfile:ACoAAABme000" in urns
    assert "urn:li:member:98765" in urns  # from objectUrn AND plainId, de-duped
    assert "urn:li:fsd_profile:ACoAAABme000" in urns
    assert len(urns) == len(set(urns))


def test_self_urns_ignore_non_member_namespaces_and_garbage():
    assert parse_self_member_urns({"data": {"conv": "urn:li:fs_conversation:2-x"}}) == []
    assert parse_self_member_urns(None) == []
    assert parse_self_member_urns("nope") == []


# ── capture redaction: shape survives, identities and bodies do not ──────────


def test_redact_urn_keeps_namespace_and_structure_with_stable_ids():
    id_map: dict[str, str] = {}
    composite = redact_urn_string(
        f"urn:li:msg_conversation:(urn:li:fsd_profile:{SELF_MEMBER},{THREAD})", id_map
    )
    assert composite == "urn:li:msg_conversation:(urn:li:fsd_profile:ID_1,ID_2)"
    # The same fragment maps to the same placeholder across urns — the
    # cross-reference the maintainer's shape confirmation needs.
    assert redact_urn_string(
        f"urn:li:fsd_profile:{SELF_MEMBER}", id_map
    ) == "urn:li:fsd_profile:ID_1"
    # Nested namespaces are kept too.
    nested = redact_urn_string(
        "urn:li:msg_messagingParticipant:urn:li:fsd_profile:ACoAAABcontact", id_map
    )
    assert nested == "urn:li:msg_messagingParticipant:urn:li:fsd_profile:ID_3"


def test_redact_payload_drops_identities_and_bodies_keeps_shape():
    payload = _inbox_payload([_conversation(TARGET_MEMBER, [
        _message(TARGET_MEMBER, 1_700_000_002_000),
        _message(SELF_MEMBER, 1_700_000_001_000),
    ])])
    id_map: dict[str, str] = {}
    redacted = redact_voyager_payload(payload, id_map)
    blob = repr(redacted)
    # No identity fragment, no name, no message body, anywhere.
    assert TARGET_MEMBER not in blob and SELF_MEMBER not in blob
    assert THREAD not in blob
    assert "98765" not in blob and "31337" not in blob
    assert "happy to chat" not in blob
    assert "Alex" not in blob and "Doe" not in blob
    # The shape is intact: keys, nesting, urn namespaces, numbers.
    conv = redacted["data"]["messengerConversationsBySyncToken"]["elements"][0]
    assert conv["entityUrn"].startswith("urn:li:msg_conversation:(urn:li:fsd_profile:ID_")
    assert conv["lastActivityAt"] == 1_700_000_002_000
    assert conv["messages"]["elements"][0]["body"]["text"] == "REDACTED_TEXT"
    # The categorical enums survive (SELF / DISTANCE_1 / PRIMARY_INBOX class),
    # which keeps the redacted payload DIRECTION-FAITHFUL: it still parses to
    # the same conclusion through the redacted target urn.
    assert conv["conversationParticipants"][1]["participantType"]["member"][
        "distance"] == "SELF"
    r_target = redact_urn_string(TARGET, id_map)
    assert inbox_direction_for(parse_inbox_last_messages(redacted), r_target) == (
        "them", 1_700_000_002.0, True,
        # The display pair is exactly what redaction is FOR: body and name
        # survive only as placeholders.
        "REDACTED_TEXT", "REDACTED_TEXT REDACTED_TEXT",
    )


def test_redact_payload_zeroes_plain_member_ids():
    assert redact_voyager_payload({"plainId": 98765}, {}) == {"plainId": 0}
