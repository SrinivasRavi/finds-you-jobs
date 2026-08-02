"""`--dry-run` preview assembly shared by module silos (extracted at the fourth
consumer — Scorer, Tailorer, CoverLetterer, Networker — per the M1 playbook).

Every module's CLI can print exactly what it *would* send instead of spending a
completion: the system skill and the assembled user prompt under one pair of
banners. Each silo re-typed those banners, and the Networker's had already
drifted (duplication audit D-M13) — this is the one copy.

`skill_label` names which skill the system half carries. It stays a parameter
because the Networker's prompt bundles a second markdown document (the audience
playbook) into the user half, so "draft skill" there is a real distinction, not
a stale string.
"""

from __future__ import annotations


def assemble_dry_run(skill_md: str, user_prompt: str, skill_label: str = "skill") -> str:
    """The banner-delimited preview payload, byte-for-byte what the CLI prints."""
    return (
        f"########## SYSTEM ({skill_label}) ##########\n"
        + skill_md
        + "\n########## USER ##########\n"
        + user_prompt
    )
