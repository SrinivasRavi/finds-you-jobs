"""The description contract (item 5, description-gap closure) — every
registered adapter either lands the JD inline in its own list request
(`INLINE_DESCRIPTION = True`) or implements `fetch_detail` for the scan's
enrich phase. A future adapter satisfying neither would silently produce
rows the scorer can never score (`MIN_JD_CHARS`), so this walks the live
registry rather than trusting each adapter file in isolation.
"""

from __future__ import annotations

from sidecar.modules.scraper.adapters import ADAPTERS
from sidecar.modules.scraper.adapters.base import provides_description

# Adapters whose list payload carries no JD body, so they fill it via
# fetch_detail in the scan's enrich phase; every other registered adapter
# declares INLINE_DESCRIPTION = True instead.
_FETCH_DETAIL_ADAPTERS = {"bamboohr", "breezy", "smartrecruiters", "workday", "linkedin"}


def test_every_registered_adapter_provides_a_description():
    missing = [a.ID for a in ADAPTERS if not provides_description(a)]
    assert not missing, f"adapter(s) with no description contract: {missing}"


def test_description_contract_mechanism_matches_expectation():
    """Pins which adapters use which mechanism, so a regression here flags an
    adapter's contract path changing without an intentional edit."""
    for adapter in ADAPTERS:
        if adapter.ID in _FETCH_DETAIL_ADAPTERS:
            assert hasattr(adapter, "fetch_detail"), f"{adapter.ID} should implement fetch_detail"
        else:
            assert getattr(adapter, "INLINE_DESCRIPTION", False), (
                f"{adapter.ID} should declare INLINE_DESCRIPTION = True"
            )


def test_provides_description_helper_reads_either_signal():
    class _InlineOnly:
        INLINE_DESCRIPTION = True

    class _DetailOnly:
        def fetch_detail(self, job, fetcher):  # noqa: ANN001, ANN201 — test double
            return ""

    class _Neither:
        pass

    assert provides_description(_InlineOnly())
    assert provides_description(_DetailOnly())
    assert not provides_description(_Neither())


def test_every_registered_adapter_declares_its_catalog_row():
    """The Settings › Discover jobs catalog is built from each adapter's own
    LABEL and KIND (audit D-M17), so a new adapter can't ship without one."""
    from sidecar.modules.scraper.adapters import CATALOG

    for adapter in ADAPTERS:
        assert adapter.LABEL, adapter.ID
        assert adapter.KIND in {"ats", "board", "search", "fallback"}, adapter.ID
    assert list(CATALOG) == [a.ID for a in ADAPTERS]
    assert ADAPTERS[-1].KIND == "fallback"  # rss claims any feed URL, so it goes last


def test_the_shared_detect_helpers_never_claim_an_entry_typed_for_another_adapter():
    from sidecar.modules.scraper.adapters.base import detect_board, detect_url
    from sidecar.modules.scraper.config import SourceEntry

    def tenant(url: str) -> str:
        return "acme"

    assert detect_url(SourceEntry(url="https://x.io/acme"), "x", tenant) == "acme"
    assert detect_url(SourceEntry(url="https://x.io/acme", type="y"), "x", tenant) == ""
    assert detect_url(SourceEntry(type="x"), "x", tenant) == ""
    assert detect_board(SourceEntry(board="b"), "b", "b.com", {"b.com"}) == "b.com"
    www = SourceEntry(url="https://www.b.com/x")
    assert detect_board(www, "b", "b.com", {"www.b.com"}) == "b.com"
    assert detect_board(SourceEntry(board="b", type="rss"), "b", "b.com", set()) == ""
    assert detect_board(SourceEntry(url="https://c.com"), "b", "b.com", {"b.com"}) == ""


def test_keyed_adapters_declare_what_discovery_reads():
    """Discovery builds its key list, seeding, sub-source toggles and analytics
    names from these declarations and names no vendor itself (F-P6)."""
    from pathlib import Path

    from sidecar.app.api import discovery

    keyed = [a for a in ADAPTERS if hasattr(a, "CREDENTIAL")]
    assert discovery.CREDENTIALS == {a.ID: a.CREDENTIAL for a in keyed}
    assert set(discovery.CREDENTIALS) == {"apify", "brave"}
    for adapter in keyed:
        assert adapter.DEFAULT_SOURCES, adapter.ID
        assert all(s["board"] == adapter.ID for s in adapter.DEFAULT_SOURCES), adapter.ID
    for adapter in (a for a in ADAPTERS if hasattr(a, "SUB_SOURCE_FIELD")):
        assert set(adapter.SUB_SOURCE_IDS) <= set(adapter.SUB_SOURCE_LABELS), adapter.ID
        assert all(s.get(adapter.SUB_SOURCE_FIELD) for s in adapter.DEFAULT_SOURCES)
    code = [
        line for line in Path(discovery.__file__).read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    assert not [line for line in code if "brave" in line.lower()]
    assert not [line for line in code if "apify" in line.lower() and "`apify:" not in line]
