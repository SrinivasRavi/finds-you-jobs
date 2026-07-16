"""Assemble the draft() prompt: draft skill (system) + master/job/contact/
playbook/warmth/guidance blocks (user).

The draft skill is the system prompt; the per-run inputs arrive as clearly
delimited blocks. Everything is in-context — the operation has no tools and no
file access (ROADMAP §4).
"""

from __future__ import annotations

from pathlib import Path

from sidecar.modules._shared.skill_md import load_skill_md

from .types import CONNECTION_NOTE_CHAR_LIMIT, DM_CHAR_LIMIT, Channel, Contact, Warmth

SKILL_PATH = Path(__file__).parent / "draft-referral-skill.md"


def load_skill() -> str:
    return load_skill_md(SKILL_PATH)


def _contact_block(contact: Contact) -> str:
    degree = contact.connection_degree if contact.connection_degree is not None else "(unknown)"
    lines = [
        f"name: {contact.full_name or '(unknown — open without a name)'}",
        f"title: {contact.current_title or '(unknown)'}",
        f"company: {contact.current_company or '(unknown)'}",
        f"headline: {contact.headline or '(none)'}",
        f"audience: {contact.audience.value}",
        f"connection_degree: {degree}",
    ]
    return "\n".join(lines)


def build_user_prompt(
    master_md: str,
    job_md: str,
    contact: Contact,
    warmth: Warmth,
    channel: Channel,
    playbook_md: str,
    guidance: str = "",
) -> str:
    char_limit = CONNECTION_NOTE_CHAR_LIMIT if channel is Channel.CONNECTION_NOTE else DM_CHAR_LIMIT
    channel_line = (
        f"WARMTH: {warmth.value} → deliver as a "
        + ("connection-request NOTE" if channel is Channel.CONNECTION_NOTE else "direct MESSAGE")
        + f" (target ≤ {char_limit} characters)."
    )
    parts = [
        "=== MASTER PROFILE (sole evidence about the seeker) ===",
        master_md.strip() or "(empty — write only a minimal honest introduction)",
        "",
        "=== JOB (the role the referral is for) ===",
        job_md.strip(),
        "",
        "=== CONTACT (the recipient — personalize only from these public facts) ===",
        _contact_block(contact),
        "",
        "=== AUDIENCE PLAYBOOK (the angle + tone for this contact) ===",
        playbook_md.strip(),
        "",
        channel_line,
    ]
    if guidance.strip():
        parts += [
            "",
            "=== PER-CONTACT GUIDANCE (apply where it does not conflict with grounding) ===",
            guidance.strip(),
        ]
    parts += [
        "",
        "Write the message now, following the skill and the output contract exactly",
        "(===MESSAGE=== then ===NOTES===).",
    ]
    return "\n".join(parts)
