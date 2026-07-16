"""Covers: the Referral Outreach facade contract + provenance (roadmap commit 9).

Provenance-only commit: the GPLv3 `upstream/` core is carried but not yet
imported/wired. These tests exercise the AGPL facade contract via the fake and
assert the licensing/provenance records are present and correct.
"""

from __future__ import annotations

from pathlib import Path

from sidecar.packages.referral_outreach import (
    AccountRef,
    ConnectionRequest,
    ContactProbeRequest,
    DirectMessageRequest,
    DiscoveredContact,
    DiscoverRequest,
    FakeReferralAutomation,
    ReferralAutomation,
    SessionCaptureRequest,
    SessionStatusRequest,
)

_PKG = Path(__file__).resolve().parents[3] / "packages" / "referral_outreach"


# ---------------------------------------------------------------------------
# Facade contract (via the fake)
# ---------------------------------------------------------------------------


def test_fake_satisfies_the_protocol() -> None:
    automation: ReferralAutomation = FakeReferralAutomation()
    # Structural typing — the fake IS a ReferralAutomation.
    assert isinstance(automation, FakeReferralAutomation)


def test_fake_capture_session_streams_and_returns() -> None:
    fake = FakeReferralAutomation(connected_as="Jane Doe")
    events: list[dict] = []
    result = fake.capture_session(
        SessionCaptureRequest(storage_state_path="/tmp/s.json"), events.append  # noqa: S108
    )
    assert result.ok is True
    assert result.connected_as == "Jane Doe"
    assert events == [{"phase": "capturing"}]


def test_fake_session_status_reflects_validity() -> None:
    assert (
        FakeReferralAutomation(session_valid=False)
        .session_status(SessionStatusRequest(storage_state_path="/tmp/s.json"))  # noqa: S108
        .valid
        is False
    )


def test_fake_discover_respects_limit() -> None:
    contacts = [DiscoveredContact(public_identifier=f"p{i}") for i in range(5)]
    fake = FakeReferralAutomation(contacts=contacts)
    result = fake.discover(DiscoverRequest(company_urn="urn:li:company:1", limit=3))
    assert [c.public_identifier for c in result.contacts] == ["p0", "p1", "p2"]


def test_fake_send_paths_return_quota() -> None:
    fake = FakeReferralAutomation()
    conn = fake.send_connection(ConnectionRequest(public_identifier="jane", note="hi"))
    dm = fake.send_dm(DirectMessageRequest(public_identifier="jane", message="hi"))
    assert conn.ok and dm.ok
    assert conn.quota is not None and conn.quota.daily_cap == 15


def test_fake_records_calls_for_assertions() -> None:
    fake = FakeReferralAutomation()
    fake.probe_contact(ContactProbeRequest(public_identifier="jane"))
    fake.quota(AccountRef(storage_state_path="/tmp/s.json"))  # noqa: S108
    kinds = [name for name, _ in fake.calls]
    assert kinds == ["probe_contact", "quota"]


# ---------------------------------------------------------------------------
# Provenance / licensing records
# ---------------------------------------------------------------------------

_UPSTREAM_PIN = "a7a9101af255d72ee5df7fbf1dfd1d7fd5fd8a1a"


def test_gpl_license_text_is_present() -> None:
    license_text = (_PKG / "upstream" / "LICENSE").read_text()
    assert "GNU GENERAL PUBLIC LICENSE" in license_text


def test_provenance_records_pin_and_direct_import_posture() -> None:
    prov = (_PKG / "provenance.md").read_text()
    assert _UPSTREAM_PIN in prov
    assert "GPL-3.0-only" in prov
    # The retirement of the subprocess firewall is recorded.
    assert "subprocess" in prov.lower()
    assert "OpenOutreach" in prov


def test_upstream_files_carry_spdx_gpl_headers() -> None:
    for path in (_PKG / "upstream").glob("*.py"):
        head = path.read_text()[:400]
        assert "SPDX-License-Identifier: GPL-3.0-only" in head, path.name


def test_subprocess_cli_is_not_carried() -> None:
    # The direct-in-process design drops the JSON-CLI bridge.
    assert not (_PKG / "upstream" / "cli.py").exists()
    assert not (_PKG / "upstream" / "__main__.py").exists()
