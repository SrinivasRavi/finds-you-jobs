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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .engines import ResolvedEngine

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


def default_operation_registry() -> OperationRegistry:
    """The app's real `kind → entrypoint` table. Empty at the core-storage
    commit; each module commit registers its kinds here."""
    return OperationRegistry()
