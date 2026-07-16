"""Audience tagging + playbook binding — pure, no I/O beyond reading the bundled
playbook skill files. Fully unit-testable.

The audience taxonomy is the four canonical P1 audiences + OTHER (US-NW-09,
FR-REF-01); custom audiences are P2 (US-PLB-06). Warmth is derived from
connection degree (US-REF-10). Tagging here is a **deterministic role-title
heuristic** — see the module docstring note in `networker.py` on why P1 uses a
free/deterministic tagger rather than an LLM call per contact.
"""

from __future__ import annotations

import re
from pathlib import Path

from .types import Audience, Channel, Contact, Warmth

_PLAYBOOKS_DIR = Path(__file__).parent / "playbooks"


def _word(haystack: str, *words: str) -> bool:
    """True if any `word` appears in `haystack` on token boundaries (the
    scraper/applier #1101/#1169 lesson: 'lead' must not fire inside 'leaderboard',
    'em' must not fire inside 'system')."""
    for w in words:
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", haystack):
            return True
    return False


# Ordered most-specific-first: recruiter, then leadership, then hiring manager,
# then peer (IC). First match wins; unmatched → OTHER (US-REF-02).
def tag_audience(title: str, headline: str = "") -> Audience:
    """Classify a contact into an audience from their role title (+ headline).

    Deterministic and free — no LLM call. Returns OTHER when nothing matches
    confidently (US-REF-02's low-confidence 'Other' tag)."""
    hay = f"{title} {headline}".lower().strip()
    if not hay:
        return Audience.OTHER

    if _word(hay, "recruiter", "sourcer", "talent", "recruiting", "talent acquisition"):
        return Audience.RECRUITER
    if _word(hay, "ceo", "cto", "coo", "cfo", "ciso", "cmo", "chief", "founder",
             "co-founder", "president", "vp", "svp", "evp", "vice president",
             "head", "director", "partner"):
        return Audience.LEADERSHIP
    if _word(hay, "manager", "lead", "em", "supervisor") or "hiring manager" in hay:
        return Audience.HM
    if _word(hay, "engineer", "developer", "swe", "sde", "programmer", "scientist",
             "designer", "architect", "analyst", "consultant", "specialist"):
        return Audience.PEER
    return Audience.OTHER


def warmth_for_degree(connection_degree: int | None) -> Warmth:
    """1st-degree → WARM (already connected, DM referral-ask); else COLD
    (connection-request-with-note). US-REF-10."""
    return Warmth.WARM if connection_degree == 1 else Warmth.COLD


def channel_for_warmth(warmth: Warmth) -> Channel:
    """WARM → DM; COLD → connection-request-with-note (FR-NW-03)."""
    return Channel.DM if warmth is Warmth.WARM else Channel.CONNECTION_NOTE


def classify(contact: Contact) -> Contact:
    """Assign audience + warmth to a discovered contact in place, returning it."""
    contact.audience = tag_audience(contact.current_title, contact.headline)
    contact.warmth = warmth_for_degree(contact.connection_degree)
    contact.is_first_degree = contact.connection_degree == 1
    return contact


def playbook_path(audience: Audience) -> Path:
    return _PLAYBOOKS_DIR / f"{audience.value}.md"


def load_playbook(audience: Audience) -> str:
    """Load the audience playbook skill file (the per-audience outreach angle).
    Falls back to the OTHER playbook if a specific one is missing."""
    path = playbook_path(audience)
    if not path.exists():
        path = playbook_path(Audience.OTHER)
    # HTML comments in skill files are authoring-side only — strip them, matching
    # the shared skill_md loader used by scorer/tailorer.
    from sidecar.modules._shared.skill_md import load_skill_md

    return load_skill_md(path)
