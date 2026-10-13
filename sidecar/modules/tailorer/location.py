"""The location line on a tailored resume: the posting's, the master's, or none
(maintainer, 2026-10-13, audit A5). The model is told the rule; the header is
then checked, because a rule a model may ignore is not a rule."""

from __future__ import annotations

import re

RESUME_LOCATION_MODES = ("posting", "master", "none")
# Contact lines live at the top, before the first section heading; a location
# further down is a job's, not the person's.
_HEADER_LINES = 8
_SECTION = re.compile(r"^\s*#{2,}\s")
_SEPARATORS = re.compile(r"\s*([|·•,]\s*){2,}")
_EDGE = re.compile(r"^[\s|·•,]+|[\s|·•,]+$")


def location_instruction(mode: str, posting: str, master: str) -> str:
    """1 sentence for the prompt saying what the header's location must be."""
    if mode == "none":
        return "Remove the person's location (city, region, country, address) from the resume."
    if mode == "posting" and posting:
        return f"Write the person's location in the resume header as exactly: {posting}"
    if master:
        return f"Keep the person's location in the resume header as: {master}"
    return ""


def enforce_location(resume_md: str, mode: str, posting: str, master: str) -> str:
    """Put the chosen location where the master's sits in the header lines. A
    posting with no readable place keeps the master's; so does `master` mode."""
    if not master or mode == "master" or (mode == "posting" and not posting):
        return resume_md
    lines = resume_md.splitlines()
    out: list[str] = []
    in_header = True
    for i, line in enumerate(lines):
        in_header = in_header and i < _HEADER_LINES and not _SECTION.match(line)
        if in_header and master in line:
            if mode == "posting":
                line = line.replace(master, posting)
            else:
                line = _EDGE.sub("", _SEPARATORS.sub(" | ", line.replace(master, "")))
                if not line.strip("#*_ "):
                    continue
        out.append(line)
    return "\n".join(out) + ("\n" if resume_md.endswith("\n") else "")
