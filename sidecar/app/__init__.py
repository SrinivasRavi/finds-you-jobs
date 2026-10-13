"""The sidecar app layer: orchestration around the framework-free modules.

The one-way import rule (architecture section 5.2, CI-linted): `app/` may import
`modules/`, never the reverse. FastAPI, Pydantic and SQLAlchemy live only here."""
