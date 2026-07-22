"""Content fingerprinting — SimHash cross-posting detection.

The bar: same posting on two hosts flags; genuinely different postings never
do; the annotation rides trust_flags and no row is ever dropped or merged
(annotate-never-drop — the dedup doc's explicit warning).
"""

from __future__ import annotations

from sidecar.modules.scraper.fingerprint import (
    is_probable_cross_listing,
    simhash64,
    similarity,
)
from sidecar.modules.scraper.scraper import _flag_cross_listings
from sidecar.modules.scraper.types import NormalizedJob

_JD = (
    "We are looking for a Senior Backend Engineer to join our platform team. "
    "You will design and operate distributed services in Python and Go, own "
    "reliability for our core APIs, and mentor other engineers. Requirements: "
    "5+ years building production systems, deep PostgreSQL experience, and a "
    "track record of pragmatic engineering. We offer remote-first work. "
    "About the role: the platform team owns ingestion, storage, and serving "
    "for all customer-facing data products. You will lead design reviews, "
    "operate services you build, define SLOs with the SRE group, and drive "
    "incident retrospectives to real remediation. Our stack runs on "
    "Kubernetes with Postgres, Kafka, and Redis; infrastructure is Terraform "
    "on AWS. What you will do: partner with product engineers to design "
    "APIs, profile and remove performance bottlenecks, harden the deployment "
    "pipeline, and raise the bar on testing and observability across the "
    "organization. What we look for: strong computer science fundamentals, "
    "experience operating high-throughput distributed systems in production, "
    "fluency in at least one systems language, empathy for on-call rotations "
    "you help design, and clear technical writing. Benefits include equity, "
    "comprehensive health coverage, a learning budget, and quarterly team "
    "offsites. Our interview process has four stages and takes two weeks. "
    "About the company: we build data infrastructure used by thousands of "
    "engineering teams to move, transform, and serve analytical data in real "
    "time. Founded in 2019, we are two hundred people across twelve time "
    "zones, profitable, and growing steadily. Engineering culture: we ship "
    "small changes behind flags, review each other's designs in writing, "
    "measure everything, and hold blameless retrospectives. We believe "
    "operational excellence is a feature and boring technology is a virtue. "
    "The team you join owns three services end to end, carries a humane "
    "on-call rotation with real compensation for pages, and spends one day a "
    "week on debt reduction and tooling. Compensation: base salary between "
    "one hundred eighty and two hundred twenty thousand dollars depending on "
    "experience, meaningful equity with a ten year exercise window, and an "
    "annual bonus tied to company performance. Location: remote within four "
    "hours of UTC, with optional hubs in Berlin, Bangalore, and Toronto. "
    "Equal opportunity: we welcome applicants of every background and make "
    "reasonable accommodations throughout the hiring process. To apply, "
    "submit a resume and a short note about a system you are proud of; cover "
    "letters are optional and read in full when provided. We respond to "
    "every application within five business days, no exceptions."
)

# Cross-posted copies differ by host boilerplate, not body — the reliable
# regime for the 0.92 threshold is realistic JD length (300+ tokens; at ~200
# the bit margins are too thin and similarity dips into the high 0.8s —
# verified empirically while building this).

_OTHER_JD = (
    "Our design team needs a Product Designer who loves user research. You "
    "will run discovery interviews, build Figma prototypes, and partner with "
    "product managers on roadmap bets. Requirements: a strong portfolio, "
    "3+ years in B2B SaaS, and fluency in design systems."
)


def test_identical_text_is_identical_fingerprint():
    assert simhash64(_JD) == simhash64(_JD) != 0


def test_lightly_edited_cross_posting_flags():
    # The LinkedIn copy of a Greenhouse posting: same body, host boilerplate
    # differs a little.
    edited = _JD.replace("remote-first work", "remote-first work and great benefits")
    assert is_probable_cross_listing(simhash64(_JD), simhash64(edited))


def test_different_postings_do_not_flag():
    assert not is_probable_cross_listing(simhash64(_JD), simhash64(_OTHER_JD))


def test_short_text_has_no_fingerprint():
    assert simhash64("Go engineer") == 0
    assert similarity(0, simhash64(_JD)) == 0.0


def test_flag_cross_listings_annotates_both_rows_never_drops():
    a = NormalizedJob(
        title="Senior Backend Engineer",
        canonical_url="https://boards.greenhouse.io/acme/jobs/1",
        description=_JD,
    )
    b = NormalizedJob(
        title="Senior Backend Engineer",
        canonical_url="https://www.linkedin.com/jobs/view/123",
        description=_JD + " Posted via LinkedIn.",
    )
    c = NormalizedJob(
        title="Product Designer",
        canonical_url="https://jobs.lever.co/acme/2",
        description=_OTHER_JD,
    )
    jobs = [a, b, c]
    _flag_cross_listings(jobs)
    assert len(jobs) == 3  # annotate, never drop
    assert f"probable-cross-listing:{b.canonical_url}" in a.trust_flags
    assert f"probable-cross-listing:{a.canonical_url}" in b.trust_flags
    assert not c.trust_flags
