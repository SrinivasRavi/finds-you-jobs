"""Networker module types — plain dataclasses, pre-architecture (ROADMAP §4).

Framework-free on purpose (no pydantic/ORM): the module is a silo; the G4
architecture pass owns the final type system and these graduate into it. The
`Contact` in particular maps onto the app's contact rows at N3 — it must not
couple to any storage/DTO layer here.

MIT (own code). This module never imports `voyager_py`; it drives it through a
subprocess driver (see `driver.py`, NFR-LIC-01).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Audience(StrEnum):
    """The four canonical P1 audiences (US-NW-09 / FR-REF-01) plus OTHER for
    low-confidence classifications (US-REF-02). Custom/user-defined audiences
    are P2 (US-PLB-06)."""

    PEER = "peer"
    HM = "hm"              # hiring manager
    RECRUITER = "recruiter"
    LEADERSHIP = "leadership"
    OTHER = "other"


class Warmth(StrEnum):
    """Outreach warmth (US-REF-10). Derived from connection degree: 1st-degree
    → WARM (a DM referral-ask, never a new connect); everyone else → COLD (a
    connection-request-with-note)."""

    COLD = "cold"
    WARM = "warm"


class Channel(StrEnum):
    """How a message is delivered. COLD → connection-request-with-note;
    WARM → direct message (FR-NW-03)."""

    CONNECTION_NOTE = "connection_note"
    DM = "dm"


# LinkedIn's connection-note character limit (the cold-path budget). The warm
# DM has no hard client limit but we keep drafts tight; both are advisory bars
# the draft skill is told to respect (US-PLB-05 per-step char-limit).
CONNECTION_NOTE_CHAR_LIMIT = 300
DM_CHAR_LIMIT = 1200


@dataclass
class Usage:
    """Aggregate cost record for one bounded operation (ROADMAP §4).

    Recorded always; NOT enforced as a budget pre-beta (ROADMAP §4). `discover`
    and `send` are zero-LLM (they delegate to the voyager subprocess: `usd`/
    tokens stay None, `internal_calls` counts subprocess invocations). `draft`
    is the one LLM operation and carries real token/usd figures.
    """

    internal_calls: int = 0
    tokens_in: int | None = None
    tokens_out: int | None = None
    usd: float | None = None
    latency_ms: int | None = None
    model: str | None = None


@dataclass
class Contact:
    """One potential referrer discovered at a company. Explicit-empty-allowed
    (module convention — no `?` glyphs). `audience`/`warmth` are auto-assigned
    at discovery (US-REF-02 / US-REF-10)."""

    public_identifier: str
    full_name: str = ""
    headline: str = ""
    current_title: str = ""
    current_company: str = ""
    url: str = ""
    connection_degree: int | None = None
    is_first_degree: bool = False
    audience: Audience = Audience.OTHER
    warmth: Warmth = Warmth.COLD


@dataclass
class CompanyCandidate:
    """One LinkedIn company entity a name resolved to (FR-NW-02 company scoping).

    The `urn` is what discovery scopes the `currentCompany` People facet by
    (current-employees-only). `domain_match` is True when this candidate's public
    website matched the employer domain parsed from the job URL — the host's
    silent-auto-pick signal (else it confirms with the user)."""

    urn: str
    company_id: str = ""
    name: str = ""
    vanity: str = ""
    industry: str = ""
    logo_url: str = ""
    website: str = ""
    domain_match: bool = False


@dataclass
class ResolveResult:
    """Output of one resolve() operation — ranked company entities for a name.
    Zero-LLM (one voyager typeahead call, plus optional per-candidate website
    lookups when a domain anchor is supplied)."""

    company: str
    candidates: list[CompanyCandidate] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


@dataclass
class DiscoverResult:
    """Output of one discover() operation (US-REF-01). Zero-LLM."""

    company: str
    contacts: list[Contact] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


@dataclass
class DraftResult:
    """Output of one draft() operation (US-REF-03 / FR-REF-02). LLM-powered.

    `notes` carries the model's judgment calls for the reviewer — grounding
    confirmations, any requested-but-unsupported claim it refused to make
    (the fabrication-guard surface, consistent with the other modules)."""

    message: str
    audience: Audience
    warmth: Warmth
    channel: Channel
    notes: list[str] = field(default_factory=list)
    char_count: int = 0
    usage: Usage = field(default_factory=Usage)


@dataclass
class SendResult:
    """Output of one send() operation (US-REF-04 / FR-NW-03). Zero-LLM.

    `quota` is the voyager-reported live remaining cap (the host displays it and
    gates its UI on it — NFR-LI-02). `error`/`reason` carry the verbatim voyager
    failure when `sent` is False (never swallowed)."""

    public_identifier: str
    channel: Channel
    sent: bool = False
    status: str = ""
    error: str = ""
    reason: str = ""
    paused_until: float | None = None
    quota: dict = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)


@dataclass
class ProbeResult:
    """Output of one read-only contact-status probe (US-NW-12 / FR-NW-15). Zero-LLM.

    The live LinkedIn state the sync engine maps onto the kanban lifecycle:
    connection degree (1 ⇒ connected/accepted) plus the 1:1 thread's last-message
    direction (`them` = they replied last; `me` = our message is last; `""` = no
    readable history) and its timestamp (epoch seconds). Explicit-empty-allowed —
    a read miss leaves the message fields empty/None (no transition this tick)."""

    public_identifier: str
    degree: int | None = None
    is_first_degree: bool = False
    last_message_direction: str = ""  # them | me | "" (none)
    last_message_at: float | None = None
    usage: Usage = field(default_factory=Usage)


class NetworkerError(Exception):
    """Typed failure. The message carries the verbatim underlying cause —
    never swallowed, never half-succeeded (vision non-negotiable). Raised for
    hard failures (voyager subprocess crash, unparseable JSON, LLM engine
    error); a send that voyager *reports* as not-sent returns SendResult with
    `sent=False` + the verbatim reason, it does not raise."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")
