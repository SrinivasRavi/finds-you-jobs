"""The resolver against every location string on the maintainer's install.

58 distinct values, hand-labelled 2026-10-01
(evidence/2026-10-01-location-resolution): 19 match, 22 miss, 17 unverifiable.
The bar is 0 match-versus-miss errors; moving a case to or from unverifiable is
a policy change, made here on purpose."""

from __future__ import annotations

import time

import pytest

from sidecar.modules.geo.resolve import (
    Attendance,
    Verdict,
    match,
    resolve_location,
    resolve_wanted,
)

# The install's own preferences: Bengaluru plus Remote, user in India.
WANTED_CHIPS = ("Bengaluru", "Remote")

CASES: tuple[tuple[str, Verdict], ...] = (
    ("Bengaluru, India", Verdict.MATCH),
    ("Remote - USA", Verdict.MISS),
    ("Remote; San Francisco", Verdict.UNVERIFIABLE),
    ("Remote, USA", Verdict.MISS),
    ("Bengaluru, Karnataka, India", Verdict.MATCH),
    ("Bangalore, India - Remote", Verdict.MATCH),
    ("Remote; San Francisco-HQ", Verdict.UNVERIFIABLE),
    ("Remote, US ", Verdict.MISS),
    ("United States (Remote)", Verdict.MISS),
    ("Remote; US - Seattle", Verdict.UNVERIFIABLE),
    ("Remote; Sao Paulo, Brazil", Verdict.UNVERIFIABLE),
    ("Remote; San Francisco, California; New York, New York", Verdict.UNVERIFIABLE),
    ("Remote; New Jersey, United States", Verdict.UNVERIFIABLE),
    ("Remote, United States", Verdict.MISS),
    ("Remote - Ireland", Verdict.MISS),
    ("Remote - Canada", Verdict.MISS),
    ("India (Remote-First)", Verdict.MATCH),
    ("Bengaluru", Verdict.MATCH),
    ("Bangalore - Engineering", Verdict.MATCH),
    (" -REMOTE, USA-", Verdict.MISS),
    ("USA Remote", Verdict.MISS),
    ("Remote; US-WA-Bellevue; US-CA-Menlo Park", Verdict.UNVERIFIABLE),
    ("Remote; US-CA-Menlo Park; US-WA-Bellevue", Verdict.UNVERIFIABLE),
    ("Remote; US-CA-Menlo Park", Verdict.UNVERIFIABLE),
    ("Remote; Toronto", Verdict.UNVERIFIABLE),
    ("Remote; Santa Clara; Sydney", Verdict.UNVERIFIABLE),
    ("Remote; Pune", Verdict.UNVERIFIABLE),
    ("Remote; New York City", Verdict.UNVERIFIABLE),
    ("Remote; IN-Bangalore-MSO", Verdict.MATCH),
    ("Remote; Hyderabad, India", Verdict.UNVERIFIABLE),
    ("Remote; DE-Berlin-Trion Building", Verdict.UNVERIFIABLE),
    ("Remote; Bengaluru-HQ", Verdict.MATCH),
    ("Remote; Abu Dhabi, UAE", Verdict.UNVERIFIABLE),
    (
        "Remote-Friendly (Travel-Required) | San Francisco, CA | Seattle, WA | New York City, NY",
        Verdict.UNVERIFIABLE,
    ),
    ("Remote, US", Verdict.MISS),
    ("Remote US", Verdict.MISS),
    ("Remote Canada", Verdict.MISS),
    ("Remote - US", Verdict.MISS),
    ("Remote - Singapore", Verdict.MISS),
    ("Remote - SF Bay Area", Verdict.MISS),
    ("Remote - New York", Verdict.MISS),
    ("Remote - India ", Verdict.MATCH),
    ("Remote - India", Verdict.MATCH),
    ("Remote - Europe or Remote America", Verdict.MISS),
    ("Remote - California", Verdict.MISS),
    ("Remote (North America)", Verdict.MISS),
    ("Remote (Global); Brooklyn, NY; Remote (USA); HQ", Verdict.MATCH),
    ("New York, NY; Silicon Valley, CA; USA Remote", Verdict.MISS),
    ("Mumbai, India", Verdict.MISS),
    ("Japan - Remote", Verdict.MISS),
    ("India - Bangalore", Verdict.MATCH),
    ("India (Bengaluru)", Verdict.MATCH),
    ("IN-Karnataka-Bangalore", Verdict.MATCH),
    ("Hybrid - Bangalore, India", Verdict.MATCH),
    ("Bengaluru, Karnataka, India, APAC", Verdict.MATCH),
    ("Bangalore, India", Verdict.MATCH),
    ("Bangalore, INDIA", Verdict.MATCH),
    ("Bangalore", Verdict.MATCH),
)


@pytest.fixture(scope="module")
def wanted():
    return resolve_wanted(WANTED_CHIPS)


@pytest.mark.parametrize(("location", "expected"), CASES)
def test_every_real_location_string_gets_its_labelled_verdict(
    location: str, expected: Verdict, wanted
) -> None:
    verdict, why = match(resolve_location(location), wanted)
    assert verdict == expected, f"{location!r} -> {verdict} ({why})"
    assert why


def test_no_case_flips_between_match_and_miss(wanted) -> None:
    """The one bar that matters: a wrong match sends the user to a job they cannot
    take, and a wrong miss hides one they can."""
    hard_errors = []
    for location, expected in CASES:
        verdict, _ = match(resolve_location(location), wanted)
        if {verdict, expected} == {Verdict.MATCH, Verdict.MISS}:
            hard_errors.append(location)
    assert hard_errors == []


def test_the_preference_chips_resolve_to_entities(wanted) -> None:
    assert wanted.remote is True
    assert 1277333 in wanted.city_ids
    assert wanted.home_countries == frozenset({"IN"})
    # A city chip must not make the whole country wanted, or every Mumbai job
    # would read as a match.
    assert wanted.countries == frozenset()
    assert wanted.unresolved == ()


def test_a_division_chip_matches_any_city_inside_it() -> None:
    wanted = resolve_wanted(("California",))
    verdict, why = match(resolve_location("San Jose, CA"), wanted)
    assert verdict == Verdict.MATCH
    assert "inside a region" in why


def test_a_country_chip_matches_any_city_inside_it() -> None:
    wanted = resolve_wanted(("India",))
    assert match(resolve_location("Pune"), wanted)[0] == Verdict.MATCH
    assert match(resolve_location("Mumbai, India"), wanted)[0] == Verdict.MATCH
    assert match(resolve_location("Berlin, Germany"), wanted)[0] == Verdict.MISS


def test_a_metro_nickname_matches_the_cities_it_stands_for() -> None:
    wanted = resolve_wanted(("Bay Area",))
    assert match(resolve_location("Menlo Park, CA"), wanted)[0] == Verdict.MATCH
    assert match(resolve_location("Remote - SF Bay Area"), wanted)[0] == Verdict.MATCH
    assert match(resolve_location("Austin, TX"), wanted)[0] == Verdict.MISS


def test_a_chip_naming_no_place_is_reported_not_swallowed() -> None:
    wanted = resolve_wanted(("Bengaluru", "Narnia"))
    assert wanted.unresolved == ("Narnia",)
    assert 1277333 in wanted.city_ids


def test_no_preference_means_nothing_to_compare() -> None:
    verdict, why = match(resolve_location("Bengaluru, India"), resolve_wanted(()))
    assert verdict == Verdict.UNVERIFIABLE
    assert "No location preference" in why


def test_the_remote_scope_is_storable_as_one_string() -> None:
    assert resolve_location("Bengaluru, India").remote_scope == ""
    assert resolve_location("Remote (Global)").remote_scope == "global"
    assert resolve_location("Remote; San Francisco").remote_scope == "unscoped"
    assert resolve_location("Remote - USA").remote_scope == "US"
    assert resolve_location("Remote - North America").remote_scope == "CA,MX,US"


def test_attendance_is_read_separately_from_eligibility() -> None:
    assert resolve_location("Hybrid - Bangalore, India").attendance == Attendance.HYBRID
    assert resolve_location("Remote - India").attendance == Attendance.REMOTE
    assert resolve_location("Bengaluru, India").attendance is None


def test_one_city_gives_a_primary_and_several_give_their_shared_country() -> None:
    single = resolve_location("Bengaluru, India").primary
    assert single is not None and single.city == "Bengaluru"
    shared = resolve_location("Remote; US-WA-Bellevue; US-CA-Menlo Park").primary
    assert shared is not None and shared.country == "US" and shared.city is None
    assert resolve_location("Remote; Santa Clara; Sydney").primary is None


def test_an_ambiguous_name_is_flagged_so_a_caller_can_ask() -> None:
    assert "washington" in resolve_location("Washington").ambiguous


def test_empty_and_junk_never_raise() -> None:
    for text in ("", "   ", "-", "???", "Various"):
        resolved = resolve_location(text)
        assert resolved.places == ()
        assert match(resolved, resolve_wanted(WANTED_CHIPS))[0] == Verdict.UNVERIFIABLE


def test_resolving_a_posting_stays_well_under_a_millisecond(wanted) -> None:
    resolve_location("warm the index")
    start = time.perf_counter()
    for location, _ in CASES:
        match(resolve_location(location), wanted)
    per_call_ms = (time.perf_counter() - start) * 1000 / len(CASES)
    assert per_call_ms < 1.0, f"{per_call_ms:.3f} ms per call"


@pytest.mark.parametrize(
    ("text", "city", "country"),
    [("Ufa, Russia", "Ufa", "RU"), ("Hue, Vietnam", "Huế", "VN"), ("Fes, Morocco", "Fes", "MA")],
)
def test_a_3_letter_city_of_200k_or_more_resolves_to_the_city(
    text: str, city: str, country: str
) -> None:
    """3-letter names had no alias row, so Ufa (1.1M people) read as Russia only
    (backlog S-C76)."""
    places = resolve_location(text).places
    assert [(p.city, p.country) for p in places] == [(city, country)]
