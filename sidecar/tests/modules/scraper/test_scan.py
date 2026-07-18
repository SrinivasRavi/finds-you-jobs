"""scan() pipeline + CLI tests — fake fetcher end-to-end, no network.

Covers:
  US-JB-01 — scored daily feed input (clean normalized rows)
  US-SYS-01 / FR-SYS-01 — canonical-URL dedup across sources
  Track M3 spec — per-source usage/error diagnostics; errors verbatim
"""

from __future__ import annotations

import json

from sidecar.modules.scraper.config import PortalsConfig, SourceEntry
from sidecar.modules.scraper.scraper import scan
from sidecar.modules.scraper.types import ScanPrefs, ScraperError

from .fakes import routed

GH_PAYLOAD = {
    "jobs": [
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1?gh_src=tw",
            "title": "Backend Engineer",
            "location": {"name": "Pune, India"},
            "first_published": "2026-07-01T00:00:00-04:00",
        },
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
            "title": "Backend Engineer",  # same posting, tracking-param variant → dedup
            "location": {"name": "Pune, India"},
        },
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
            "title": "Account Executive",  # filtered by title
            "location": {"name": "Pune, India"},
        },
        {
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/3",
            "title": "Backend Engineer",
            "location": {"name": "Austin, TX"},  # filtered by location
        },
        {"absolute_url": "", "title": "Ghost row"},  # structurally broken → dropped + counted
    ]
}


def _config(*entries: SourceEntry) -> PortalsConfig:
    return PortalsConfig(sources=list(entries), prefs=ScanPrefs())


def test_scan_filters_dedups_and_reports():
    config = _config(SourceEntry(url="https://boards.greenhouse.io/acme", company="Acme"))
    prefs = ScanPrefs(title_allow=["backend engineer"], location_allow=["india"])
    result = scan(config, prefs, fetcher_factory=routed({"/boards/acme/jobs": GH_PAYLOAD}))

    assert [j.canonical_url for j in result.jobs] == [
        "https://job-boards.greenhouse.io/acme/jobs/1"
    ]
    job = result.jobs[0]
    assert job.trust_score > 0 and job.source_adapter == "greenhouse"

    report = result.per_source["greenhouse:acme"]
    assert report.fetched == 5
    assert report.kept == 1
    assert report.usage.internal_calls == 1
    assert any("structurally broken" in e for e in report.errors)


def test_scan_dedups_across_sources_first_wins():
    config = _config(
        SourceEntry(url="https://boards.greenhouse.io/one", company="One"),
        SourceEntry(url="https://boards.greenhouse.io/two", company="Two"),
    )
    shared = {
        "jobs": [
            {
                "absolute_url": "https://example.com/jobs/same",
                "title": "Software Engineer",
                "location": {"name": "Remote, India"},
            }
        ]
    }
    result = scan(
        config,
        ScanPrefs(),
        fetcher_factory=routed({"/boards/one/jobs": shared, "/boards/two/jobs": shared}),
    )
    assert len(result.jobs) == 1
    assert result.jobs[0].company == "One"
    assert result.per_source["greenhouse:one"].kept == 1
    assert result.per_source["greenhouse:two"].kept == 0


def test_scan_failing_source_never_kills_the_scan():
    config = _config(
        SourceEntry(url="https://boards.greenhouse.io/down"),
        SourceEntry(url="https://boards.greenhouse.io/up", company="Up"),
    )
    boom = ScraperError("fetch", "could not fetch https://boards-api.greenhouse.io: HTTP 503")
    ok = {
        "jobs": [
            {
                "absolute_url": "https://example.com/jobs/9",
                "title": "Engineer",
                "location": {"name": "Mumbai, India"},
            }
        ]
    }
    result = scan(
        config,
        ScanPrefs(),
        fetcher_factory=routed({"/boards/down/jobs": boom, "/boards/up/jobs": ok}),
    )
    assert len(result.jobs) == 1
    assert result.per_source["greenhouse:down"].errors == [str(boom)]  # verbatim


def test_scan_unresolved_source_reported_not_fatal():
    config = _config(SourceEntry(url="https://jobs.example-unknown-ats.com/acme"))
    result = scan(config, ScanPrefs(), fetcher_factory=routed({}))
    assert result.jobs == []
    (key,) = result.per_source.keys()
    assert key.startswith("unresolved:")
    assert "no adapter claims this source" in result.per_source[key].errors[0]


def test_scan_per_source_cap_opt_in():
    many = {
        "jobs": [
            {
                "absolute_url": f"https://example.com/jobs/{i}",
                "title": "Engineer",
                "location": {"name": "Remote"},
            }
            for i in range(10)
        ]
    }
    config = _config(SourceEntry(url="https://boards.greenhouse.io/acme"))
    factory = routed({"/boards/acme/jobs": many})
    assert len(scan(config, ScanPrefs(), fetcher_factory=factory).jobs) == 10  # uncapped default
    capped = scan(config, ScanPrefs(per_source_cap=3), fetcher_factory=factory)
    assert len(capped.jobs) == 3


def test_scan_freshness_window():
    payload = {
        "jobs": [
            {
                "absolute_url": "https://example.com/jobs/old",
                "title": "Engineer",
                "location": {"name": "Remote"},
                "first_published": "2020-01-01T00:00:00+00:00",
            },
            {
                "absolute_url": "https://example.com/jobs/undated",
                "title": "Engineer",
                "location": {"name": "Remote"},
            },
        ]
    }
    config = _config(SourceEntry(url="https://boards.greenhouse.io/acme"))
    result = scan(
        config, ScanPrefs(max_age_days=30), fetcher_factory=routed({"/boards/acme/jobs": payload})
    )
    # old row aged out; undated row kept (source gave no date; quality flags it)
    assert [j.canonical_url for j in result.jobs] == ["https://example.com/jobs/undated"]
    assert "no-posted-date" in result.jobs[0].trust_flags


def test_jsonl_row_shape_matches_contract():
    config = _config(SourceEntry(url="https://boards.greenhouse.io/acme", company="Acme"))
    result = scan(config, ScanPrefs(), fetcher_factory=routed({"/boards/acme/jobs": GH_PAYLOAD}))
    row = json.loads(json.dumps(result.jobs[0].to_dict()))
    assert set(row) == {
        "title",
        "canonical_url",
        "company",
        "location",
        "description",
        "posted_at",
        "salary",
        "source_adapter",
        "trust_score",
        "trust_flags",
    }


# --- CLI ---


def test_cli_dry_run_prints_claims_no_network(capsys, tmp_path):
    from sidecar.modules.scraper.__main__ import main

    portals = tmp_path / "portals.toml"
    portals.write_text(
        '[[sources]]\nurl = "https://boards.greenhouse.io/gleanwork"\n'
        '[[sources]]\nurl = "https://unknown.example.com/x"\n'
    )
    assert main(["--portals", str(portals), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "greenhouse:gleanwork" in out
    assert "UNRESOLVED" in out


def test_cli_missing_config_fails_typed(capsys, tmp_path):
    from sidecar.modules.scraper.__main__ import main

    assert main(["--portals", str(tmp_path / "nope.toml"), "--dry-run"]) == 1
    assert "[portals-config]" in capsys.readouterr().err


def test_example_portals_config_parses_and_resolves():
    from pathlib import Path

    from sidecar.modules.scraper import adapters
    from sidecar.modules.scraper.config import load_portals

    example = (
        Path(__file__).resolve().parents[4]
        / "sidecar"
        / "modules"
        / "scraper"
        / "portals.example.toml"
    )
    config = load_portals(example)
    assert config.prefs.title_allow  # filters present
    for entry in config.sources:
        # every example entry must be claimed once all adapters land
        assert adapters.resolve(entry) is not None, f"unclaimed example source: {entry}"


# ---------------------------------------------------------------------------
# Source opt-outs (Settings → Discovery sources)
# ---------------------------------------------------------------------------


def test_disabled_family_is_skipped_before_any_fetch():
    config = _config(
        SourceEntry(url="https://boards.greenhouse.io/acme", company="Acme"),
        SourceEntry(board="remoteok"),
    )
    prefs = ScanPrefs(
        title_allow=["backend engineer"],
        disabled_sources=["greenhouse"],
    )
    # No greenhouse route on purpose: a disabled family must never be fetched,
    # so its absence can't produce an error row either.
    result = scan(
        config,
        prefs,
        fetcher_factory=routed(
            {"remoteok.com/api": [{"legal": ""}, {
                "position": "Backend Engineer",
                "url": "https://remoteok.com/remote-jobs/1",
                "company": "Acme",
                "location": "Remote",
            }]}
        ),
    )
    assert list(result.per_source) == ["remoteok:remoteok.com"]
    assert [j.source_adapter for j in result.jobs] == ["remoteok"]


def test_disabled_full_source_key_skips_only_that_entry():
    config = _config(
        SourceEntry(url="https://boards.greenhouse.io/one", company="One"),
        SourceEntry(url="https://boards.greenhouse.io/two", company="Two"),
    )
    prefs = ScanPrefs(
        title_allow=["software engineer"], disabled_sources=["greenhouse:two"]
    )
    result = scan(
        config,
        prefs,
        fetcher_factory=routed(
            {
                "/boards/one/jobs": {
                    "jobs": [
                        {
                            "absolute_url": "https://example.com/jobs/a",
                            "title": "Software Engineer",
                            "location": {"name": "Remote"},
                        }
                    ]
                }
            }
        ),
    )
    assert list(result.per_source) == ["greenhouse:one"]
    assert len(result.jobs) == 1


def test_disabled_sources_parse_from_portals_config():
    from sidecar.modules.scraper.config import parse_portals

    config = parse_portals(
        {
            "sources": [{"board": "remoteok"}],
            "disabled_sources": ["greenhouse", "apify:memo23/naukri-scraper"],
        }
    )
    assert config.prefs.disabled_sources == ["greenhouse", "apify:memo23/naukri-scraper"]
