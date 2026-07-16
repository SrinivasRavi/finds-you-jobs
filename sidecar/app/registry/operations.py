"""Operation registry: `kind → entrypoint` (architecture §5.4).

An entrypoint is the thin app-side wrapper over one `sidecar.modules.*` bounded
operation. It receives an `OperationContext` (the durable input snapshot + a
resolved engine for LLM kinds) and returns an `OperationOutcome` (result_ref +
usage + engine/model for the ledger). The runner never knows what a kind *does*
— only this contract.

**Core-storage boundary.** This commit ships the contract and an empty default
registry: real kinds (scan/score/tailor/cover/…) register here as their module
commits land (`docs/internal/roadmap.md` §7.2 #5+). The core tests exercise the
runner with fake entrypoints only.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING, Any

from .engines import EngineNotConfiguredError, ResolvedEngine

if TYPE_CHECKING:
    from ..db import Database

PublishFn = Callable[[dict[str, Any]], None]


@dataclass
class OperationContext:
    """Everything an entrypoint needs to run one operation.

    `db` + `operation_id` let entrypoints persist their results — scan writes
    `Job` rows, score writes a `JobScore`, tailor/cover fill their pre-created
    `Artifact` (found by `operation_id`). Both are `None` under the
    fake-entrypoint runner tests, which never touch storage."""

    kind: str
    input_snapshot: dict[str, Any]
    engine: ResolvedEngine | None = None
    db: Database | None = None
    operation_id: str | None = None
    # Lets an entrypoint stream its own typed sub-events onto the SSE hub
    # (the Applier live-modal substream). None under the fake-entrypoint tests.
    publish: PublishFn | None = None


@dataclass
class OperationOutcome:
    """What an entrypoint hands back for the ledger + result pointer."""

    result_ref: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    engine: str | None = None
    model: str | None = None


Entrypoint = Callable[[OperationContext], OperationOutcome]


class UnknownOperationKind(Exception):
    """No entrypoint registered for this kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"no entrypoint registered for operation kind {kind!r}")


class OperationRegistry:
    def __init__(self, entries: dict[str, Entrypoint] | None = None) -> None:
        self._entries: dict[str, Entrypoint] = dict(entries or {})

    def register(self, kind: str, entrypoint: Entrypoint) -> None:
        self._entries[kind] = entrypoint

    def resolve(self, kind: str) -> Entrypoint:
        entrypoint = self._entries.get(kind)
        if entrypoint is None:
            raise UnknownOperationKind(kind)
        return entrypoint

    def kinds(self) -> frozenset[str]:
        return frozenset(self._entries)


def _usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if is_dataclass(usage) and not isinstance(usage, type):
        return asdict(usage)
    if isinstance(usage, dict):
        return usage
    return None


# ---------------------------------------------------------------------------
# The real wrappers (app → modules import is allowed and correct).
# ---------------------------------------------------------------------------


def _require_engine(ctx: OperationContext) -> ResolvedEngine:
    if ctx.engine is None:
        raise EngineNotConfiguredError(ctx.kind)
    return ctx.engine


def extract_entrypoint(ctx: OperationContext) -> OperationOutcome:
    """Extract the structured application profile from the current master
    resume (Profiler module) → `master_profiles.application_profile`
    (FR-APP-01). Routed engine — one small call. (The prior repository threads
    a user-editable prompt override here; that returns with the
    prompt-overrides feature.)"""
    resolved = _require_engine(ctx)
    from sidecar.modules.profiler import extract_profile

    if ctx.db is None:
        raise RuntimeError("extract operation requires a database context")
    with ctx.db.repos() as repos:
        profile_row = repos.profile.get_current()
        if profile_row is None:
            raise LookupError("no master profile to extract an application profile from")
        master_md = profile_row.resume_markdown
        version = profile_row.version

    result = extract_profile(master_md, engine=resolved.engine)
    record = {**result.profile, "profile_version": version, "source": "extracted"}
    with ctx.db.repos() as repos:
        repos.profile.set_application_profile(record)
    return OperationOutcome(
        result_ref={
            "profile_version": version,
            "keys_filled": sorted(k for k, v in result.profile.items() if v),
        },
        usage=_usage_to_dict(result.usage),
        engine=resolved.name,
        model=(_usage_to_dict(result.usage) or {}).get("model") or resolved.model,
    )


def default_operation_registry() -> OperationRegistry:
    """The app's real `kind → entrypoint` table. Grows as module commits land
    (architecture §5.4)."""
    return OperationRegistry(
        {
            "extract": extract_entrypoint,
        }
    )
