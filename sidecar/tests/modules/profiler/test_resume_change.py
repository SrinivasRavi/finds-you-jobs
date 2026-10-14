"""A major resume edit is more than 300 characters added, and nothing else counts
(maintainer, 2026-10-13; score-storage.md section 5.2)."""

from __future__ import annotations

from sidecar.modules.profiler.resume_change import (
    MAJOR_EDIT_CHARS,
    added_chars,
    is_major_edit,
)

BASE = """# Asha Rao
## Experience
**Acme Corp** | Senior Engineer | 2021 to now
- Built the billing service in Python.
- Ran the on-call rotation for 6 services and cut pages by half.
## Projects
- **Ledger**: a double-entry ledger in Rust.
## Education
**IIT Madras** | B.Tech 2016
"""

# As long as the longest bullet on the maintainer's resume: 298 characters.
LONG_BULLET = (
    "- Designed and shipped the multi-region payments ledger that settles 4 million "
    "transactions a day across 3 currencies, moving reconciliation from a nightly batch "
    "to streaming, which cut settlement disputes by 62.5% and let finance close the books "
    "2 days earlier every month without extra headcount."
)

# A new job block of the shortest size measured (about 430 characters).
NEW_JOB = """**Globex** | Staff Engineer | 2018 to 2021
- Led the data platform team of 6 through a move from Hadoop to Spark on Kubernetes.
- Built the event pipeline that feeds every product dashboard, 2 billion events a day.
- Cut the warehouse bill by 40% by tiering cold partitions to object storage.
- Mentored 4 engineers into senior roles and ran the team's hiring loop.
"""


def test_the_long_bullet_and_new_job_bracket_the_bar() -> None:
    assert len(LONG_BULLET) == 298 < MAJOR_EDIT_CHARS
    assert len(NEW_JOB) > MAJOR_EDIT_CHARS


def test_a_new_job_is_major() -> None:
    after = BASE.replace("## Projects", NEW_JOB + "## Projects")
    assert is_major_edit(BASE, after)


def test_a_big_addition_inside_an_existing_entry_is_major() -> None:
    # The structural rule this replaced missed this: no new heading, no new title.
    after = BASE.replace(
        "- Built the billing service in Python.",
        "- Built the billing service in Python.\n" + LONG_BULLET + "\n" + LONG_BULLET.upper(),
    )
    assert is_major_edit(BASE, after)


def test_1_added_bullet_is_minor_even_at_the_longest() -> None:
    after = BASE.replace("## Projects", LONG_BULLET + "\n## Projects")
    assert not is_major_edit(BASE, after)


def test_a_short_new_section_is_minor() -> None:
    assert not is_major_edit(BASE, BASE + "## Languages\n- English, Tamil, Hindi.\n")


def test_rewording_a_bullet_is_minor() -> None:
    after = BASE.replace(
        "Built the billing service in Python.",
        "Led the billing rewrite in Python, cutting cost 30%.",
    )
    assert not is_major_edit(BASE, after)


def test_a_typo_fix_counts_its_word() -> None:
    after = BASE.replace("rotation", "rotaton")
    assert added_chars(BASE, after) == len("rotaton")


def test_removing_or_reordering_adds_nothing() -> None:
    lines = BASE.splitlines()
    assert added_chars(BASE, "\n".join(lines[:5])) == 0
    assert added_chars(BASE, "\n".join(reversed(lines))) == 0
    assert added_chars(BASE, BASE.replace("\n", "\n\n")) == 0


def test_an_unchanged_resume_adds_nothing() -> None:
    assert added_chars(BASE, BASE) == 0
