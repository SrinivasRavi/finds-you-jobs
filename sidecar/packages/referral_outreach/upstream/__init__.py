# referral_outreach.upstream — GPL v3 subtree (see LICENSE). Forked from OpenOutreach.
#
# SPDX-License-Identifier: GPL-3.0-only
#
# This subtree is licensed GPL v3 and CANNOT be relicensed. finds-you-jobs is
# AGPL-3.0-only, and GPLv3 + AGPLv3 are compatible for this combination, so the
# code is imported and called DIRECTLY in-process (no subprocess firewall — the
# subprocess boundary the prior MIT-era repository used is retired here;
# `docs/internal/referral-outreach.md` §2). The GPL notices on these files are
# retained; the aggregate is AGPL-based while these files stay GPLv3.
#
# NOTE: inline comments below that reference "the subprocess", "the MIT host",
# or `python -m voyager_py` are carried verbatim from the subprocess-era fork
# and describe that origin; they do not reflect this repository's direct-import
# architecture. `provenance.md` at the package root is the authoritative record.
#
# Upstream: OpenOutreach — https://github.com/eracle/OpenOutreach
# Forked at commit a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a (2026-04-29).
# See ../provenance.md for the take/trim table and what was stripped.
"""LinkedIn voyager client (GPL) — carried for direct in-process use.

The finds-you-jobs Referral Outreach facade (`referral_outreach.client`, AGPL)
imports and drives this code directly; the JSON-CLI (`cli.py`/`__main__.py`)
and its subprocess bridge are deliberately not carried.
"""

__version__ = "0.0.1"

# Upstream provenance, surfaced in the CLI `status`/`--version` envelope.
UPSTREAM_REPO = "https://github.com/eracle/OpenOutreach"
UPSTREAM_COMMIT = "a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a"
