"""Table-driven tests for the deterministic seniority-level extractor, anchored
to the 93 distinct job titles on the maintainer's install (measured 2026-10-01,
docs/internal/evidence/2026-10-01-location-resolution/README.md, "Titles").
`CORPUS_TITLES` below is a literal snapshot from

    sqlite3 ~/fyj-test-profiles/onboard1/data/db.sqlite "select distinct title from jobs;"

captured so this file runs on a machine with no such database."""

from __future__ import annotations

import time

import pytest

from sidecar.modules.scorer.level import LEVEL_ORDER, Level, extract_level

CORPUS_TITLES: tuple[str, ...] = (
    "Forward Deployed Engineer {Mumbai}",
    "Sr. Forward Deployed Engineer (FDE) - Digital Native Business ",
    "Forward Deployed Engineer (FDE), Financial Services- NYC ",
    "Senior/Staff   Forward Deployed Engineer, Applied AI",
    "Forward Deployed Engineer",
    "Forward Deployed Engineer, India",
    "Forward Deployed Engineer - UAE",
    "Forward Deployed Engineer (India)",
    "Sr. Staff/Staff Forward Deployed Engineer",
    "Full Stack Staff & Software Engineer, Consumer Monetization",
    "Staff & Software Engineer, Payments & Billing, Consumer Monetization",
    "Mobile / Full Stack Staff & Software Engineer, Consumer Monetization",
    "Software Engineer, Search Infrastructure",
    "Software Engineer, DevOps",
    "Software Engineer, Product Velocity",
    "Software Engineer, Infrastructure AI",
    "Staff Software Engineer, Data Platform",
    "Software Engineer - Java Platform",
    "Principal Software Engineer II - Interoperable Foundations",
    "Senior Software Engineer - Cortex AI Infrastructure",
    "Staff Software Engineer, Frontier Security Team",
    "Senior+ Software Engineer, Legal Tech",
    "Principal UI Software Engineer",
    "Senior Software Engineer in Test (SET) — Access Essentials",
    "Senior Software Engineer, Lifecycle Management",
    "Senior Software Engineer Salesforce",
    "Staff Software Engineer",
    "Staff Software Engineer, macOS",
    "Senior Software Engineer II",
    "Software Engineer II",
    "Senior/Staff Software Engineer",
    "Staff Software Engineer, Finance Automation",
    "Staff Software Engineer, Tech Lead, Payroll Onboarding",
    "Software Engineer, Model Capabilities",
    "Software Engineer Intern, Mobile (Winter 2027)",
    "Staff Software Engineer - NLP",
    "Senior Software Engineer I-Storage",
    "Staff Software Engineer - Files Platform ",
    "Senior Software Engineer- Blockchain Platform (Wallets, Liquidity & Bridging)",
    "Staff Software Engineer (Platform - Financial Engineering)",
    "Principal Software Engineer",
    "Senior Software Engineer",
    "AI ML Ops Software Engineer (Bangalore Hybrid)",
    "Senior Software Engineer, Fullstack, Rewards & Incentives",
    "Staff Software Engineer - RnD Enablement.",
    "Senior Software Engineer, GI",
    "Staff Software Engineer, GI ",
    "Senior Software Engineer (West Coast USA)",
    "Software Engineer II - Critical Email Products",
    "Software Engineer II -  Data Integration Platform (DIP) Ingestion",
    "Senior Staff Software Engineer",
    " Principal Software Engineer - IBM i (AS/400) Connectivity",
    "Principal Software Engineer – IBM z/OS Connectivity",
    "Senior Staff Software Engineer- Ai Governance",
    "Software Engineer",
    "Staff Systems and Platforms Software Engineer",
    "Senior Mobile Software Engineer (iOS / Android), Identity & Security - India",
    "Senior Software Engineer - Privileged Access Management (PAM / IAM)- India",
    "Software Engineer, Privileged Access Management (PAM) / Password Manager (PWM) - India",
    "Staff Software Engineer, Privileged Access Management (PAM) / Password Manager (PWM) - India",
    "Senior Software Engineer, Developer Platform",
    "Senior Software Engineer - Clearing",
    "Senior Software Engineer, Core",
    "Senior Software Engineer in Test",
    "Senior Software Engineer (Java & DSA)",
    "Software Engineer, AI",
    "IT Software Engineer, Infrastructure",
    "Software Engineer, Implicit Signals",
    "Staff Software Engineer ",
    "Staff Software Engineer-FullStack",
    "Staff Software Engineer, Lifecycle Management",
    "Senior Software Engineer II, Route Planning",
    "Software Engineer II, Android ",
    "Principal, Software Engineer (L5)",
    "Staff Software Engineer (L4)",
    "Staff Software Engineer - Customer Facing Applied AI ",
    "Senior Software Engineer, Backend - Overseer (Platform)",
    "Senior Software Engineer, Mobile QA",
    "Senior Software Engineer, Native Mobile",
    "Senior Staff Software Engineer, Native Mobile",
    "Software Engineer, CDP - Foundations ",
    "Staff Software Engineer, Mobile QA",
    "Staff Software Engineer, Native Mobile",
    "Senior Software Engineer II - Solution Lifecycle (Remote Eligible)",
    "Senior Software Engineer - Solution Lifecycle (Remote Eligible)",
    "Software Engineer, Passport & Commerce, iOS",
    "Software Engineer, Passport & Commerce, Web",
    "Software Engineer, Applied AI (Brazil)",
    "Software Engineer, Autonomous Revenue Operations (Brazil)",
    "Senior Software Engineer, Data",
    "Staff Software Engineer, Provider Engagement ",
    "Software Engineer - FullStack ",
    "Senior Staff Software Engineer (R14272)",
)


def test_corpus_matches_the_measured_snapshot():
    assert len(CORPUS_TITLES) == 93
    assert len(set(CORPUS_TITLES)) == 93


# One case per level, lowest to highest. Real corpus titles where one exists;
# the corpus has no VP, director, entry, bare-manager or bare-lead title, so
# those 5 get a realistic synthetic title instead.
LEVEL_CASES: tuple[tuple[str, Level], ...] = (
    ("Software Engineer Intern, Mobile (Winter 2027)", Level.INTERN),
    ("Associate Software Engineer", Level.ENTRY),
    ("Software Engineer II", Level.MID),
    ("Senior Software Engineer", Level.SENIOR),
    ("Staff Software Engineer", Level.STAFF),
    ("Senior Staff Software Engineer", Level.SENIOR_STAFF),
    ("Principal Software Engineer", Level.PRINCIPAL),
    ("Engineering Lead, Payments", Level.LEAD),
    ("Engineering Manager", Level.MANAGER),
    ("Director of Engineering", Level.DIRECTOR),
    ("VP of Engineering", Level.VP),
)


@pytest.mark.parametrize("title, expected", LEVEL_CASES)
def test_one_case_per_level(title: str, expected: Level) -> None:
    assert extract_level(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    (
        ("Staff Software Engineer, Tech Lead, Payroll Onboarding", Level.STAFF),
        (
            "Software Engineer, Privileged Access Management (PAM) / Password Manager (PWM)"
            " - India",
            Level.MANAGER,
        ),
    ),
)
def test_a_stated_ic_rung_beats_a_management_word_elsewhere(title: str, expected: Level) -> None:
    """Both are real corpus titles, and both are why the management words sit in
    their own pool. A posting that names its rung has named its level: "Tech Lead"
    after a comma is a duty, and "Password Manager" is a product. The second title
    names no rung at all, so its only level word is the one that fires."""
    assert extract_level(title) == expected


def test_every_level_has_a_case():
    assert {expected for _, expected in LEVEL_CASES} == set(Level)


TRAP_CASES: tuple[tuple[str, Level | None], ...] = (
    # The "i" in "IBM i" must never read as the entry-level roman numeral I.
    (" Principal Software Engineer - IBM i (AS/400) Connectivity", Level.PRINCIPAL),
    # Same trap, isolated: no "Principal" to short-circuit the numeral guard.
    ("Software Engineer - IBM i (AS/400) Connectivity", None),
    # The word "Senior" beats the numeral "II": this is a senior engineer on a
    # ladder that also numbers its rungs, not a mid-level one.
    ("Senior Software Engineer II", Level.SENIOR),
)


@pytest.mark.parametrize("title, expected", TRAP_CASES)
def test_measured_traps(title: str, expected: Level | None) -> None:
    assert extract_level(title) == expected


def test_empty_title_is_none():
    assert extract_level("") is None


def test_title_with_no_level_signal_is_none():
    assert extract_level("Software Engineer, DevOps") is None


def test_coverage_over_the_corpus_meets_the_measured_floor():
    labeled = sum(1 for title in CORPUS_TITLES if extract_level(title) is not None)
    assert labeled >= 70


def test_level_order_matches_the_enum_definition_order():
    assert LEVEL_ORDER == tuple(Level)


@pytest.mark.parametrize(
    "title",
    ["", " ", "\t\n", "!!!---///", "a" * 500, "Senior" * 50, "()()()", "III-II-I"],
)
def test_never_raises_on_odd_input(title: str) -> None:
    extract_level(title)


def test_runs_well_under_a_millisecond_per_call():
    start = time.perf_counter()
    for title in CORPUS_TITLES:
        extract_level(title)
    elapsed = time.perf_counter() - start
    assert elapsed / len(CORPUS_TITLES) < 0.001
