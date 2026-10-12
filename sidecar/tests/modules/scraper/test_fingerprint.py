"""Content fingerprinting — SimHash cross-posting detection.

The bar: same posting on two hosts flags; genuinely different postings never
do; the annotation rides trust_flags and no row is ever dropped or merged
(annotate-never-drop — the dedup doc's explicit warning).
"""

from __future__ import annotations

from sidecar.modules.scraper.fingerprint import (
    SIMILARITY_THRESHOLD,
    is_probable_cross_listing,
    simhash64,
    similarity,
)

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
# regime for the similarity threshold is realistic JD length (300+ tokens; at ~200
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


def test_adjacent_seniority_at_one_company_does_not_flag():
    # url-less-jobs.md section 6.4: employer boilerplate puts any 2 postings
    # from 1 company above the similarity threshold, which is why the gate
    # also compares company and title. 9 of 21 flags on the bench corpus were
    # this shape (`Principal SWE` against `Senior SWE` at the same employer).
    senior = _JD
    principal = _JD.replace("remote-first work", "remote-first work and equity")
    assert similarity(simhash64(senior), simhash64(principal)) >= SIMILARITY_THRESHOLD
    assert not is_probable_cross_listing(
        simhash64(senior),
        simhash64(principal),
        company_a="uipath",
        company_b="uipath",
        title_a="Senior Software Engineer",
        title_b="Principal Software Engineer",
    )


def test_same_title_at_different_companies_does_not_flag():
    edited = _JD.replace("remote-first work", "remote-first work and equity")
    assert not is_probable_cross_listing(
        simhash64(_JD),
        simhash64(edited),
        company_a="Acme",
        company_b="Globex",
        title_a="Senior Software Engineer",
        title_b="Senior Software Engineer",
    )


def test_labels_normalize_before_comparing():
    edited = _JD.replace("remote-first work", "remote-first work and equity")
    assert is_probable_cross_listing(
        simhash64(_JD),
        simhash64(edited),
        company_a="UiPath, Inc.",
        company_b="uipath inc",
        title_a="Senior Software Engineer",
        title_b="senior   software engineer",
    )
