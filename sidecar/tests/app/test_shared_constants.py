"""Covers: the constants the frontend copies are published in the OpenAPI
schema as literal types (sidecar/app/shared_constants.py, audit finding 11),
so `pnpm codegen` can turn a drift into a TypeScript compile error."""

from __future__ import annotations

from sidecar.app.main import create_app
from sidecar.app.registry.engine_config import CLI_PROVIDERS
from sidecar.modules.scorer.weights import PRESET_LOCATION_PENALTY


def test_the_schema_publishes_each_constant_as_a_literal() -> None:
    app = create_app(token="t", enable_scheduler=False)  # noqa: S106
    shared = app.openapi()["components"]["schemas"]["SharedConstants"]["properties"]
    penalty = shared["preset_location_penalty"]["properties"]
    assert {k: v["enum"] for k, v in penalty.items()} == {
        k: [v] for k, v in PRESET_LOCATION_PENALTY.items()
    }
    assert set(shared["cli_provider"]["enum"]) == set(CLI_PROVIDERS)
    assert shared["attach_types"]["properties"][".pdf"]["enum"] == ["application/pdf"]
