"""Dump the sidecar's OpenAPI schema to stdout (architecture §4.3)."""

from __future__ import annotations

import json

from .main import create_app


def main() -> None:
    app = create_app(token="codegen")  # noqa: S106 — schema introspection, not a secret
    print(json.dumps(app.openapi(), indent=2))


if __name__ == "__main__":
    main()
