"""The sidecar app layer — orchestration around the framework-free modules.

The one-way import rule (architecture §5.2, CI-linted): `app/` may import
`modules/`; `modules/` never imports `app/`. FastAPI / Pydantic / SQLAlchemy
live only here, never inside a module.

Skeleton-commit scope: handshake entrypoint, FastAPI skeleton (/healthz,
/shutdown), bearer auth, orphan watchdog, flight-recorder log. The runner,
registries, scheduler, SSE hub and db land with the core-storage commit
(`docs/internal/roadmap.md` §7.2 #3).
"""
