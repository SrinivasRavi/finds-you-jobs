"""Places with the same name, on both sides (docs/internal/place-picker.md).

The posting side reads a location as a unit, so the pieces after a city choose
which city it is. The preference side never guesses: the picker suggests, the
user picks, and the chip stores the place itself.
"""

from __future__ import annotations

import pytest

from sidecar.modules.geo.gazetteer import gazetteer
from sidecar.modules.geo.resolve import Verdict, match, resolve_location, resolve_wanted
from sidecar.modules.geo.suggest import suggest

# (posting text, (country, admin1 key, city or None)) for the 1 place it means.
POSTINGS = [
    # The cases that started this: the piece after the city picks the city.
    ("London, ON", ("CA", "CA.08", "London")),
    ("London, Ontario", ("CA", "CA.08", "London")),
    ("London, ON, Canada", ("CA", "CA.08", "London")),
    ("London, UK", ("GB", "GB.ENG", "London")),
    ("London, England", ("GB", "GB.ENG", "London")),
    ("London, United Kingdom", ("GB", "GB.ENG", "London")),
    ("Portland, ME", ("US", "US.ME", "Portland")),
    ("Portland, OR", ("US", "US.OR", "Portland")),
    ("Portland, Oregon", ("US", "US.OR", "Portland")),
    ("Cambridge, MA", ("US", "US.MA", "Cambridge")),
    ("Cambridge, UK", ("GB", "GB.ENG", "Cambridge")),
    ("Cambridge, ON", ("CA", "CA.08", "Cambridge")),
    # A 2-letter code that is also a country, beside a city in that US state.
    ("Ontario, CA", ("US", "US.CA", "Ontario")),
    ("Dublin, CA", ("US", "US.CA", "Dublin")),
    ("Dublin, OH", ("US", "US.OH", "Dublin")),
    ("Dublin, Ireland", ("IE", None, "Dublin")),
    ("Birmingham, AL", ("US", "US.AL", "Birmingham")),
    ("Paris, TX", ("US", "US.TX", "Paris")),
    ("Paris, France", ("FR", None, "Paris")),
    ("Kingston, ON", ("CA", "CA.08", "Kingston")),
    ("Kingston, Jamaica", ("JM", None, "Kingston")),
    ("Kingston, NY", ("US", "US.NY", "Kingston")),
    # Same name as a state or a country, read from what follows.
    ("Seattle, Washington", ("US", "US.WA", "Seattle")),
    ("Washington, DC", ("US", "US.DC", "Washington")),
    ("Atlanta, Georgia", ("US", "US.GA", "Atlanta")),
    ("Tbilisi, Georgia", ("GE", None, "Tbilisi")),
    # The division codes GeoNames numbers: Canada and Australia.
    ("Victoria, BC", ("CA", "CA.02", "Victoria")),
    ("Toronto, ON", ("CA", "CA.08", "Toronto")),
    ("Montreal, QC", ("CA", "CA.10", "Montreal")),
    ("Calgary, AB", ("CA", "CA.01", "Calgary")),
    ("Sydney, NSW", ("AU", "AU.02", "Sydney")),
    ("Sydney, NS", ("CA", "CA.07", "Sydney")),
    ("Perth, WA", ("AU", "AU.08", "Perth")),
    ("Perth, Scotland", ("GB", "GB.SCT", "Perth")),
    ("Hyderabad, Pakistan", ("PK", None, "Hyderabad")),
    ("Hyderabad, Telangana", ("IN", "IN.40", "Hyderabad")),
    # Accents and case never change the answer.
    ("Montréal, QC", ("CA", "CA.10", "Montreal")),
    ("LONDON, ON", ("CA", "CA.08", "London")),
    # Unchanged shapes: a bare city, an ATS code, a remote segment.
    ("Bengaluru", ("IN", "IN.19", "Bengaluru")),
    ("US-CA-Menlo Park", ("US", "US.CA", "Menlo Park")),
    ("Hybrid - London, ON", ("CA", "CA.08", "London")),
]


@pytest.mark.parametrize(("text", "want"), POSTINGS)
def test_a_posting_location_is_read_as_a_unit(text: str, want: tuple) -> None:
    primary = resolve_location(text).primary
    assert primary is not None, text
    country, admin1, city = want
    assert primary.country == country, text
    if admin1 is not None:
        assert primary.admin1_key == admin1, text
    if city is not None:
        assert primary.city is not None and primary.city.lower().startswith(city.lower()[:5]), text


def test_a_qualifier_never_becomes_a_second_wrong_place() -> None:
    """`ME` beside Portland is Maine, not Montenegro as well."""
    for text in ("Portland, ME", "Cambridge, MA", "London, ON, Canada"):
        countries = resolve_location(text).countries
        assert len(countries) == 1, (text, countries)


def test_a_city_not_in_the_named_division_resolves_to_the_division() -> None:
    """Honest over wrong: a qualifier naming a division that holds none of the
    candidates means the city isn't in our cut, so the division is the place."""
    resolved = resolve_location("London, Nova Scotia")
    assert resolved.primary is not None
    assert resolved.primary.admin1_key == "CA.07"
    assert resolved.primary.city is None


def test_without_a_qualifier_population_decides_and_it_stays_ambiguous() -> None:
    resolved = resolve_location("London")
    assert resolved.primary is not None and resolved.primary.country == "GB"
    assert "london" in resolved.ambiguous


def test_several_segments_each_read_as_a_unit() -> None:
    resolved = resolve_location("London, ON or Portland, ME")
    assert {p.admin1_key for p in resolved.places if p.city} == {"CA.08", "US.ME"}


def test_unknown_and_empty_text_resolve_to_nothing() -> None:
    assert resolve_location("").places == ()
    assert resolve_location("Xyzzyville, QQ").primary is None


def test_the_gazetteer_keeps_every_city_of_a_name_biggest_first() -> None:
    londons = gazetteer().cities_named("london")
    assert [c.country for c in londons[:2]] == ["GB", "CA"]


# --- the preference side: suggestions ------------------------------------


def _labels(q: str) -> list[str]:
    return [s.label for s in suggest(q)]


def test_typing_london_offers_both_londons_named_in_full() -> None:
    labels = _labels("london")
    assert labels[0] == "London, England, United Kingdom"
    assert "London, Ontario, Canada" in labels


def test_a_borough_named_after_a_city_is_not_offered_as_that_city() -> None:
    labels = _labels("london")
    assert not any(label.startswith(("Brent", "Bexley")) for label in labels)


def test_a_one_word_alternate_spelling_still_finds_the_city() -> None:
    assert any(label.startswith("Bengaluru") for label in _labels("banga"))


def test_a_prefix_lists_the_biggest_city_first() -> None:
    assert _labels("lon")[0] == "London, England, United Kingdom"


def test_a_province_outranks_a_city_of_the_same_name() -> None:
    labels = _labels("ontario")
    assert labels[0] == "Ontario, Canada"
    assert "Ontario, California, United States" in labels


def test_a_country_comes_first_when_it_matches() -> None:
    assert _labels("can")[0] == "Canada"
    assert _labels("india")[0] == "India"


def test_remote_regions_and_metros_are_offered() -> None:
    assert _labels("remo")[0] == "Remote"
    assert "Bay Area, United States" in _labels("bay ar")
    assert "Europe" in _labels("euro")


def test_accents_and_case_do_not_matter() -> None:
    assert any(label.startswith("Zürich") for label in _labels("zurich"))
    assert _labels("LONDON")[0] == "London, England, United Kingdom"


def test_suggestions_are_unique_and_bounded() -> None:
    labels = _labels("san")
    assert len(labels) == len(set(labels)) <= 8


def test_nothing_typed_suggests_nothing() -> None:
    assert suggest("") == []
    assert suggest("   ") == []
    assert suggest("qqqqzzzz") == []


def test_a_suggestion_carries_what_a_chip_stores() -> None:
    top = suggest("london ontario")[0] if suggest("london ontario") else None
    picked = next(s for s in suggest("london") if s.label == "London, Ontario, Canada")
    assert picked.name == "London" and picked.level == "city" and picked.id == "6058560"
    assert top is None or top.label == "London, Ontario, Canada"


# --- the preference side: a picked chip is never re-guessed ---------------


def _chip(label: str) -> dict:
    found = next(s for s in suggest(label.split(",")[0]) if s.label == label)
    return found.as_chip()


def test_a_picked_london_ontario_matches_only_london_ontario() -> None:
    wanted = resolve_wanted([_chip("London, Ontario, Canada")])
    assert match(resolve_location("London, ON"), wanted)[0] is Verdict.MATCH
    assert match(resolve_location("London, UK"), wanted)[0] is Verdict.MISS


def test_picking_both_londons_matches_both() -> None:
    wanted = resolve_wanted(
        [_chip("London, Ontario, Canada"), _chip("London, England, United Kingdom")]
    )
    assert match(resolve_location("London, ON"), wanted)[0] is Verdict.MATCH
    assert match(resolve_location("London, UK"), wanted)[0] is Verdict.MATCH


def test_a_picked_province_country_metro_and_remote_resolve_exactly() -> None:
    wanted = resolve_wanted(
        [_chip("Ontario, Canada"), _chip("India"), _chip("Bay Area, United States")]
        + [suggest("remote")[0].as_chip()]
    )
    assert "CA.08" in wanted.admin1_keys
    assert "IN" in wanted.countries
    assert wanted.remote
    assert match(resolve_location("Palo Alto, CA"), wanted)[0] is Verdict.MATCH
    assert match(resolve_location("Kingston, ON"), wanted)[0] is Verdict.MATCH


def test_a_legacy_string_chip_still_works() -> None:
    wanted = resolve_wanted(["Bengaluru"])
    assert match(resolve_location("Bangalore, India"), wanted)[0] is Verdict.MATCH


def test_a_typed_chip_matching_no_place_is_unresolved_not_dropped() -> None:
    wanted = resolve_wanted([{"label": "Moonbase", "name": "Moonbase", "level": "text", "id": ""}])
    assert wanted.unresolved == ("Moonbase",)


def test_the_suggest_route_answers_the_picker(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from sidecar.app.main import create_app

    token = "test-token-places"  # noqa: S105 — test fixture
    app = create_app(
        token=token, original_ppid=None, data_dir=tmp_path / "data", enable_scheduler=False
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/geo/suggest", params={"q": "london"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert rows[0] == {
            "label": "London, England, United Kingdom", "name": "London",
            "level": "city", "id": "2643743",
        }
        assert any(r["label"] == "London, Ontario, Canada" for r in rows)


def test_each_consumer_gets_the_right_part_of_a_picked_chip() -> None:
    """Discovery's word filter and the search sources get the short name, the
    same word as before the picker; LLM scoring's prompt gets the full label so
    the model knows which London (place-picker.md section 5)."""
    from types import SimpleNamespace

    from sidecar.app.registry.persistence import render_preferences_block
    from sidecar.modules.geo.resolve import chip_label, chip_name

    chip = next(s for s in suggest("london") if s.label == "London, Ontario, Canada").as_chip()
    assert chip_name(chip) == "London"
    assert chip_name("Bengaluru") == "Bengaluru"
    assert chip_label(chip) == "London, Ontario, Canada"
    block = render_preferences_block(SimpleNamespace(role_aliases=["SWE"], locations=[chip]))
    assert "London, Ontario, Canada" in block


# --- audit A7: a metro's own cities, and a wanted region at scan time ----------


def test_a_metro_chip_takes_its_cities_in_its_own_country() -> None:
    """The biggest "Santa Clara" is in Cuba; the Bay Area's is in California."""
    from sidecar.modules.geo.resolve import Verdict, match, resolve_location, resolve_wanted

    wanted = resolve_wanted([{"label": "Bay Area, United States", "name": "Bay Area",
                              "level": "metro", "id": "bay area"}])
    assert match(resolve_location("Santa Clara, California"), wanted)[0] is Verdict.MATCH
    assert match(resolve_location("San Jose, CA"), wanted)[0] is Verdict.MATCH
    assert match(resolve_location("Los Angeles, CA"), wanted)[0] is Verdict.MISS


def test_the_scan_keeps_a_posting_inside_a_wanted_region() -> None:
    """A word match drops "Pune, India" for a Maharashtra chip; the place test keeps it,
    and a posting outside every wanted place is still dropped."""
    from dataclasses import replace

    from sidecar.app.registry.persistence import _expand_locations, _with_place_match
    from sidecar.modules.scraper.filters import passes_location
    from sidecar.modules.scraper.types import ScanPrefs

    chip = {"label": "Maharashtra, India", "name": "Maharashtra", "level": "admin1",
            "id": "IN.16"}
    words = _expand_locations(["Maharashtra"])
    base = ScanPrefs(location_allow=words, location_always_allow=words)
    assert passes_location("Pune, India", base) is False  # the defect, word match only
    prefs = _with_place_match(base, [chip])
    assert passes_location("Pune, India", prefs) is True
    assert passes_location("Mumbai", prefs) is True
    assert passes_location("Bengaluru", prefs) is False
    # A block still wins over a wanted place.
    assert passes_location("Pune, India", replace(prefs, location_block=["pune"])) is False
    # Chips that name no place leave the word filter alone.
    assert _with_place_match(base, ["Somewhere Unknown"]) is base
