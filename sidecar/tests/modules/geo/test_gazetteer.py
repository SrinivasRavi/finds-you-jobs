"""The shipped gazetteer holds the places postings actually name, and stays small."""

from __future__ import annotations

from pathlib import Path

from sidecar.modules.geo.gazetteer import DATA, City, fold, gazetteer

# The installer is 101 MB and this data ships inside it, so a careless rebuild
# that stops cutting fields has to fail a test rather than a release.
SIZE_BUDGET_BYTES = 2_500_000


def test_data_files_ship_and_stay_under_budget() -> None:
    files = sorted(DATA.glob("*.tsv.gz"))
    assert [f.name for f in files] == [
        "admin1.tsv.gz",
        "aliases.tsv.gz",
        "cities.tsv.gz",
        "countries.tsv.gz",
    ]
    assert sum(f.stat().st_size for f in files) < SIZE_BUDGET_BYTES


def test_both_spellings_of_bengaluru_are_one_city() -> None:
    g = gazetteer()
    bengaluru = g.city(fold("Bengaluru"))
    bangalore = g.city(fold("Bangalore"))
    assert bengaluru is not None and bangalore is not None
    assert bengaluru == bangalore
    assert bengaluru.geonameid == 1277333
    assert bengaluru.country == "IN"
    assert bengaluru.admin1_key == "IN.19"


def test_folding_absorbs_case_accents_and_dashes() -> None:
    g = gazetteer()
    for spelling in ("SÃO PAULO", "Sao Paulo", "são paulo"):
        city = g.city(fold(spelling))
        assert city is not None and city.country == "BR"


def test_a_name_naming_two_places_resolves_to_the_larger_and_says_so() -> None:
    g = gazetteer()
    assert "washington" in g.ambiguous
    city = g.city("washington")
    assert city is not None and city.admin1_key == "US.DC"
    # Hyderabad is in both India and Pakistan; the Indian one is 3x the size.
    assert "hyderabad" in g.ambiguous
    hyderabad = g.city("hyderabad")
    assert hyderabad is not None and hyderabad.country == "IN"


def test_divisions_and_countries_resolve_by_name_and_by_code() -> None:
    g = gazetteer()
    assert g.admin1("karnataka") == "IN"
    assert g.admin1("california") == "US"
    assert g.admin1("wa") == "US"
    assert g.country("india") == "IN"
    assert g.country("united states") == "US"
    assert g.country("usa") == "US"
    assert g.country("us") == "US"


def test_an_unknown_name_resolves_to_nothing_rather_than_a_guess() -> None:
    g = gazetteer()
    assert g.city("not a place at all") is None
    assert g.admin1("not a place at all") is None
    assert g.country("not a place at all") is None


def test_the_index_loads_once_per_process() -> None:
    assert gazetteer() is gazetteer()


def test_a_city_knows_its_own_admin1_key() -> None:
    assert City(1, "Somewhere", "19", "IN", 10).admin1_key == "IN.19"


def test_the_build_script_is_shipped_beside_its_output() -> None:
    assert (Path(DATA).parent / "build_data.py").is_file()
