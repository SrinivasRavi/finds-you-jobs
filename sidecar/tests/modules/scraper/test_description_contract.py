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
