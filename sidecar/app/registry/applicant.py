"""Applicant details: the facts the Applier fills forms from (audit A5, 2026-10-13).

The record is `master_profiles.application_profile`, read from the resume by the
`extract` op and confirmed by the user in Settings > Applying before their first
apply. Work authorization is a list, 1 entry per country; the Applier's model picks
the entry for the job's country. The location a form receives is the posting's own.
"""

from __future__ import annotations

from typing import Any

# The 4 statuses an authorization entry can hold, in the editor's order.
WORK_AUTH_STATUSES = ("citizen", "permanent_resident", "visa", "needs_sponsorship")
_STATUS_WORDS = {
    "citizen": "citizen, no sponsorship needed",
    "permanent_resident": "permanent resident, no sponsorship needed",
    "visa": "authorized on a work visa, no sponsorship needed",
    "needs_sponsorship": "not authorized yet, needs visa sponsorship",
}
# Keys the user owns once they've saved the record; a later resume read keeps them.
_USER_KEYS = ("work_authorizations", "confirmed_at")
# Voluntary self-identification: an unset one is answered "I don't wish to answer"
# (maintainer, 2026-10-13). The value words the model reads for each select.
SELF_ID_KEYS = ("gender", "race_ethnicity", "veteran_status", "disability_status")
_SELF_ID_WORDS = {
    "veteran_status": {"yes": "a protected veteran", "no": "not a protected veteran"},
    "disability_status": {"yes": "has a disability", "no": "has no disability"},
}
_DECLINE = "I don't wish to answer (pick the form's decline option)"
_SKIP = {"source", "profile_version", "confirmed_at", "work_authorization"}


def missing_required(record: Any) -> list[str]:
    """The required fields still empty: a name and a location, nothing else
    (maintainer, 2026-10-13). The Applier leaves anything else it can't answer
    for the user at review."""
    rec = record if isinstance(record, dict) else {}
    missing = []
    if not str(rec.get("first_name") or rec.get("name") or "").strip():
        missing.append("name")
    if not str(rec.get("location") or "").strip():
        missing.append("location")
    return missing


def details_confirmed(record: Any) -> bool:
    """True once the user has reviewed and saved their details at least once."""
    return isinstance(record, dict) and bool(record.get("confirmed_at"))


def merge_extracted(existing: Any, extracted: dict[str, Any]) -> dict[str, Any]:
    """A fresh resume read, without losing what the user typed: once the record is
    confirmed, every non-empty field they saved wins, and the work authorizations
    and confirmation always carry over."""
    if not isinstance(existing, dict):
        return dict(extracted)
    merged = dict(extracted)
    for key in _USER_KEYS:
        if key in existing:
            merged[key] = existing[key]
    if details_confirmed(existing):
        for key, value in existing.items():
            if value not in (None, "", [], {}) and key not in ("source", "profile_version"):
                merged[key] = value
    return merged


def work_authorization_line(record: dict[str, Any]) -> str:
    """The authorizations as 1 fact line the model reads, or the legacy free-text
    `work_authorization` when no list was saved."""
    entries = [
        e for e in record.get("work_authorizations") or []
        if isinstance(e, dict) and str(e.get("country") or "").strip()
    ]
    if not entries:
        return str(record.get("work_authorization") or "").strip()
    parts = []
    for e in entries:
        words = _STATUS_WORDS.get(str(e.get("status") or ""), str(e.get("status") or ""))
        note = str(e.get("note") or "").strip()
        parts.append(f"{str(e['country']).strip()}: {words}" + (f" ({note})" if note else ""))
    return (
        "; ".join(parts)
        + ". Answer each authorization or sponsorship question for the country the job"
        " is in; a country not listed means not authorized there."
    )


def posting_place(job_location: str) -> tuple[str, str] | None:
    """(location label, country name) of the posting's first resolved place, or None
    when it names none (remote, unknown text), so the user's own location stands."""
    from sidecar.modules.geo.gazetteer import gazetteer
    from sidecar.modules.geo.resolve import resolve_location
    from sidecar.modules.geo.suggest import place_label

    g = gazetteer()
    resolved = resolve_location(job_location or "", g)
    for place in resolved.places:
        label = place_label(g, place)
        if label:
            return label, g.country_name.get(place.country or "", "")
    return None


def apply_facts(record: Any, job_location: str) -> dict[str, str]:
    """The USER FACTS the Applier fills from: every scalar field, the authorization
    line, and the posting's location in place of the user's (maintainer, 2026-10-13:
    "match the exact job posting's location for your own location")."""
    if not isinstance(record, dict):
        return {}
    facts = {
        str(k).replace("_", " "): str(v).strip()
        for k, v in record.items()
        if isinstance(v, str | int | float) and str(v).strip()
        and k not in _SKIP and k not in SELF_ID_KEYS
    }
    for key in SELF_ID_KEYS:
        value = str(record.get(key) or "").strip()
        words = _SELF_ID_WORDS.get(key, {})
        facts[key.replace("_", " ")] = (
            _DECLINE if value in ("", "decline") else words.get(value, value)
        )
    auth = work_authorization_line(record)
    if auth:
        facts["work authorization"] = auth
    place = posting_place(job_location)
    if place is not None:
        facts["location"], country = place
        if country:
            facts["country"] = country
    return facts
