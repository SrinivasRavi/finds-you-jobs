"""Per-kind concurrency policy (architecture §5.3) — table-driven + tunable.

Defaults: LLM kinds (`score`/`tailor`/`cover`) share a pool of ≤ 2 in flight;
`scan` is single-flight; `apply` runs exclusively (nothing else concurrent).
`can_start` is a pure decision function so the policy is unit-testable without
touching threads or the DB.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConcurrencyPolicy:
    groups: dict[str, str] = field(default_factory=dict)  # kind -> group name
    group_limits: dict[str, int] = field(default_factory=dict)  # group -> max in flight
    exclusive_kinds: frozenset[str] = frozenset()  # kinds that run alone

    def group_for(self, kind: str) -> str:
        return self.groups.get(kind, kind)

    def limit_for_group(self, group: str) -> int:
        return self.group_limits.get(group, 1)


DEFAULT_POLICY = ConcurrencyPolicy(
    groups={
        "score": "llm",
        "tailor": "llm",
        "cover": "llm",
        "draft": "llm",  # the Networker's one LLM op shares the LLM pool (Track N3)
        "scan": "scan",
        # Trash-TTL eviction (FR-SYS-04): zero-LLM, DB-only, single-flight.
        "cleanup_trash": "cleanup_trash",
        "apply": "apply",
        # Networking voyager ops (Track N3) are each single-flight: LinkedIn
        # automation must never fan out in parallel (account-safety, NFR-LI-*).
        "discover": "networker_discover",
        "send": "networker_send",
        # The headed login (Track N4) opens a visible browser the user drives —
        # exclusive so nothing else contends for it (like apply).
        "linkedin_login": "linkedin_login",
        "archive_stale_contacts": "archive_stale_contacts",
    },
    group_limits={
        "llm": 2, "scan": 1, "cleanup_trash": 1, "apply": 1,
        "networker_discover": 1, "networker_send": 1,
        "linkedin_login": 1, "archive_stale_contacts": 1,
    },
    exclusive_kinds=frozenset({"apply", "linkedin_login"}),
)


def can_start(kind: str, running_kinds: Iterable[str], policy: ConcurrencyPolicy) -> bool:
    """True if an operation of `kind` may start given what is already running.

    Rules, in order:
      1. If an exclusive operation is running, nothing else may start.
      2. If `kind` is exclusive, it may start only when nothing is running.
      3. Otherwise `kind` may start while its concurrency group is under limit.
    """
    running = list(running_kinds)
    if any(k in policy.exclusive_kinds for k in running):
        return False
    if kind in policy.exclusive_kinds:
        return len(running) == 0
    group = policy.group_for(kind)
    running_in_group = sum(1 for k in running if policy.group_for(k) == group)
    return running_in_group < policy.limit_for_group(group)
