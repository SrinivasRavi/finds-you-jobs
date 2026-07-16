"""Scraper module silo — Track M3 (docs/ROADMAP.md §5).

Zero-LLM job discovery: per-source adapters over public JSON APIs and feeds,
URL auto-detection from a portals-style config, title/location filters,
canonical-URL dedup (FR-SYS-01), trust checks at ingest, per-source
usage/error diagnostics.
"""

from .probe import probe_url
from .scraper import scan
from .types import NormalizedJob, ScanPrefs, ScanResult, ScraperError, SourceReport, Usage

__all__ = [
    "NormalizedJob",
    "ScanPrefs",
    "ScanResult",
    "ScraperError",
    "SourceReport",
    "Usage",
    "probe_url",
    "scan",
]
