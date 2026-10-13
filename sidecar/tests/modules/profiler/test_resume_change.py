"""A major resume edit marks older scores rescore-due; a minor one marks nothing
(maintainer, 2026-10-13; score-storage.md section 5.2)."""

from __future__ import annotations

from sidecar.modules.profiler.resume_change import is_major_edit

BASE = """# Asha Rao
## Experience
**Acme Corp** | Senior Engineer | 2021 to now
- Built the billing service in Python.
## Projects
- **Ledger**: a double-entry ledger in Rust.
## Education
**IIT Madras** | B.Tech 2016
"""


def test_a_new_section_is_major() -> None:
    assert is_major_edit(BASE, BASE + "## Publications\n- A paper.\n")


def test_a_new_project_is_major() -> None:
    after = BASE.replace(
        "## Education", "- **Scheduler**: a cron service in Go.\n## Education"
    )
    assert is_major_edit(BASE, after)


def test_a_new_job_is_major() -> None:
    after = BASE.replace(
        "## Projects", "**Globex** | Engineer | 2018 to 2021\n- Ran the data team.\n## Projects"
    )
    assert is_major_edit(BASE, after)


def test_rewording_a_bullet_is_minor() -> None:
    after = BASE.replace(
        "Built the billing service in Python.",
        "Led the billing rewrite in Python, cutting cost 30%.",
    )
    assert not is_major_edit(BASE, after)


def test_formatting_case_and_punctuation_are_minor() -> None:
    after = BASE.replace("## Experience", "##   EXPERIENCE")
    after = after.replace("**Acme Corp**", "**Acme corp.**")
    assert not is_major_edit(BASE, after)


def test_removing_or_reordering_is_minor() -> None:
    lines = BASE.splitlines()
    assert not is_major_edit(BASE, "\n".join(lines[:5]))
    assert not is_major_edit(BASE, "\n".join(reversed(lines)))


def test_a_plain_text_resume_heading_counts() -> None:
    before = "ASHA RAO\nEXPERIENCE\nAcme Corp, engineer.\n"
    assert is_major_edit(before, before + "CERTIFICATIONS\nAWS Solutions Architect.\n")
    assert not is_major_edit(before, before.replace("engineer", "senior engineer"))
