"""Portals config — the career-ops `portals.yml` model, as stdlib TOML.

The user lists company careers URLs and board feeds; the right adapter claims
each entry from its URL shape (auto-detection). TOML over YAML is a deliberate
lean-by-design call: `tomllib` is stdlib, the repo carries zero runtime deps,
and the config is flat enough that the format doesn't matter. JSON is accepted
too for programmatic callers.

Shape (see `sidecar/modules/scraper/portals.example.toml`):

    [[sources]]
    url = "https://boards.greenhouse.io/gleanwork"
    company = "Glean"            # optional display-name override

    [[sources]]
    board = "remoteok"           # boards: remoteok | remotive | hackernews |
                                 #         arbeitnow | themuse

    [[sources]]
    url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    type = "rss"                 # optional explicit adapter id

    [filters.title]
    allow = ["software engineer"]
    block = ["staff"]
    [filters.location]
    allow = ["india", "remote"]
    block = []
    always_allow = []
    [filters.company]
    block = ["Example Corp"]     # cross-cutting exclude, not a curated-list removal —
                                 # see ScanPrefs.company_block
    [filters.content]
    allow = []                  # description keywords; block wins over allow
    block = ["unpaid internship"]
    [[filters.content.by_title_keyword]]
    title = ["manager"]          # rule applies only when title matches
    allow = []
    block = ["on-site"]
    [filters.visa]
    enabled = false              # drop explicit sponsorship denials
    phrases = []                 # empty = filters.DEFAULT_VISA_PHRASES
    [filters.salary]
    min = 0                      # annual, 0 = off; unstated salary passes
    max = 0
    currency = "USD"             # postings in a different currency pass
    [scan]
    max_age_days = 0             # 0 = off
    per_source_cap = 0           # 0 = uncapped (never self-throttle by default)
    search_terms = []            # extra user-authored terms for search sources
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .types import ContentRule, ScanPrefs, ScraperError


@dataclass
class SourceEntry:
    """One `[[sources]]` row. `url` or `board` required; `type` forces an
    adapter. `actor` names the Apify actor for `board = "apify"` rows."""

    url: str = ""
    board: str = ""
    type: str = ""
    company: str = ""
    actor: str = ""


@dataclass
class PortalsConfig:
    sources: list[SourceEntry] = field(default_factory=list)
    prefs: ScanPrefs = field(default_factory=ScanPrefs)


def _content_rules(raw: object, where: str) -> list[ContentRule]:
    if not isinstance(raw, list):
        raise ScraperError("portals-config", f"{where} must be a list of tables, got {raw!r}")
    rules: list[ContentRule] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ScraperError("portals-config", f"{where}[{i}] is not a table: {entry!r}")
        rule = ContentRule(
            title=_str_list(entry.get("title", []), f"{where}[{i}].title"),
            allow=_str_list(entry.get("allow", []), f"{where}[{i}].allow"),
            block=_str_list(entry.get("block", []), f"{where}[{i}].block"),
        )
        if not rule.title:
            raise ScraperError(
                "portals-config",
                f"{where}[{i}] needs a non-empty `title` list (the rule's scope)",
            )
        rules.append(rule)
    return rules


def _str_list(raw: object, where: str) -> list[str]:
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ScraperError("portals-config", f"{where} must be a list of strings, got {raw!r}")
    return raw


def parse_portals(data: dict, where: str = "portals config") -> PortalsConfig:
    raw_sources = data.get("sources", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise ScraperError("portals-config", f"{where} has no [[sources]] entries")

    sources: list[SourceEntry] = []
    for i, raw in enumerate(raw_sources):
        if not isinstance(raw, dict):
            raise ScraperError("portals-config", f"sources[{i}] is not a table: {raw!r}")
        entry = SourceEntry(
            url=str(raw.get("url", "")),
            board=str(raw.get("board", "")),
            type=str(raw.get("type", "")),
            company=str(raw.get("company", "")),
            actor=str(raw.get("actor", "")),
        )
        if not entry.url and not entry.board:
            raise ScraperError("portals-config", f"sources[{i}] needs `url` or `board`")
        sources.append(entry)

    filters = data.get("filters", {})
    title = filters.get("title", {}) if isinstance(filters, dict) else {}
    location = filters.get("location", {}) if isinstance(filters, dict) else {}
    company = filters.get("company", {}) if isinstance(filters, dict) else {}
    content = filters.get("content", {}) if isinstance(filters, dict) else {}
    visa = filters.get("visa", {}) if isinstance(filters, dict) else {}
    salary = filters.get("salary", {}) if isinstance(filters, dict) else {}
    scan_opts = data.get("scan", {})
    if not isinstance(scan_opts, dict):
        raise ScraperError("portals-config", "[scan] must be a table")

    prefs = ScanPrefs(
        title_allow=_str_list(title.get("allow", []), "filters.title.allow"),
        title_block=_str_list(title.get("block", []), "filters.title.block"),
        location_allow=_str_list(location.get("allow", []), "filters.location.allow"),
        location_block=_str_list(location.get("block", []), "filters.location.block"),
        location_always_allow=_str_list(
            location.get("always_allow", []), "filters.location.always_allow"
        ),
        company_block=_str_list(company.get("block", []), "filters.company.block"),
        content_allow=_str_list(content.get("allow", []), "filters.content.allow"),
        content_block=_str_list(content.get("block", []), "filters.content.block"),
        content_by_title=_content_rules(
            content.get("by_title_keyword", []), "filters.content.by_title_keyword"
        ),
        visa_filter=bool(visa.get("enabled", False)),
        visa_phrases=_str_list(visa.get("phrases", []), "filters.visa.phrases"),
        salary_min=int(salary.get("min", 0)),
        salary_max=int(salary.get("max", 0)),
        salary_currency=str(salary.get("currency", "")),
        search_terms=_str_list(scan_opts.get("search_terms", []), "scan.search_terms"),
        max_age_days=int(scan_opts.get("max_age_days", 0)),
        per_source_cap=int(scan_opts.get("per_source_cap", 0)),
        timeout_s=int(scan_opts.get("timeout_s", 20)),
        disabled_sources=_str_list(data.get("disabled_sources", []), "disabled_sources"),
    )
    return PortalsConfig(sources=sources, prefs=prefs)


def load_portals(path: str | Path) -> PortalsConfig:
    p = Path(path)
    if not p.exists():
        raise ScraperError("portals-config", f"no such file: {p}")
    try:
        if p.suffix.lower() == ".json":
            data = json.loads(p.read_text(encoding="utf-8"))
        else:
            data = tomllib.loads(p.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as e:
        raise ScraperError("portals-config", f"could not parse {p}: {e}") from e
    if not isinstance(data, dict):
        raise ScraperError("portals-config", f"{p} did not parse to a table")
    return parse_portals(data, where=str(p))
