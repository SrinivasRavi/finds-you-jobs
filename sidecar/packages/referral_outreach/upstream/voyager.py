# voyager_py/voyager.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Forked verbatim from OpenOutreach `linkedin/api/voyager.py` @ a7a9101.
# This is the load-bearing IP of the fork: the parser that turns a raw
# LinkedIn Voyager profile response (data + included graph) into a clean,
# JSON-serialisable dict. Pure — no I/O, no network.
#   - Contact-sync messaging read (finds-you-jobs, 2026-08-15): pure parsers
#     for the read-only last-message probe, reworked through live wire evidence
#     to LinkedIn's GraphQL messenger inbox — `parse_inbox_last_messages`,
#     `summarize_inbox_payload` and `inbox_direction_for` read the ONE
#     conversations page the client fetches per sweep, after headed DevTools
#     capture proved the legacy LEGACY_INBOX REST finder dead (500 for every
#     recipient form; messaging moved to GraphQL). `parse_self_member_urns`
#     (the `/me` identity read), `urn_member_id`, and the capture redaction
#     (`redact_urn_string` / `redact_voyager_payload`) back the client's
#     env-gated all-paths capture. Superseded same-day intermediates (the
#     self-anchored LEGACY_INBOX parse, the numeric-member recipient
#     extraction) are recorded in `provenance.md`. Same-day display extension:
#     each inbox-map row now also carries the last message's TEXT and the other
#     participant's display name (`InboxThread`), read from the same one
#     response — nothing extra is fetched — so the host can show the thread's
#     real last message with honest Me/name attribution.
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, NamedTuple, Optional, Tuple

ConnectionDistance = Literal["DISTANCE_1", "DISTANCE_2", "DISTANCE_3", "OUT_OF_NETWORK", None]

DISTANCE_TO_DEGREE: Dict[str, Optional[int]] = {
    "DISTANCE_1": 1,
    "DISTANCE_2": 2,
    "DISTANCE_3": 3,
    "OUT_OF_NETWORK": None,
}


# ======================
# Internal dataclasses (only used for validation & structure)
# ======================

@dataclass
class Date:
    year: Optional[int] = None
    month: Optional[int] = None


@dataclass
class DateRange:
    start: Optional[Date] = None
    end: Optional[Date] = None


@dataclass
class Position:
    title: str
    company_name: str
    company_urn: Optional[str] = None
    company_logo_url: Optional[str] = None
    location: Optional[str] = None
    date_range: Optional[DateRange] = None
    description: Optional[str] = None
    urn: Optional[str] = None


@dataclass
class Education:
    school_name: str
    degree_name: Optional[str] = None
    field_of_study: Optional[str] = None
    date_range: Optional[DateRange] = None
    urn: Optional[str] = None


@dataclass
class LinkedInProfile:
    url: str
    urn: str
    full_name: str
    first_name: str
    last_name: str

    headline: Optional[str] = None
    summary: Optional[str] = None
    public_identifier: Optional[str] = None
    location_name: Optional[str] = None
    geo: Optional[Dict[str, Any]] = None
    industry: Optional[Dict[str, Any]] = None

    profile_picture_url: Optional[str] = None

    positions: List[Position] = field(default_factory=list)
    educations: List[Education] = field(default_factory=list)
    current_position: Optional[Position] = None

    country_code: Optional[str] = None
    supported_locales: List[str] = field(default_factory=list)

    connection_distance: Optional[ConnectionDistance] = None
    connection_degree: Optional[int] = None


# ======================
# Private helpers
# ======================

def _resolve_references(data: dict) -> Dict[str, dict]:
    """Build urn → entity lookup from 'included' array."""
    return {
        entity.get("entityUrn"): entity
        for entity in data.get("included", [])
        if entity.get("entityUrn")
    }


def _resolve_star_field(entity: dict, urn_map: Dict[str, dict], field_name: str) -> Any:
    """Resolve *company, *school, *elements, etc."""
    value = entity.get(field_name)
    if not value:
        return None
    if isinstance(value, list):
        return [urn_map.get(urn) for urn in value if urn_map.get(urn)]
    return urn_map.get(value)


# ======================
# Shared parser helpers (PUBLIC — the parser trio's common vocabulary)
# ======================
#
# `company.py` and `jobs.py` each carried their own copy of these two, one of
# them explicitly "kept local to avoid importing a private symbol across
# modules" — so the copies drifted (defensive isinstance guards on one side
# only, a `.strip()` on another). They live here, public, because voyager.py is
# the pure parser this trio is built around. Still GPL-subtree-internal: nothing
# outside `upstream/` may import them.


def vector_image_url(vector_img: Optional[dict], target_width: int = 400) -> Optional[str]:
    """Resolve a Voyager vectorImage to a displayable URL, ``None`` when it has
    no usable artifact.

    Picks the artifact with width closest to ``target_width`` (artifacts are
    typically 100/200/400/800 px) and joins it to ``rootUrl``. Every shape guard
    degrades to ``None`` — a malformed image node is a missing picture, never a
    crash in the middle of a profile parse. (Callers that want "" for the miss
    wrap this; see `company._vector_image_url`.)
    """
    if not isinstance(vector_img, dict):
        return None
    root = vector_img.get("rootUrl")
    artifacts = vector_img.get("artifacts") or []
    if not root or not isinstance(artifacts, list) or not artifacts:
        return None
    chosen = min(artifacts, key=lambda a: abs((a or {}).get("width", 0) - target_width))
    seg = (chosen or {}).get("fileIdentifyingUrlPathSegment", "")
    return root + seg if seg else None


def text_of(value: Any, *, strip: bool = False) -> str:
    """A Voyager text node → a plain string ("" when there is none).

    A node is either a bare ``str`` or a TextViewModel-ish ``{"text": …}``.
    ``strip=True`` trims surrounding whitespace — the jobs-search cards arrive
    with non-breaking spaces around the title, and a whitespace-only title must
    read as absent.
    """
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict):
        text = str(value.get("text") or "")
    else:
        text = ""
    return text.strip() if strip else text


# ======================
# Private helpers (continued)
# ======================


def _company_logo_url(company: Optional[dict]) -> Optional[str]:
    if not company:
        return None
    return vector_image_url((company.get("logo") or {}).get("vectorImage"))


def _date_from_raw(raw: Optional[dict]) -> Optional[Date]:
    if not raw:
        return None
    return Date(year=raw.get("year"), month=raw.get("month"))


def _date_range_from_raw(raw: Optional[dict]) -> Optional[DateRange]:
    if not raw:
        return None
    return DateRange(
        start=_date_from_raw(raw.get("start")),
        end=_date_from_raw(raw.get("end")),
    )


def _enrich_position(pos: dict, urn_map: Dict[str, dict]) -> Position:
    company = _resolve_star_field(pos, urn_map, "*company")

    return Position(
        title=pos.get("title") or "Unknown Title",
        company_name=company.get("name") if company else pos.get("companyName", "Unknown Company"),
        company_urn=company.get("entityUrn") if company else pos.get("companyUrn"),
        company_logo_url=_company_logo_url(company),
        location=pos.get("locationName"),
        date_range=_date_range_from_raw(pos.get("dateRange")),
        description=pos.get("description"),
        urn=pos.get("entityUrn"),
    )


def _enrich_education(edu: dict, urn_map: Dict[str, dict]) -> Education:
    school = _resolve_star_field(edu, urn_map, "*school")

    return Education(
        school_name=school.get("name") if school else edu.get("schoolName", "Unknown School"),
        degree_name=edu.get("degreeName"),
        field_of_study=edu.get("fieldOfStudy"),
        date_range=_date_range_from_raw(edu.get("dateRange")),
        urn=edu.get("entityUrn"),
    )


def _degree_from_union(union: dict) -> tuple[Optional[str], Optional[int]]:
    """Extract (distance_str, degree) from a memberRelationshipUnion/Data dict."""
    if any(k in union for k in ("connectedMember", "connected", "*connection", "connection")):
        return "DISTANCE_1", 1

    if "noConnection" in union:
        distance_str = union["noConnection"].get("memberDistance")
        degree = DISTANCE_TO_DEGREE.get(distance_str)
        return distance_str, degree

    return None, None


def _extract_connection_info(
    profile_entity: dict, urn_map: Dict[str, dict]
) -> tuple[Optional[str], Optional[int]]:
    member_rel_urn = profile_entity.get("*memberRelationship")
    if not member_rel_urn:
        return None, None

    rel = urn_map.get(member_rel_urn)
    if not rel:
        return None, None

    union = rel.get("memberRelationshipUnion") or rel.get("memberRelationshipData")
    if not union:
        return None, None

    return _degree_from_union(union)


def _scan_included_for_connection(
    json_response: dict,
) -> tuple[Optional[str], Optional[int]]:
    """Extract (distance_str, degree) by scanning included entities directly.

    Works with any Voyager decoration that includes MemberRelationship
    entities (e.g. TopCardSupplementary-120). Does NOT depend on the profile
    entity linking via *memberRelationship — which the FullProfileWithEntities
    decoration used by discovery frequently omits, the root of the
    "connection_degree NULL on every discovered row" dogfood bug.
    """
    for entity in json_response.get("included", []):
        if entity.get("$type") != "com.linkedin.voyager.dash.relationships.MemberRelationship":
            continue
        union = entity.get("memberRelationshipUnion") or entity.get("memberRelationshipData")
        if not union:
            continue
        distance_str, degree = _degree_from_union(union)
        if degree is not None:
            return distance_str, degree
    return None, None


def parse_connection_degree(json_response: dict) -> Optional[int]:
    """Connection degree by scanning included entities directly (degree only)."""
    return _scan_included_for_connection(json_response)[1]


# ======================
# Messaging: the GraphQL inbox read (contact-sync probe, FR-NW-15)
# ======================
#
# NEW code for the finds-you-jobs fork (GPL subtree) — the read-only messaging
# probe the contact-status sync engine needs (never a send). Pure: given the
# raw `messengerConversations` GraphQL response (LinkedIn's own client's
# sync-token inbox snapshot, captured 2026-08-15 via headed DevTools after the
# legacy REST finder proved dead), build a map of every 1:1 conversation's
# other participant → who sent its LAST message and when. The sync engine then
# answers "did this contact reply?" for a whole sweep from ONE request.
# Defensive throughout: any missing/unexpected shape degrades to an absent map
# entry so a live parse miss turns into "no transition this tick", never a
# crash (the honest-failure ethos).
#
# Captured response shape (synthetic fixtures mirror it; no real data kept —
# the live wrapper is `data.messengerConversationsBySyncToken.elements`, but
# the walk stays wrapper-agnostic):
#   data.<wrapper key varies by query>.elements: [ Conversation ]
#     Conversation.entityUrn: "urn:li:msg_conversation:(urn:li:fsd_profile:<self>,<thread>)"
#     Conversation.conversationParticipants: [ MessagingParticipant ]
#       .hostIdentityUrn: "urn:li:fsd_profile:<profileId>"   ← the join key
#       .participantType.member.distance: "SELF" | "DISTANCE_1" | …
#     Conversation.messages.elements: [ Message ] NEWEST-FIRST
#       .sender.hostIdentityUrn / .actor.hostIdentityUrn: who sent it
#       .deliveredAt: epoch ms


class InboxThread(NamedTuple):
    """One 1:1 thread's last message, as the sweep's inbox map stores it: who
    sent it (profile-id tail), when, the message text, and the OTHER
    participant's display name — the human name the host attributes a `them`
    message to. `text`/`other_name` degrade to "" when unreadable, same
    honest-absence rule as everything else here."""

    sender_tail: str
    sent_at: Optional[float]
    text: str
    other_name: str


def urn_member_id(urn: Optional[str]) -> str:
    """The trailing member-id fragment of any LinkedIn URN, for cross-format
    comparison (an inbox participant's `hostIdentityUrn` vs the contact's
    fsd_profile `target_urn` — the join key of the sweep's inbox map). Empty
    when there is no urn. Public (2026-08-15): the cross-module convention
    here is public helpers, never private imports."""
    if not urn:
        return ""
    tail = str(urn).rstrip(")").split(":")[-1]
    return tail.split(",")[-1].strip()


def _event_timestamp(event: dict) -> Optional[float]:
    """Delivered/created epoch-seconds of one message event (LinkedIn stores ms)."""
    for key in ("deliveredAt", "createdAt", "lastActivityAt"):
        raw = event.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw) / 1000.0 if raw > 1e11 else float(raw)
    return None


def _conversation_nodes(json_response: Any) -> List[dict]:
    """Every dict carrying a `conversationParticipants` list, wherever the
    GraphQL wrapper nests it — the wrapper key between `data` and `elements`
    varies by query, so conversations are recognised by their own shape, never
    by a hardcoded path. Pure, never raises."""
    found: List[dict] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("conversationParticipants"), list):
                found.append(node)
                return  # a conversation never nests another conversation
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)

    _walk(json_response)
    return found


def _participant_tail(participant: Any) -> str:
    """The profile-id tail of a participant/sender/actor node's
    `hostIdentityUrn` ("" when unreadable)."""
    if not isinstance(participant, dict):
        return ""
    host = participant.get("hostIdentityUrn")
    return urn_member_id(host) if isinstance(host, str) else ""


def _participant_is_self(participant: dict) -> bool:
    """True when the response itself marks this participant as the logged-in
    member (`participantType.member.distance == "SELF"`)."""
    ptype = participant.get("participantType")
    member = ptype.get("member") if isinstance(ptype, dict) else None
    return isinstance(member, dict) and member.get("distance") == "SELF"


def _participant_display_name(participant: Any) -> str:
    """The participant's rendered name off `participantType.member`
    (`firstName`/`lastName` text nodes), "" when unreadable."""
    if not isinstance(participant, dict):
        return ""
    ptype = participant.get("participantType")
    member = ptype.get("member") if isinstance(ptype, dict) else None
    if not isinstance(member, dict):
        return ""
    first = text_of(member.get("firstName"), strip=True)
    last = text_of(member.get("lastName"), strip=True)
    return f"{first} {last}".strip()


def parse_inbox_last_messages(json_response: Any) -> Dict[str, InboxThread]:
    """{ other-participant profile-id tail : InboxThread(last-sender tail,
    deliveredAt epoch-seconds, message text, other participant's display
    name) } for every readable 1:1 conversation in a `messengerConversations`
    GraphQL response.

    1:1 threads ONLY: a conversation qualifies when it has exactly 2
    participants, exactly 1 of them marked SELF by the response itself (group
    chats and unreadable participant sets are skipped — an absent entry means
    "no message data", which the sync engine treats as no reply-based
    transition). The last message is `messages.elements[0]` (NEWEST-first,
    verified on the live wire 2026-08-15); its sender is read from
    `sender.hostIdentityUrn` with `actor` as the fallback spelling, its text
    from `body.text`, and the other participant's name from their
    `participantType.member` first/last text nodes. Should the same contact
    somehow appear in 2 threads, the newer timestamp wins — no
    response-ordering assumption. Pure + defensive: any unexpected shape just
    yields no entry."""
    out: Dict[str, InboxThread] = {}
    for conv in _conversation_nodes(json_response):
        participants = [
            p for p in conv["conversationParticipants"] if isinstance(p, dict)
        ]
        if len(participants) != 2:
            continue  # group chat (or unreadable) — the probe is 1:1 only
        selves = [p for p in participants if _participant_is_self(p)]
        if len(selves) != 1:
            continue  # can't tell who is who — honest skip, never a guess
        other = participants[0] if participants[1] is selves[0] else participants[1]
        other_tail = _participant_tail(other)
        if not other_tail:
            continue
        messages = conv.get("messages")
        if isinstance(messages, dict):
            messages = messages.get("elements")
        if not (isinstance(messages, list) and messages and isinstance(messages[0], dict)):
            continue  # thread with no readable messages — no message data
        last = messages[0]
        sender_tail = _participant_tail(last.get("sender")) or _participant_tail(
            last.get("actor")
        )
        ts = _event_timestamp(last)
        previous = out.get(other_tail)
        if previous is None or (ts or 0.0) >= (previous.sent_at or 0.0):
            out[other_tail] = InboxThread(
                sender_tail,
                ts,
                text_of(last.get("body"), strip=True),
                _participant_display_name(other),
            )
    return out


def summarize_inbox_payload(json_response: Any) -> Dict[str, int]:
    """Body-shape counts for the all-paths capture: how many conversations the
    inbox page carried and how many of them are 1:1 (exactly 2 participants).
    One glance separates "empty inbox page" from "conversations present but
    none 1:1/readable". Pure, never raises; garbage reports zeros."""
    conversations = _conversation_nodes(json_response)
    one_to_one = [
        c for c in conversations
        if len([p for p in c["conversationParticipants"] if isinstance(p, dict)]) == 2
    ]
    return {"conversations": len(conversations), "one_to_one": len(one_to_one)}


def inbox_direction_for(
    inbox_map: Dict[str, InboxThread],
    target_urn: Optional[str],
) -> Tuple[Optional[str], Optional[float], bool, str, Optional[str]]:
    """(direction, sent_at, thread_found, text, from_name) for one contact
    against the sweep's inbox map (`parse_inbox_last_messages`).

    The contact's fsd_profile urn tail is the join key. In a qualifying 1:1
    thread the only participants are self and the contact, so the last
    message's sender tail equal to the contact ⇒ `them` (their reply is
    pending OUR answer); any other readable sender ⇒ `me`; an unreadable
    sender ⇒ None (honest unknown, never a claimed `me`). `text` is the last
    message's body ("" when unreadable) and `from_name` the OTHER
    participant's display name (None when unreadable) — the host's display
    pair, whichever direction the message ran. No thread in the fetched page
    ⇒ (None, None, False, "", None) — no reply-based transition, degree
    transitions still apply."""
    tail = urn_member_id(target_urn)
    if not tail or tail not in inbox_map:
        return None, None, False, "", None
    thread = inbox_map[tail]
    from_name = thread.other_name or None
    if not thread.sender_tail:
        return None, thread.sent_at, True, thread.text, from_name
    direction = "them" if thread.sender_tail == tail else "me"
    return direction, thread.sent_at, True, thread.text, from_name


# Every URN namespace a member identity is spelled in across the messaging +
# identity decorations. `/voyager/api/me` describes ONLY the logged-in member,
# so every urn in these namespaces found there is a self identity — collecting
# them all is what makes the `me` anchor robust to whichever namespace a
# messaging sender urn arrives in (fs_messagingMember composite, miniProfile,
# dash fsd_profile, numeric member id).
_MEMBER_URN_NAMESPACES = frozenset({
    "member", "fs_miniProfile", "fs_profile", "fsd_profile",
    "fs_messagingMember", "msg_messagingParticipant",
})


def parse_self_member_urns(me_json: Any) -> List[str]:
    """Every member-identity URN in a `/voyager/api/me` response (any
    decoration): `fs_miniProfile` entityUrn, `urn:li:member:<id>` objectUrn,
    dash `fsd_profile` urn, a bare `plainId`, … Order-preserving, de-duplicated,
    `[]` on anything unreadable — never raises (the probe must not fail because
    the identity read hiccuped)."""
    found: List[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            plain = node.get("plainId")
            if isinstance(plain, int) and not isinstance(plain, bool):
                found.append(f"urn:li:member:{plain}")
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for value in node:
                _walk(value)
        elif isinstance(node, str) and node.startswith("urn:li:"):
            parts = node.split(":")
            if len(parts) >= 4 and parts[2] in _MEMBER_URN_NAMESPACES:
                found.append(node)

    _walk(me_json)
    return list(dict.fromkeys(found))


# ======================
# Capture redaction (maintainer-run shape confirmation, 2026-08-15)
# ======================
#
# Pure helpers behind the client's env-gated capture (`FYJ_LINKEDIN_CAPTURE_DIR`):
# a structurally-faithful, identity-free copy of a raw Voyager payload the
# maintainer can hand back to lock the wire-cold fixtures to the real shape.
# Keys, nesting, `$type`/schema names, timestamps and booleans are kept (the
# SHAPE is the evidence); URN id fragments map to stable `ID_n` placeholders
# (same fragment ⇒ same placeholder, so sender/participant cross-references
# stay checkable); every other string — names, message bodies, headlines —
# becomes "REDACTED_TEXT".

_URN_PREFIX_RE = re.compile(r"urn:li:[A-Za-z_.]+:")
_URN_ID_DELIM_RE = re.compile(r"([(),])")


def _redact_fragment(fragment: str, id_map: Dict[str, str]) -> str:
    if fragment not in id_map:
        id_map[fragment] = f"ID_{len(id_map) + 1}"
    return id_map[fragment]


def redact_urn_string(value: str, id_map: Dict[str, str]) -> str:
    """`value` with every URN's id fragments replaced via `id_map`, namespaces
    (`urn:li:<ns>:`, nested urns included) and composite structure (`(a,b)`)
    kept verbatim."""
    out: List[str] = []
    for part in re.split(r"(urn:li:[A-Za-z_.]+:)", value):
        if not part:
            continue
        if _URN_PREFIX_RE.fullmatch(part):
            out.append(part)
            continue
        out.append("".join(
            token if _URN_ID_DELIM_RE.fullmatch(token) else _redact_fragment(token, id_map)
            for token in _URN_ID_DELIM_RE.split(part)
            if token
        ))
    return "".join(out)


def redact_voyager_payload(node: Any, id_map: Dict[str, str]) -> Any:
    """A structurally-faithful, identity-free copy of `node` (see the section
    comment above). Numbers under identity keys (`plainId`, `memberId`) are
    zeroed; other numbers (timestamps, counts) pass through."""
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            new_key = redact_urn_string(key, id_map) if "urn:li:" in key else key
            if key in ("plainId", "memberId") and isinstance(value, (int, float)):
                out[new_key] = 0
            elif key in ("distance", "category") and isinstance(value, str):
                # LinkedIn enum tokens (SELF / DISTANCE_1 / PRIMARY_INBOX …):
                # categorical, never user content — and the SELF marker is what
                # lets a redacted inbox payload still parse to the same map,
                # so the capture stays direction-faithful (2026-08-15).
                out[new_key] = value
            else:
                out[new_key] = redact_voyager_payload(value, id_map)
        return out
    if isinstance(node, list):
        return [redact_voyager_payload(value, id_map) for value in node]
    if isinstance(node, str):
        if "urn:li:" in node:
            return redact_urn_string(node, id_map)
        if node.startswith("com.linkedin.") or node == "":
            return node
        return "REDACTED_TEXT"
    return node


# ======================
# Public function – returns plain dict
# ======================

def parse_linkedin_voyager_response(
        json_response: dict,
        public_identifier: Optional[str] = None,
) -> dict:
    """
    Parse a full LinkedIn Voyager profile response and return a clean dictionary.

    Uses dataclasses internally for validation and structure,
    but returns a plain, JSON-serializable dict (no dataclass leakage).

    Args:
        json_response: Raw JSON from Voyager API (with "data" and "included")
        public_identifier: Optional filter – only parse profile with this public ID

    Returns:
        dict with clean, structured LinkedIn profile data
    """
    urn_map = _resolve_references(json_response)

    # Find the main Profile entity
    profile_entity = None
    for entity in json_response.get("included", []):
        if entity.get("$type") == "com.linkedin.voyager.dash.identity.profile.Profile":
            entity_id = entity.get("publicIdentifier")
            if public_identifier is not None and entity_id == public_identifier:
                profile_entity = entity
                break
            if public_identifier is None:
                recipes = entity.get("$recipeTypes", [])
                is_full = any("FullProfile" in r for r in recipes)
                if is_full:
                    profile_entity = entity
                    break
                if profile_entity is None:
                    profile_entity = entity

    # Fallback if not found via $type
    if not profile_entity:
        main_urn = json_response.get("data", {}).get("*elements", [None])[0]
        profile_entity = urn_map.get(main_urn)

    if not profile_entity:
        raise ValueError("Could not find profile entity in the Voyager response")

    first_name = profile_entity.get("firstName", "")
    last_name = profile_entity.get("lastName", "")

    # Extract connection info. The linked path (profile *memberRelationship) is
    # tried first; when the decoration doesn't link it (common on the discovery
    # FullProfileWithEntities response), fall back to scanning the included graph
    # for any MemberRelationship entity so `connection_degree` still lands and
    # warmth routing works (US-REF-10 / FR-NW-02).
    connection_distance, connection_degree = _extract_connection_info(profile_entity, urn_map)
    if connection_degree is None:
        connection_distance, connection_degree = _scan_included_for_connection(json_response)

    # Build positions
    positions: List[Position] = []
    pos_groups_urn = profile_entity.get("*profilePositionGroups")
    if pos_groups_urn:
        pos_groups_resp = urn_map.get(pos_groups_urn)
        if pos_groups_resp and pos_groups_resp.get("*elements"):
            for group_urn in pos_groups_resp["*elements"]:
                group = urn_map.get(group_urn)
                if not group:
                    continue
                positions_coll_urn = group.get("*profilePositionInPositionGroup")
                if positions_coll_urn:
                    positions_coll = urn_map.get(positions_coll_urn)
                    if positions_coll and positions_coll.get("*elements"):
                        for pos_urn in positions_coll["*elements"]:
                            pos = urn_map.get(pos_urn)
                            if pos:
                                positions.append(_enrich_position(pos, urn_map))

    # Build educations
    educations: List[Education] = []
    educations_urn = profile_entity.get("*profileEducations")
    if educations_urn:
        edu_coll = urn_map.get(educations_urn)
        if edu_coll and edu_coll.get("*elements"):
            for edu_urn in edu_coll["*elements"]:
                edu = urn_map.get(edu_urn)
                if edu:
                    educations.append(_enrich_education(edu, urn_map))

    # Resolve geo — try direct *geo first, then nested geoLocation.*geo
    geo_entity = _resolve_star_field(profile_entity, urn_map, "*geo")
    if not geo_entity:
        geo_location = profile_entity.get("geoLocation")
        if geo_location:
            geo_urn = geo_location.get("*geo") or geo_location.get("geoUrn")
            if geo_urn:
                geo_entity = urn_map.get(geo_urn)

    location_name = profile_entity.get("locationName")
    if not location_name and geo_entity:
        location_name = geo_entity.get("defaultLocalizedName")

    # Extract country code from profile location
    country_code = profile_entity.get("location", {}).get("countryCode")

    # Extract supported languages from profile locales
    supported_raw = profile_entity.get("supportedLocales") or []
    supported_locales = [loc.get("language") for loc in supported_raw if loc.get("language")]

    profile_picture_url = vector_image_url(
        ((profile_entity.get("profilePicture") or {}).get("displayImageReference") or {}).get(
            "vectorImage"
        )
    )

    current_position = next(
        (p for p in positions if p.date_range is None or p.date_range.end is None),
        None,
    )

    # Assemble data for dataclass validation
    profile_data = {
        "urn": profile_entity["entityUrn"],
        "first_name": first_name,
        "last_name": last_name,
        "full_name": f"{first_name} {last_name}".strip() or None,
        "headline": profile_entity.get("headline"),
        "summary": profile_entity.get("summary"),
        "public_identifier": profile_entity.get("publicIdentifier"),
        "location_name": location_name,
        "geo": geo_entity,
        "industry": _resolve_star_field(profile_entity, urn_map, "*industry"),
        "country_code": country_code,
        "supported_locales": supported_locales,
        "url": f"https://www.linkedin.com/in/{profile_entity.get('publicIdentifier', '')}/",
        "profile_picture_url": profile_picture_url,
        "positions": positions,
        "educations": educations,
        "current_position": current_position,
        "connection_distance": connection_distance,
        "connection_degree": connection_degree,
    }

    # Validate with dataclass (will raise if something is wrong)
    profile_obj = LinkedInProfile(**profile_data)

    # Return clean dictionary – perfect for JSON, APIs, logging, etc.
    return asdict(profile_obj)
