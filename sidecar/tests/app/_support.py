"""The bearer token the shared `app_client` fixture builds its app with.

Lives beside `conftest.py` rather than inside it so a test module can import
the header without reaching into a conftest. 28 modules each declared their own
unique token string and its own `# noqa: S105`, which bought nothing: every
test builds its own app (duplication audit D-T3).
"""

from __future__ import annotations

TOKEN = "test-token"  # noqa: S105 (test fixture, not a real secret)
AUTH = {"Authorization": f"Bearer {TOKEN}"}
