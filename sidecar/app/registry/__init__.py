"""Registries (architecture §5.4).

The operation registry (`kind → module entrypoint`) and the engine registry
(named `Engine` instances + a routing map `kind → (engine, model)`). The runner
resolves both when it dispatches an operation; a community alternative
implementation of a kind is a new entrypoint wired here — the interface is the
contract, implementations bend to it.
"""

from __future__ import annotations

from .engines import EngineNotConfiguredError, EngineRegistry, ResolvedEngine
from .operations import (
    OperationContext,
    OperationOutcome,
    OperationRegistry,
    UnknownOperationKind,
    default_operation_registry,
)

__all__ = [
    "EngineNotConfiguredError",
    "EngineRegistry",
    "OperationContext",
    "OperationOutcome",
    "OperationRegistry",
    "ResolvedEngine",
    "UnknownOperationKind",
    "default_operation_registry",
]
