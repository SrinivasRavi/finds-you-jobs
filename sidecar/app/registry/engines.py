"""Engine registry (architecture section 5.4/section 9).

Named `Engine` instances + a routing map `operation kind → (engine, model)`.
In A3 the registry is typically empty: LLM kinds (`score`/`tailor`/`cover`)
then resolve to `None` and their wrappers raise `EngineNotConfiguredError` —
a clear typed error, never a silent hang. A4 wires the real engines + the
Settings-owned routing map onto this same surface.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class Engine(Protocol):
    """The only place model access lives (architecture section 9)."""

    def complete(self, system_prompt: str, user_prompt: str) -> tuple[str, Any]: ...


class EngineNotConfiguredError(Exception):
    """No engine is routed for an LLM operation kind. Surfaced verbatim on the
    operation row (NFR-SIDE-04) — never swallowed."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(
            f"no engine configured for {kind!r}: AI features stay off until an LLM "
            "provider is set up in Settings › LLM Providers"
        )


@dataclass
class ResolvedEngine:
    """A routed engine + the model it should run (audit trail for the ledger)."""

    engine: Engine
    name: str
    model: str | None = None


class EngineRegistry:
    """Named engines and a per-kind routing map. `register_factory` lets 1 engine
    honor a per-kind model override by building at the routed model; `clear_routing`
    re-applies routing without rebuilding."""

    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}
        self._factories: dict[str, Callable[[str | None], Engine]] = {}
        self._routing: dict[str, tuple[str, str | None]] = {}

    def reset(self) -> None:
        """Drop all engines, factories, and routing. The Settings BYOK CRUD path
        rebuilds the registry from scratch after each change (add/update/delete)
        so a removed provider leaves no stale factory behind."""
        self._engines.clear()
        self._factories.clear()
        self._routing.clear()

    def register(self, name: str, engine: Engine) -> None:
        self._engines[name] = engine

    def register_factory(self, name: str, factory: Callable[[str | None], Engine]) -> None:
        self._factories[name] = factory

    def route(self, kind: str, *, engine: str, model: str | None = None) -> None:
        self._routing[kind] = (engine, model)

    def clear_routing(self) -> None:
        self._routing.clear()

    def resolve(self, kind: str) -> ResolvedEngine | None:
        routed = self._routing.get(kind)
        if routed is None:
            return None
        name, model = routed
        engine = self._engines.get(name)
        if engine is None:
            factory = self._factories.get(name)
            if factory is not None:
                engine = factory(model)
        if engine is None:
            return None
        return ResolvedEngine(engine=engine, name=name, model=model)
