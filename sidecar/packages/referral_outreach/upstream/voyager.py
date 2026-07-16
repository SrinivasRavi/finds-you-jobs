# voyager_py/voyager.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# Forked verbatim from OpenOutreach `linkedin/api/voyager.py` @ a7a9101.
# This is the load-bearing IP of the fork: the parser that turns a raw
# LinkedIn Voyager profile response (data + included graph) into a clean,
# JSON-serialisable dict. Pure — no I/O, no network.
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

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


def _vector_image_url(vector_img: Optional[dict], target_width: int = 400) -> Optional[str]:
    """Resolve a Voyager vectorImage to a displayable URL.

    Picks the artifact with width closest to ``target_width`` (artifacts are
    typically 100/200/400/800 px) and joins it to ``rootUrl``.
    """
    if not vector_img:
        return None
    root = vector_img.get("rootUrl")
    artifacts = vector_img.get("artifacts") or []
    if not root or not artifacts:
        return None
    chosen = min(artifacts, key=lambda a: abs(a.get("width", 0) - target_width))
    seg = chosen.get("fileIdentifyingUrlPathSegment", "")
    return root + seg if seg else None


def _company_logo_url(company: Optional[dict]) -> Optional[str]:
    if not company:
        return None
    return _vector_image_url((company.get("logo") or {}).get("vectorImage"))


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
# Messaging: last-message direction + timestamp (contact-sync probe, FR-NW-15)
# ======================
#
# NEW code for the finds-you-jobs fork (GPL subtree) — the read-only messaging
# probe the contact-status sync engine needs (never a send). Pure: given a
# raw Voyager conversations response (Messenger dash `conversations` decoration)
# and the TARGET member's URN, decide whether the last event in the 1:1 thread
# was sent by us (`me`) or by them (`them`), and when. Defensive throughout:
# any missing/unexpected shape degrades to (None, None) so a live parse miss
# turns into "no transition this tick", never a crash (the honest-failure ethos).


def _urn_member_id(urn: Optional[str]) -> str:
    """The trailing member-id fragment of any LinkedIn URN, for cross-format
    comparison (a message `*sender` fsd_profile URN vs the target's fsd_profile
    URN). Empty when there is no urn."""
    if not urn:
        return ""
    tail = str(urn).rstrip(")").split(":")[-1]
    return tail.split(",")[-1].strip()


def _message_events(conversation: dict) -> List[dict]:
    """The message events of one conversation, newest-last. Covers both the dash
    `messages` embedding and the legacy `events` list."""
    for key in ("messages", "events"):
        seq = conversation.get(key)
        if isinstance(seq, dict):
            seq = seq.get("elements")
        if isinstance(seq, list) and seq:
            return seq
    return []


def _event_sender_urn(event: dict) -> str:
    """The sender's profile URN of one message event, across decoration shapes."""
    sender = event.get("sender") or event.get("*sender") or {}
    if isinstance(sender, str):
        return sender
    if isinstance(sender, dict):
        for key in ("*hostIdentityUrn", "hostIdentityUrn", "entityUrn", "*participant",
                    "participant", "*miniProfile"):
            val = sender.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _event_timestamp(event: dict) -> Optional[float]:
    """Delivered/created epoch-seconds of one message event (LinkedIn stores ms)."""
    for key in ("deliveredAt", "createdAt", "lastActivityAt"):
        raw = event.get(key)
        if isinstance(raw, (int, float)) and raw > 0:
            return float(raw) / 1000.0 if raw > 1e11 else float(raw)
    return None


def parse_last_message(
    json_response: dict, target_urn: Optional[str]
) -> tuple[Optional[str], Optional[float]]:
    """(direction, epoch_seconds) of the most recent message with `target_urn`.

    `direction` is `"them"` when the target sent the last message, `"me"` when we
    did, and `None` when there is no readable 1:1 history. The decision rests only
    on the last event's sender: sender member-id == target member-id ⇒ `them`,
    else ⇒ `me`. Pure + defensive — any unexpected shape returns (None, None)."""
    target_id = _urn_member_id(target_urn)
    conversations = json_response.get("elements")
    if not isinstance(conversations, list):
        conversations = json_response.get("included") if isinstance(
            json_response.get("included"), list
        ) else []

    best_ts: Optional[float] = None
    best_dir: Optional[str] = None
    for conv in conversations:
        if not isinstance(conv, dict):
            continue
        events = _message_events(conv)
        for event in events:
            if not isinstance(event, dict):
                continue
            ts = _event_timestamp(event)
            if ts is None:
                continue
            if best_ts is None or ts >= best_ts:
                sender_id = _urn_member_id(_event_sender_urn(event))
                direction = "them" if (target_id and sender_id == target_id) else "me"
                best_ts, best_dir = ts, direction
    return best_dir, best_ts


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

    profile_picture_url = _vector_image_url(
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
