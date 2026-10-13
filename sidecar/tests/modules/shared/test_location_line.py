"""The location line on a tailored resume and its cover letter: the posting's, the
master's, or none (maintainer, 2026-10-13, audit A5 and B3)."""

from __future__ import annotations

from sidecar.modules._shared.location_line import enforce_location, location_instruction
from sidecar.modules.tailorer.prompt import build_user_prompt

MASTER = "Bengaluru, Karnataka, India"
RESUME = (
    "# Asha Rao\n"
    f"asha@example.com | +91 98450 00000 | {MASTER}\n\n"
    "## Experience\n"
    f"**Acme** | {MASTER} | 2021 to now\n"
)


def test_posting_mode_writes_the_postings_place_in_the_header_only() -> None:
    out = enforce_location(RESUME, "posting", "London, Ontario, Canada", MASTER)
    assert out.splitlines()[1] == "asha@example.com | +91 98450 00000 | London, Ontario, Canada"
    # A job's own location under a section is history, not the person's address.
    assert f"**Acme** | {MASTER} | 2021 to now" in out


def test_none_mode_removes_the_location_and_tidies_the_separators() -> None:
    out = enforce_location(RESUME, "none", "", MASTER)
    assert out.splitlines()[1] == "asha@example.com | +91 98450 00000"
    alone = enforce_location(f"# Asha\n{MASTER}\n\nBody\n", "none", "", MASTER)
    assert alone == "# Asha\n\nBody\n"


def test_master_mode_and_an_unreadable_posting_change_nothing() -> None:
    assert enforce_location(RESUME, "master", "London", MASTER) == RESUME
    assert enforce_location(RESUME, "posting", "", MASTER) == RESUME
    assert enforce_location(RESUME, "posting", "London", "") == RESUME


def test_the_model_is_told_the_rule() -> None:
    rule = location_instruction("posting", "London, Ontario, Canada", MASTER)
    prompt = build_user_prompt("# M", "JD", location_rule=rule)
    assert "=== LOCATION" in prompt and "London, Ontario, Canada" in prompt
    assert "Remove the person's location" in location_instruction("none", "", MASTER)
    assert MASTER in location_instruction("posting", "", MASTER)


def test_the_rule_names_the_document_it_governs() -> None:
    assert "cover letter header" in location_instruction(
        "posting", "London", MASTER, document="cover letter"
    )
