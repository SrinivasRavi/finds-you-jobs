"""The bearer token the shared `app_client` fixture builds its app with.

Lives beside `conftest.py` rather than inside it so a test module can import
the header without reaching into a conftest. 28 modules each declared their own
unique token string and its own `# noqa: S105`, which bought nothing: every
test builds its own app (duplication audit D-T3).
"""

from __future__ import annotations

TOKEN = "test-token"  # noqa: S105 (test fixture, not a real secret)
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def seed_application_profile(db) -> None:  # noqa: ANN001 (a Database; kept import-free)
    """A master resume plus the facts read from it, which Apply needs (audit A5)."""
    with db.repos() as repos:
        if repos.profile.get_current() is None:
            repos.profile.upsert("# Ada Lovelace\n\nBackend engineer.")
        repos.profile.set_application_profile(
            {"name": "Ada Lovelace", "email": "ada@example.com", "source": "edited"}
        )
