"""Covers: the Referral Outreach provenance and licensing records (roadmap commit 9).

These assert the licensing and provenance records are present and correct. The
typed protocol and its fake were deleted 2026-10-15: nothing in production
called them (10-10 report 4.3).
"""

from __future__ import annotations

from pathlib import Path

_PKG = Path(__file__).resolve().parents[3] / "packages" / "referral_outreach"


# ---------------------------------------------------------------------------
# Provenance / licensing records
# ---------------------------------------------------------------------------

_UPSTREAM_PIN = "a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a"


def test_gpl_license_text_is_present() -> None:
    license_text = (_PKG / "upstream" / "LICENSE").read_text(encoding="utf-8")
    assert "GNU GENERAL PUBLIC LICENSE" in license_text


def test_provenance_records_pin_and_direct_import_posture() -> None:
    prov = (_PKG / "provenance.md").read_text(encoding="utf-8")
    assert _UPSTREAM_PIN in prov
    assert "GPL-3.0-only" in prov
    # The retirement of the subprocess firewall is recorded.
    assert "subprocess" in prov.lower()
    assert "OpenOutreach" in prov


def test_upstream_files_carry_spdx_gpl_headers() -> None:
    for path in (_PKG / "upstream").glob("*.py"):
        head = path.read_text(encoding="utf-8")[:400]
        assert "SPDX-License-Identifier: GPL-3.0-only" in head, path.name


def test_subprocess_cli_is_not_carried() -> None:
    # The direct-in-process design drops the JSON-CLI bridge.
    assert not (_PKG / "upstream" / "cli.py").exists()
    assert not (_PKG / "upstream" / "__main__.py").exists()
