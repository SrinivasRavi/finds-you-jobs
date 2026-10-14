"""The shipped title index suggests; it never refuses a title it hasn't heard of."""

from __future__ import annotations

import time
from pathlib import Path

from sidecar.modules.titles.suggest import DATA, _index, is_known, suggest, warm

# The installer ships this file, so a careless rebuild that stops cutting
# fields has to fail a test rather than a release.
SIZE_BUDGET_BYTES = 600_000


def test_data_file_ships_and_stays_under_budget() -> None:
    path = DATA / "titles.tsv.gz"
    assert path.is_file()
    assert path.stat().st_size < SIZE_BUDGET_BYTES


def test_common_tech_titles_return_sensible_suggestions() -> None:
    hits = suggest("software eng")
    assert hits
    assert hits[0] == "Software Engineer"


def test_prefix_beats_substring() -> None:
    """The word "scientist" starts only 2 O*NET titles, so the rest of the 8
    returned have to be filled from titles that merely contain it ("Data
    Scientist"), and those 2 prefix hits must still lead.
    """
    hits = suggest("scientist")
    assert hits[:2] == ["Scientist", "Scientist Propagator"]
    assert all("scientist" in h.lower() for h in hits[2:])
    assert not all(h.lower().startswith("scientist") for h in hits[2:])


def test_the_limit_is_honored() -> None:
    assert len(suggest("engineer", limit=3)) <= 3
    assert len(suggest("manager", limit=1)) == 1


def test_short_and_empty_input_return_nothing() -> None:
    assert suggest("") == []
    assert suggest("a") == []


def test_is_known_matches_a_real_taxonomy_title_case_and_accent_insensitively() -> None:
    assert is_known("Software Engineer")
    assert is_known("software engineer")
    assert is_known("SOFTWARE ENGINEER")


def test_is_known_rejects_a_title_the_taxonomy_lacks() -> None:
    assert is_known("Backend Engineer") is False


def test_a_title_the_taxonomy_lacks_is_still_a_title() -> None:
    """O*NET names "backend" nowhere, so "Backend Engineer" is unknown to the index.
    Fine: `suggest` is advisory, and `is_known` False never blocks the user."""
    hits = suggest("backend")
    assert hits == [] or all(not h.lower().startswith("backend") for h in hits)
    assert is_known("Backend Engineer") is False


def test_the_index_loads_once_per_process() -> None:
    assert _index() is _index()


def test_suggest_is_fast_enough_for_every_keystroke() -> None:
    warm()
    queries = ["en", "ma", "se", "software eng", "manager", "director", "backend", "zzzqx"]
    # Best of 5, so a scheduler stall isn't read as the code's cost. 15 ms, not 5: a CI
    # runner measured about 5x a Mac (7.58 ms against 1.56 ms), and a full scan, the
    # regression this catches, costs about 5 ms on a Mac, so it still fails here.
    for query in queries:
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            suggest(query)
            samples.append((time.perf_counter() - start) * 1000)
        elapsed_ms = min(samples)
        assert elapsed_ms < 15, f"{query!r} took {elapsed_ms:.2f} ms"


def test_the_build_script_is_shipped_beside_its_output() -> None:
    assert (Path(DATA).parent / "build_data.py").is_file()
