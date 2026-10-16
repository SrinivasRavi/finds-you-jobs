"""scripts/beta_rubric.py, the maintainer's S-G8 report (backlog S-C77)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[3] / "scripts" / "beta_rubric.py"
_spec = importlib.util.spec_from_file_location("beta_rubric", _PATH)
assert _spec is not None and _spec.loader is not None
beta_rubric = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(beta_rubric)


def test_seconds_reads_a_naive_and_a_utc_stamp_as_the_same_clock() -> None:
    """The ledger holds naive and UTC-aware stamps; subtracting 1 of each raised
    TypeError and ended the whole report."""
    assert beta_rubric._seconds("2026-10-01T10:00:00", "2026-10-01T10:00:05+00:00") == 5.0
    assert beta_rubric._seconds("2026-10-01T10:00:00+00:00", "2026-10-01T10:00:07") == 7.0
    assert beta_rubric._seconds(None, "2026-10-01T10:00:07") is None
