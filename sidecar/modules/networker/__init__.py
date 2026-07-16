"""Networker module (silo) — discover / draft / send referral outreach.

finds-you-jobs-owned (AGPL). Drives the GPLv3 OpenOutreach-derived worker
(`sidecar.packages.referral_outreach.upstream`) DIRECTLY in-process through the
`DirectVoyagerDriver` — the subprocess firewall the prior MIT-era repository
used is retired (`docs/internal/referral-outreach.md` §2). The one LLM
operation is `draft`.
"""

from .networker import discover, draft, dry_run_prompt, probe, quota, resolve, send
from .types import (
    Audience,
    Channel,
    CompanyCandidate,
    Contact,
    DiscoverResult,
    DraftResult,
    NetworkerError,
    ProbeResult,
    ResolveResult,
    SendResult,
    Usage,
    Warmth,
)

__all__ = [
    "discover",
    "resolve",
    "draft",
    "send",
    "probe",
    "quota",
    "dry_run_prompt",
    "Audience",
    "Warmth",
    "Channel",
    "CompanyCandidate",
    "Contact",
    "DiscoverResult",
    "ResolveResult",
    "DraftResult",
    "SendResult",
    "ProbeResult",
    "Usage",
    "NetworkerError",
]
