"""The Networker black box: discover, draft, send.

`discover(company, job)`: 0 LLM, via the voyager driver, each contact tagged by
audience and warmth. `draft(contact, job)`: the 1 LLM op, grounded in the master
profile and the audience playbook. `send(message, contact)`: 0 LLM, DM or invite
note, voyager's quota and errors surfaced verbatim. Seams: `VoyagerDriver` and
`Engine`. Audience tagging is a deterministic title heuristic, not the LLM
FR-REF-01 names, to keep discovery free (NFR-COST-01)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable

from sidecar.modules._shared.completion_retry import complete_and_parse
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
    """Resolve a company to ranked LinkedIn entities (FR-NW-02), 0 LLM. A pasted
    company `url` is authoritative; otherwise typeahead, with `prefer_domain`
    flagging the website match. The host applies the pick policy and caches it."""
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
                             error=str(raw.get("error") or ""),
                             reason=str(raw.get("reason") or ""),
                             usage=Usage(internal_calls=1))
    finally:
        drv.close()


def discover(
    company: str,
    job: str = "",  # part of the section 66 contract; reserved for JD-aware tagging (module note)
    driver: VoyagerDriver | None = None,
    limit: int = 10,
    dry_run: bool = False,
    *,
    company_urn: str | None = None,
    page: int = 1,
) -> DiscoverResult:
    """Discover up to `limit` current employees of `company`, tagged (US-REF-01/02/10).
    `company_urn` scopes the People search to current employees; `page` fetches the
    next batch; `job` is reserved for JD-aware relevance."""
    if not company:
        raise NetworkerError("discover", "company is required")
    drv = driver or DirectVoyagerDriver()
    try:
        raw = drv.discover(company, limit, company_urn=company_urn, page=page, dry_run=dry_run)
        rows = raw.get("contacts", []) or []
        contacts = [_contact_from_raw(r) for r in rows]
        return DiscoverResult(company=company, contacts=contacts,
                              error=str(raw.get("error") or ""),
                              reason=str(raw.get("reason") or ""),
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
    cancelled: Callable[[], bool] | None = None,
) -> DraftResult:
    """Draft 1 grounded referral ask (US-REF-03 / FR-REF-02), the 1 LLM op. Warmth
    comes from the connection degree, the playbook from `contact.audience`, evidence
    from `master_md` only. `skill_md` replaces the skill file (the playbook stays);
    `cancelled` raises `CompletionCancelled` (F-M7)."""
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
        (message, notes), usage = complete_and_parse(
            engine, system_prompt, user_prompt, parse_output, NetworkerError, cancelled
        )
        return DraftResult(
            message=message,
            audience=contact.audience,
            warmth=warmth,
            channel=channel,
            notes=notes,
            char_count=len(message),
            usage=Usage(**usage),
        )
    finally:
        if not keep_scratch:
            scratch.cleanup()


def send(
    message: str,
    contact: Contact,
    driver: VoyagerDriver | None = None,
    *,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> SendResult:
    """Send `message` to `contact` (US-REF-04 / FR-NW-03): warm gets a DM, cold an
    invite with a note. `on_step` gets each completed step key as the driver
    finishes it. A not-sent (cap, backoff, UI failure) returns `sent=False` with the
    reason; only a crash or unparseable output raises NetworkerError."""
    if not message.strip():
        raise NetworkerError("send", "message is empty")
    warmth = warmth_for_degree(contact.connection_degree)
    channel = channel_for_warmth(warmth)
    drv = driver or DirectVoyagerDriver()
    try:
        if channel.value == "dm":
            raw = drv.send_dm(
                contact.public_identifier, message, dry_run=dry_run, on_step=on_step
            )
        else:
            raw = drv.send_connection(
                contact.public_identifier, note=message, dry_run=dry_run,
                on_step=on_step,
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


def probe_batch(
    contacts: list[Contact],
    driver: VoyagerDriver | None = None,
    *,
    urns: dict[str, str] | None = None,
    thread_only: set[str] | None = None,
    dry_run: bool = False,
) -> list[ProbeResult]:
    """Probe many contacts in 1 browser session (US-NW-12 / FR-NW-15), read-only.
    A contact in both `urns` and `thread_only` gets a free thread-only probe; the
    rest are paced full probes that return a `target_urn` to cache. Results come
    thread-only first (join by `public_identifier`) and stop at the first refusal,
    whose reason rides on the last result. A driver crash raises NetworkerError."""
    for contact in contacts:
        if not contact.public_identifier:
            raise NetworkerError("probe", "contact.public_identifier is required")
    if not contacts:
        return []
    urns = urns or {}
    thread_pids = thread_only or set()
    drv = driver or DirectVoyagerDriver()
    try:
        raw = drv.contact_sync_states(
            [
                {
                    "public_identifier": c.public_identifier,
                    "urn": urns.get(c.public_identifier, ""),
                    "thread_only": c.public_identifier in thread_pids,
                }
                for c in contacts
            ],
            dry_run=dry_run,
        )
        results: list[ProbeResult] = []
        for entry in raw.get("results") or []:
            degree = entry.get("degree")
            results.append(ProbeResult(
                public_identifier=str(entry.get("public_identifier") or ""),
                degree=degree,
                is_first_degree=bool(entry.get("is_first_degree", degree == 1)),
                last_message_direction=entry.get("last_message_direction") or "",
                last_message_at=entry.get("last_message_at"),
                last_message_text=entry.get("last_message_text") or "",
                last_message_from=entry.get("last_message_from") or "",
                target_urn=str(entry.get("target_urn") or ""),
                ok=bool(entry.get("ok", False)),
                error=str(entry.get("error") or ""),
                reason=str(entry.get("reason") or ""),
                usage=Usage(internal_calls=1),
            ))
        return results
    finally:
        drv.close()


def quota(driver: VoyagerDriver | None = None) -> dict:
    """The live remaining cap the host displays + gates its UI on (FR-NW-01/04,
    NFR-LI-02). Zero-LLM."""
    drv = driver or DirectVoyagerDriver()
    try:
        return drv.quota()
    finally:
        drv.close()

