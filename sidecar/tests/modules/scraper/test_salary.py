"""Salary parsing + salary filter — career-ops `salary_filter` parity.

The parser's contract is confidence, not coverage: a string it can't read
returns None, and the filter treats None as "no signal → pass" — an
unparsed salary must never cost the user a lead (rank-don't-gate).
"""

from __future__ import annotations

import pytest

from sidecar.modules.scraper.filters import passes_salary
from sidecar.modules.scraper.salary import SalaryRange, parse_salary
from sidecar.modules.scraper.types import ScanPrefs

# --- parser ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("$120k–$150k", SalaryRange(120_000, 150_000, "USD")),
        ("$120,000 - $150,000 per year", SalaryRange(120_000, 150_000, "USD")),
        ("€60,000", SalaryRange(60_000, 60_000, "EUR")),
        ("£85k", SalaryRange(85_000, 85_000, "GBP")),
        ("₹12L – ₹18L per annum", SalaryRange(1_200_000, 1_800_000, "INR")),
        ("12 to 18 lakhs INR", SalaryRange(1_200_000, 1_800_000, "INR")),
        ("$50/hr", SalaryRange(104_000, 104_000, "USD")),
        ("8000 EUR monthly", SalaryRange(96_000, 96_000, "EUR")),
        ("120000-150000 USD", SalaryRange(120_000, 150_000, "USD")),
        ("1.5 crore", SalaryRange(15_000_000, 15_000_000, "")),
        ("150k", SalaryRange(150_000, 150_000, "")),
    ],
)
def test_parse_salary_reads_common_shapes(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Competitive",
        "DOE",
        "Great benefits and 401k match",  # retirement plan, not pay
        "5 days a week, 8 hours",  # small unscaled numbers are noise
    ],
)
def test_parse_salary_returns_none_without_confidence(text):
    assert parse_salary(text) is None


def test_parse_salary_explicit_code_beats_symbol():
    assert parse_salary("$100k CAD") == SalaryRange(100_000, 100_000, "CAD")


# --- filter ---


def test_salary_filter_off_by_default():
    assert passes_salary("$30k", ScanPrefs())


def test_salary_filter_min_drops_below_range():
    prefs = ScanPrefs(salary_min=100_000, salary_currency="USD")
    assert not passes_salary("$60k–$80k", prefs)
    assert passes_salary("$90k–$120k", prefs)  # range overlaps the floor
    assert passes_salary("$150k", prefs)


def test_salary_filter_max_drops_above_range():
    prefs = ScanPrefs(salary_max=150_000, salary_currency="USD")
    assert not passes_salary("$180k–$220k", prefs)
    assert passes_salary("$140k–$180k", prefs)


def test_salary_filter_no_signal_always_passes():
    prefs = ScanPrefs(salary_min=100_000, salary_currency="USD")
    assert passes_salary("", prefs)
    assert passes_salary("Competitive, DOE", prefs)


def test_salary_filter_different_currency_passes():
    prefs = ScanPrefs(salary_min=100_000, salary_currency="USD")
    assert passes_salary("₹12L per annum", prefs)  # ≈$14k, but incomparable


def test_salary_filter_currencyless_posting_compares():
    prefs = ScanPrefs(salary_min=100_000, salary_currency="USD")
    assert not passes_salary("60k-80k", prefs)
