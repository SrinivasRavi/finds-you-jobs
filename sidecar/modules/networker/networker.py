"""The Networker black box: discover / draft / send (ROADMAP §66).

- `discover(company, job) → contacts[]` — zero-LLM; delegates to the voyager
  subprocess (driver), then tags each contact by audience + warmth.
- `draft(contact, job, guidance?) → message` — the one LLM operation; grounded
  in the seeker's master profile, per the bound audience playbook.
- `send(message, contact) → result` — zero-LLM; routes warm→DM / cold→connection
  -request-with-note through the voyager subprocess. Caps/backoff are voyager's;
  we surface its verbatim quota + errors, never swallowing them.

**Two seams, mirroring the other silos:** the voyager subprocess sits behind the
`VoyagerDriver` protocol (production = `DirectVoyagerDriver`; test = a fake),
and the LLM sits behind the `Engine` protocol. Teardown of the driver happens in
`finally` (NFR-MEM-02 / §4). Storage stance (§4): the module owns no persistent
storage; `draft` uses a per-operation scratch dir deleted on return.

**Design note — deterministic audience tagging (flagged).** FR-REF-01 says "the
LLM assigns each contact an audience tag". P1 here uses a **deterministic
role-title heuristic** (`playbooks.tag_audience`) instead, so discovery stays
zero-LLM and free (tagging 10 contacts is not 10 LLM calls — that would strain
the per-operation cost bound, NFR-COST-01) and is fully unit-testable. The
per-contact JD-aware LLM tagging remains a possible enhancement; `job` is
accepted by `discover`/`draft` and `draft` already feeds the JD to the model.
Open question for the maintainer — see the ROADMAP N2 as-built note.
"""

from __future__ import annotations

import tempfile

from sidecar.modules._shared.job_input import JobInputError
from sidecar.modules._shared.job_input import resolve_job as _resolve_job

from .driver import DirectVoyagerDriver, VoyagerDriver
from .engine import ClaudeCliEngine, Engine
from .output_parse import parse_output
from .playbooks import channel_for_warmth, classify, load_playbook, warmth_for_degree
from .prompt import build_user_prompt, load_skill
from .types import (
    CompanyCandidate,
    Contact,
    DiscoverResult,
    DraftResult,
    NetworkerError,
    ProbeResult,
    ResolveResult,
    SendResult,
    Usage,
)


def _resolve_jd(job: str) -> str:
    try:
        return _resolve_job(job)
    except JobInputError as e:
        raise NetworkerError(e.stage, e.message) from e


def _contact_from_raw(raw: dict) -> Contact:
    """Map one voyager discovery row onto a Contact, then classify it."""
    contact = Contact(
        public_identifier=raw.get("public_identifier", ""),
        full_name=raw.get("full_name") or "",
        headline=raw.get("headline") or "",
        current_title=raw.get("current_title") or "",
        current_company=raw.get("current_company") or "",
        url=raw.get("url") or "",
        connection_degree=raw.get("connection_degree"),
    )
    return classify(contact)


def resolve(
    company: str,
    driver: VoyagerDriver | None = None,
    *,
    url: str | None = None,
    prefer_domain: str | None = None,
    limit: int = 5,
    dry_run: bool = False,
) -> ResolveResult:
    """Resolve a company → ranked LinkedIn company entities (FR-NW-02).

    Zero-LLM; delegates to voyager. `url` (a pasted LinkedIn company URL) is the
    authoritative single-entity path. Otherwise typeahead on `company`;
    `prefer_domain` (the employer domain from the job URL) flags the
    website-matched candidate for the host's silent auto-pick. The host applies the
    pick policy (domain-match → auto; else → user confirm/paste) and caches it."""
    if not company and not url:
        raise NetworkerError("resolve", "company or url is required")
    drv = driver or DirectVoyagerDriver()
    try:
        raw = drv.resolve_company(
            company, url=url, prefer_domain=prefer_domain, limit=limit, dry_run=dry_run
        )
        rows = raw.get("companies", []) or []
        candidates = [
            CompanyCandidate(
                urn=r.get("urn") or "",
                company_id=r.get("company_id") or "",
                name=r.get("name") or "",
                vanity=r.get("vanity") or "",
                industry=r.get("industry") or "",
                logo_url=r.get("logo_url") or "",
                website=r.get("website") or "",
                domain_match=bool(r.get("domain_match")),
            )
            for r in rows
            if r.get("urn")
        ]
        return ResolveResult(company=company, candidates=candidates,
                             usage=Usage(internal_calls=1))
    finally:
        drv.close()


def discover(
    company: str,
    job: str = "",  # part of the §66 contract; reserved for JD-aware tagging (module note)
    driver: VoyagerDriver | None = None,
    limit: int = 10,
    dry_run: bool = False,
    *,
    company_urn: str | None = None,
    page: int = 1,
) -> DiscoverResult:
    """Discover ≤ `limit` current employees of `company`, tagged by audience +
    warmth (US-REF-01/02/10). Zero-LLM. `company_urn` (resolved + disambiguated
    by the host) scopes the People search by the `currentCompany` facet — the
    current-employees-only correctness fix. `page` fetches the next batch for
    "find 10 more" (voyager paginates the results page). `job` is accepted per the
    §66 contract and reserved for future JD-aware relevance (module note)."""
    if not company:
        raise NetworkerError("discover", "company is required")
    drv = driver or DirectVoyagerDriver()
    try:
        raw = drv.discover(company, limit, company_urn=company_urn, page=page, dry_run=dry_run)
        rows = raw.get("contacts", []) or []
        contacts = [_contact_from_raw(r) for r in rows]
        return DiscoverResult(company=company, contacts=contacts,
                              usage=Usage(internal_calls=1))
    finally:
        drv.close()


def draft(
    contact: Contact,
    job: str,
    guidance: str = "",
    *,
    master_md: str = "",
    engine: Engine | None = None,
    keep_scratch: bool = False,
    skill_md: str | None = None,
) -> DraftResult:
    """Draft one grounded referral-ask for `contact` (US-REF-03 / FR-REF-02).

    The one LLM operation. Warmth/channel derive from the contact's connection
    degree (US-REF-10); the audience playbook is bound from `contact.audience`.
    `master_md` is the seeker's sole evidence (no fabrication — the skill enforces
    it, surfacing any refusal in `notes`).

    `skill_md`, when provided, replaces the on-disk draft skill file as the
    system prompt (the app's user-editable-prompt override, §5). None → the
    default. The bound audience playbook is unaffected — it stays appended to the
    user prompt."""
    if not contact.public_identifier:
        raise NetworkerError("draft", "contact.public_identifier is required")
    engine = engine or ClaudeCliEngine()
    jd_md = _resolve_jd(job)
    warmth = warmth_for_degree(contact.connection_degree)
    channel = channel_for_warmth(warmth)
    playbook_md = load_playbook(contact.audience)
    system_prompt = skill_md if skill_md is not None else load_skill()
    user_prompt = build_user_prompt(
        master_md, jd_md, contact, warmth, channel, playbook_md, guidance
    )

    scratch = tempfile.TemporaryDirectory(prefix="fyj-draft-")
    try:
        raw, usage = engine.complete(system_prompt, user_prompt)
        message, notes = parse_output(raw)
        return DraftResult(
            message=message,
            audience=contact.audience,
            warmth=warmth,
            channel=channel,
            notes=notes,
            char_count=len(message),
            usage=usage,
        )
    finally:
        if not keep_scratch:
            scratch.cleanup()


def send(
    message: str,
    contact: Contact,
    driver: VoyagerDriver | None = None,
    *,
    tier: str | None = None,
    dry_run: bool = False,
) -> SendResult:
    """Send `message` to `contact` via the voyager subprocess (US-REF-04 /
    FR-NW-03). Warm → DM; cold → connection-request-with-note. Zero-LLM.

    A voyager-reported not-sent (cap hit / backoff / UI failure) returns a
    SendResult with `sent=False` + the verbatim reason; only a subprocess crash
    or unparseable output raises NetworkerError (both via the driver)."""
    if not message.strip():
        raise NetworkerError("send", "message is empty")
    warmth = warmth_for_degree(contact.connection_degree)
    channel = channel_for_warmth(warmth)
    drv = driver or DirectVoyagerDriver()
    try:
        if channel.value == "dm":
            raw = drv.send_dm(contact.public_identifier, message, tier, dry_run=dry_run)
        else:
            raw = drv.send_connection(
                contact.public_identifier, note=message, tier=tier, dry_run=dry_run
            )
        return _send_result_from_raw(raw, contact, channel, dry_run)
    finally:
        drv.close()


def _send_result_from_raw(raw: dict, contact: Contact, channel, dry_run: bool) -> SendResult:
    if dry_run:
        return SendResult(
            public_identifier=contact.public_identifier,
            channel=channel,
            sent=False,
            status="planned",
            reason=raw.get("blocked_reason", ""),
            quota=raw.get("quota", {}),
            usage=Usage(internal_calls=1),
        )
    return SendResult(
        public_identifier=contact.public_identifier,
        channel=channel,
        sent=bool(raw.get("sent", False)),
        status=str(raw.get("status", "")),
        error=str(raw.get("error", "")),
        reason=str(raw.get("reason", "")),
        paused_until=raw.get("paused_until"),
        quota=raw.get("quota", {}),
        usage=Usage(internal_calls=1),
    )


def probe(
    contact: Contact,
    driver: VoyagerDriver | None = None,
    *,
    dry_run: bool = False,
) -> ProbeResult:
    """Read a contact's live LinkedIn state for the status-sync engine (US-NW-12
    / FR-NW-15). Zero-LLM, READ-ONLY — delegates to the voyager `contact-sync`
    subprocess (degree + last-message direction/timestamp). A read miss returns a
    ProbeResult with empty/None message fields (the host makes no transition).

    A hard failure (subprocess crash / unparseable JSON) raises NetworkerError via
    the driver, exactly like discover/send — never a silent half-result."""
    if not contact.public_identifier:
        raise NetworkerError("probe", "contact.public_identifier is required")
    drv = driver or DirectVoyagerDriver()
    try:
        raw = drv.contact_sync(contact.public_identifier, dry_run=dry_run)
        degree = raw.get("degree")
        return ProbeResult(
            public_identifier=contact.public_identifier,
            degree=degree,
            is_first_degree=bool(raw.get("is_first_degree", degree == 1)),
            last_message_direction=raw.get("last_message_direction") or "",
            last_message_at=raw.get("last_message_at"),
            usage=Usage(internal_calls=1),
        )
    finally:
        drv.close()


def quota(driver: VoyagerDriver | None = None, *, tier: str | None = None) -> dict:
    """The live remaining cap the host displays + gates its UI on (FR-NW-01/04,
    NFR-LI-02). Zero-LLM."""
    drv = driver or DirectVoyagerDriver()
    try:
        return drv.quota(tier)
    finally:
        drv.close()


def dry_run_prompt(contact: Contact, job: str, master_md: str = "", guidance: str = "") -> str:
    """Assemble the full draft prompt without any LLM call (CLI --dry-run)."""
    jd_md = _resolve_jd(job)
    warmth = warmth_for_degree(contact.connection_degree)
    channel = channel_for_warmth(warmth)
    playbook_md = load_playbook(contact.audience)
    return (
        "########## SYSTEM (draft skill) ##########\n"
        + load_skill()
        + "\n########## USER ##########\n"
        + build_user_prompt(master_md, jd_md, contact, warmth, channel, playbook_md, guidance)
    )
