# voyager_py/tests/test_contact_sync_probe.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Covers the READ-ONLY contact-status sync probe (FR-NW-15 / US-NW-12):

  - `parse_last_message` — pure parser: last-message direction (`me`/`them`) +
    timestamp from a Voyager conversations response, defensive on bad shapes.

Zero live LinkedIn traffic — the parser runs on fixture dicts. The prior repo's
`contact-sync` CLI dry-run tests are dropped: the JSON-CLI/subprocess bridge is
retired in this rebuild (see ../../../packages/referral_outreach/provenance.md).
"""

from __future__ import annotations

from sidecar.packages.referral_outreach.upstream.voyager import parse_last_message

TARGET = "urn:li:fsd_profile:ACoAAABcontact"
ME = "urn:li:fsd_profile:ACoAAABme000"


def _conv(events: list[dict]) -> dict:
    return {"elements": [{"messages": {"elements": events}}]}


def _event(sender: str, ts_ms: int) -> dict:
    return {"sender": {"*hostIdentityUrn": sender}, "deliveredAt": ts_ms}


def test_parse_last_message_them_when_target_sent_last():
    data = _conv([_event(ME, 1_700_000_001_000), _event(TARGET, 1_700_000_002_000)])
    direction, ts = parse_last_message(data, TARGET)
    assert direction == "them"
    assert ts == 1_700_000_002.0  # ms → seconds


def test_parse_last_message_me_when_we_sent_last():
    data = _conv([_event(TARGET, 1_700_000_001_000), _event(ME, 1_700_000_003_000)])
    direction, ts = parse_last_message(data, TARGET)
    assert direction == "me"
    assert ts == 1_700_000_003.0


def test_parse_last_message_none_on_empty_history():
    assert parse_last_message({"elements": []}, TARGET) == (None, None)
    assert parse_last_message({}, TARGET) == (None, None)


def test_parse_last_message_defensive_on_garbage():
    # Unexpected shapes never raise — they degrade to "no readable history".
    assert parse_last_message({"elements": "nope"}, TARGET) == (None, None)
    assert parse_last_message({"elements": [{"messages": {}}]}, TARGET) == (None, None)
    assert parse_last_message({"elements": [None, 5]}, TARGET) == (None, None)


def test_parse_last_message_seconds_left_as_is():
    # A timestamp already in seconds (< 1e11) is not divided again.
    data = _conv([_event(TARGET, 1_700_000_000)])
    _direction, ts = parse_last_message(data, TARGET)
    assert ts == 1_700_000_000.0


def test_parse_last_message_legacy_events_list():
    data = {"elements": [{"events": [_event(TARGET, 1_700_000_005_000)]}]}
    direction, ts = parse_last_message(data, TARGET)
    assert direction == "them" and ts == 1_700_000_005.0
