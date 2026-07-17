# voyager_py/tests/test_secure_store.py — GPL v3 (see ../LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
"""Encrypted-at-rest storage-state store (NFR-SEC-01). Pure logic — no browser,
no network. The host passes the Fernet key via FYJ_SESSION_KEY; readers accept
both sealed and legacy plaintext files so an existing session is never lost."""

from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from sidecar.packages.referral_outreach.upstream.errors import VoyagerError
from sidecar.packages.referral_outreach.upstream.secure_store import (
    SEALED_MARKER,
    SESSION_KEY_ENV,
    UnreadableStateFile,
    load_state_file,
    save_state_file,
)

STATE = {"cookies": [{"name": "li_at", "value": "SECRET_TOKEN_VALUE", "expires": 2_000_000_000}]}


def test_sealed_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    path = tmp_path / "storage_state.json"
    save_state_file(path, STATE)

    raw = path.read_text()
    assert SEALED_MARKER in raw
    assert "li_at" not in raw and "SECRET_TOKEN_VALUE" not in raw  # nothing plaintext
    assert load_state_file(path) == STATE


def test_no_key_writes_plaintext_and_reads_back(tmp_path, monkeypatch):
    # The standalone-CLI path: no key in the env → plaintext (with a warning).
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    path = tmp_path / "storage_state.json"
    save_state_file(path, STATE)
    assert json.loads(path.read_text()) == STATE
    assert load_state_file(path) == STATE


def test_legacy_plaintext_read_with_key_set(tmp_path, monkeypatch):
    # A pre-encryption file must stay readable after the key exists (migration
    # safety: the session is never invalidated by the upgrade).
    path = tmp_path / "storage_state.json"
    path.write_text(json.dumps(STATE))
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    assert load_state_file(path) == STATE


def test_sealed_without_key_raises_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    path = tmp_path / "storage_state.json"
    save_state_file(path, STATE)
    monkeypatch.delenv(SESSION_KEY_ENV)
    with pytest.raises(VoyagerError, match="encrypted but FYJ_SESSION_KEY is not set"):
        load_state_file(path)


def test_sealed_with_wrong_key_raises_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())
    path = tmp_path / "storage_state.json"
    save_state_file(path, STATE)
    monkeypatch.setenv(SESSION_KEY_ENV, Fernet.generate_key().decode())  # different key
    with pytest.raises(VoyagerError, match="wrong or rotated"):
        load_state_file(path)


def test_missing_file_is_none_and_corrupt_is_typed(tmp_path):
    assert load_state_file(tmp_path / "absent.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    with pytest.raises(UnreadableStateFile):
        load_state_file(corrupt)


def test_inspect_tolerates_corrupt_file(tmp_path):
    from sidecar.packages.referral_outreach.upstream.session import inspect_storage_state

    corrupt = tmp_path / "storage_state.json"
    corrupt.write_text("{not json")
    info = inspect_storage_state(corrupt)
    assert info == {
        "present": False, "has_auth_cookie": False, "expired": False, "li_at_expires": None
    }
