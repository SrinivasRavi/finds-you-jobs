"""Covers: secrets-at-rest primitives (NFR-SEC-01) — key resolution + sealing.

The LinkedIn session-file seal/read/write tests return with the Referral
Outreach commits, alongside the helpers they cover.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sidecar.app.security import (
    KEY_FILE_NAME,
    SESSION_KEY_ENV,
    get_app_key,
    get_session_key,
    mask_key,
    open_secret,
    seal_secret,
)


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(SESSION_KEY_ENV, key)
    assert get_session_key(tmp_path) == key
    # No key file is created when the env override answers.
    assert not (tmp_path / KEY_FILE_NAME).exists()


def test_key_file_fallback_creates_owner_only_and_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    first = get_session_key(tmp_path, use_keyring=False)
    path = tmp_path / KEY_FILE_NAME
    assert path.exists()
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    # Stable across calls — the same key comes back, no rotation.
    assert get_session_key(tmp_path, use_keyring=False) == first
    # And it is a usable Fernet key.
    Fernet(first.encode())


def test_get_app_key_is_the_session_key() -> None:
    assert get_app_key is get_session_key


def test_seal_open_secret_roundtrip_never_plaintext(tmp_path: Path) -> None:
    key = Fernet.generate_key().decode()
    plaintext = "sk-ant-secret-value-123456"
    token = seal_secret(plaintext, key)
    assert plaintext.encode() not in token
    assert b"secret" not in token
    assert open_secret(token, key) == plaintext


def test_mask_key_reveals_only_a_hint() -> None:
    assert mask_key("sk-ant-api-key-abcd1234") == "sk-…1234"
    assert mask_key("plainlongtokenvalue9876") == "…9876"
    # Short keys reveal nothing at all.
    assert mask_key("short") == "…"
    assert mask_key("") == "…"
