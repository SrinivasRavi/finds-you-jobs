"""Covers: secrets-at-rest primitives (NFR-SEC-01) — key resolution + sealing.

The LinkedIn session-file seal/read/write tests return with the Referral
Outreach commits, alongside the helpers they cover.
"""

from __future__ import annotations

import getpass
import json
import os
import sqlite3
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from sidecar.app.security import (
    KEY_FILE_NAME,
    SESSION_KEY_ENV,
    AppKeyUnavailable,
    clear_key_cache,
    get_app_key,
    get_session_key,
    mask_key,
    open_secret,
    seal_secret,
)


@pytest.fixture(autouse=True)
def _fresh_key_cache() -> Iterator[None]:
    """The resolver caches per data dir for the process's life. Each test needs
    to observe the real resolution, not a neighbour's."""
    clear_key_cache()
    yield
    clear_key_cache()


def _seal_a_secret_in_db(data_dir: Path) -> None:
    """The minimum an install needs to look like it has sealed something: one
    engine_settings row with a non-null key_encrypted."""
    with sqlite3.connect(data_dir / "db.sqlite") as conn:
        conn.execute("CREATE TABLE engine_settings (engine_id TEXT, key_encrypted BLOB)")
        conn.execute("INSERT INTO engine_settings VALUES ('anthropic', X'DEADBEEF')")


def _break_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store that raises on read — a locked keychain, a dead D-Bus, a revoked
    ACL. Distinct from a store that answers and holds nothing."""
    import keyring

    def boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("keychain is locked")

    monkeypatch.setattr(keyring, "get_password", boom)
    monkeypatch.setattr(keyring, "set_password", boom)


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key().decode()
    monkeypatch.setenv(SESSION_KEY_ENV, key)
    assert get_session_key(tmp_path) == key
    # No key file is created when the env override answers.
    assert not (tmp_path / KEY_FILE_NAME).exists()


def test_env_override_is_read_ahead_of_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caching the override would leak one test's key into the next."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    minted = get_session_key(tmp_path, use_keyring=False)
    override = Fernet.generate_key().decode()
    monkeypatch.setenv(SESSION_KEY_ENV, override)
    assert get_session_key(tmp_path, use_keyring=False) == override != minted


def _acl_entries(path: Path) -> list[str]:
    """The account grants `icacls` reports for `path`, one per line. Windows
    only; the header/footer lines it prints are dropped."""
    icacls = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe"
    )
    proc = subprocess.run(  # noqa: S603
        [icacls, str(path)], capture_output=True, text=True, check=False, timeout=15
    )
    assert proc.returncode == 0, proc.stderr
    head, _, rest = proc.stdout.partition(str(path))
    return [ln.strip() for ln in rest.splitlines() if ln.strip() and ":" in ln]


def test_key_file_fallback_creates_owner_only_and_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    first = get_session_key(tmp_path, use_keyring=False)
    path = tmp_path / KEY_FILE_NAME
    assert path.exists()
    if os.name == "nt":
        # Windows has no mode bits — `stat` reports 0o666 whatever `os.open`
        # was given, so the bar is the ACL, and it is parity with 0600 rather
        # than a single ACE: 0600 never shut out root either. What must never
        # appear is a principal that is somebody ELSE — `BUILTIN\Users` and
        # `Everyone` above all. See `_harden_owner_only` for why.
        entries = _acl_entries(path)
        assert entries, "icacls listed no ACL entries for the key file"
        account = (os.environ.get("USERNAME") or getpass.getuser()).lower()
        allowed = (
            account,
            # Windows' root, and an Administrator can take ownership anyway.
            r"nt authority\system",
            r"builtin\administrators",
            # Owner-scoped pseudo-SIDs (S-1-3-4, S-1-3-0). Both resolve to the
            # owner, so neither can hand a 3rd party anything.
            "owner rights",
            "creator owner",
        )
        assert all(any(who in e.lower() for who in allowed) for e in entries), entries
    else:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Stable across calls — the same key comes back, no rotation. Cleared first,
    # so this reads the file again instead of proving only that a dict works.
    clear_key_cache()
    assert get_session_key(tmp_path, use_keyring=False) == first
    # And it is a usable Fernet key.
    Fernet(first.encode())


def test_windows_key_file_gets_an_explicit_owner_only_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows half of "owner-only" is an `icacls` grant, and this machine
    may not be Windows — so pin the command we would run there."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("USERNAME", "ada")
    monkeypatch.setenv("USERDOMAIN", "LOVELACE")
    calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):  # noqa: ANN001, ANN202
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    get_session_key(tmp_path, use_keyring=False)

    assert len(calls) == 1, calls
    argv = calls[0]
    assert argv[0].lower().endswith("icacls.exe")
    assert argv[1] == str(tmp_path / KEY_FILE_NAME)
    # Inheritance dropped, then exactly one grant, to the running account.
    assert argv[2:] == ["/inheritance:r", "/grant:r", "LOVELACE\\ada:F"]


def test_a_failed_acl_call_never_breaks_key_minting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hardening is best effort: the key is already written when it runs, and
    losing the app key is far worse than a permissive ACL."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    monkeypatch.setattr(os, "name", "nt")

    def _boom(argv, **kwargs):  # noqa: ANN001, ANN202
        raise OSError("icacls is missing")

    monkeypatch.setattr(subprocess, "run", _boom)
    key = get_session_key(tmp_path, use_keyring=False)
    assert (tmp_path / KEY_FILE_NAME).exists()
    Fernet(key.encode())


def test_resolution_is_cached_so_the_store_is_consulted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every call after the first must be a dict lookup: `keyring` blocks, and
    the shell kills the process group on one missed 2s health probe."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    import keyring

    stored = Fernet.generate_key().decode()
    reads = 0

    def counted(*_args: object, **_kwargs: object) -> str:
        nonlocal reads
        reads += 1
        return stored

    monkeypatch.setattr(keyring, "get_password", counted)
    assert get_session_key(tmp_path) == stored
    before = reads
    for _ in range(5):
        assert get_session_key(tmp_path) == stored
    assert reads == before


def test_unreadable_store_with_sealed_data_raises_instead_of_minting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The data-loss bug, pinned. A keychain that throws used to be indis-
    tinguishable from a fresh install, so the file path minted a SECOND key and
    every sealed secret went silently unreadable."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    _seal_a_secret_in_db(tmp_path)
    _break_keyring(monkeypatch)

    with pytest.raises(AppKeyUnavailable):
        get_session_key(tmp_path)
    # And critically: no replacement key was written anywhere.
    assert not (tmp_path / KEY_FILE_NAME).exists()


def test_empty_store_with_sealed_data_also_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A backend that answers and holds nothing is the same loss: the key that
    opened those secrets is gone either way."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    _seal_a_secret_in_db(tmp_path)
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *_a, **_k: None)

    with pytest.raises(AppKeyUnavailable):
        get_session_key(tmp_path)
    assert not (tmp_path / KEY_FILE_NAME).exists()


def test_a_sealed_linkedin_session_counts_as_sealed_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    state = tmp_path / "linkedin" / "storage_state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"fyj_sealed": 1, "token": "x"}), encoding="utf-8")
    _break_keyring(monkeypatch)

    with pytest.raises(AppKeyUnavailable):
        get_session_key(tmp_path)


def test_unreadable_store_on_a_fresh_profile_mints_to_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No sealed data means nothing to lose, so a broken keychain must NOT stop
    a new user — a Linux box with no Secret Service has to keep working."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    _break_keyring(monkeypatch)

    key = get_session_key(tmp_path)
    Fernet(key.encode())
    assert (tmp_path / KEY_FILE_NAME).exists()


def test_an_existing_key_file_is_used_when_the_store_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file is the fallback store, so data sealed under it stays openable
    even when the keychain stops answering."""
    monkeypatch.delenv(SESSION_KEY_ENV, raising=False)
    existing = get_session_key(tmp_path, use_keyring=False)
    _seal_a_secret_in_db(tmp_path)
    clear_key_cache()
    _break_keyring(monkeypatch)

    assert get_session_key(tmp_path) == existing


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


def test_a_missing_app_key_reaches_the_user_as_a_message_not_a_500(tmp_path: Path) -> None:
    """The whole point of `AppKeyUnavailable` is a careful, actionable message,
    and nothing caught it: FastAPI has no global handler by default, so the
    first request touching a secret answered a bare 500 and the message lived
    only in `logs/sidecar.log`. 503 with `detail` verbatim — the frontend
    already renders `detail`."""
    from fastapi.testclient import TestClient

    from sidecar.app.main import create_app

    token = "test-token-appkey"  # noqa: S105 — test fixture, not a real secret
    app = create_app(token=token, original_ppid=None, data_dir=tmp_path / "data")
    with TestClient(app) as client:
        import sidecar.app.api.engines as engines_api

        def _no_key(*_args: object, **_kwargs: object) -> str:
            raise AppKeyUnavailable("this install has encrypted secrets but its app key is missing")

        engines_api.get_app_key = _no_key  # type: ignore[assignment]
        try:
            r = client.post(
                "/api/engines",
                headers={"Authorization": f"Bearer {token}"},
                json={"provider": "anthropic", "key": "sk-ant-whatever", "enabled": True},
            )
        finally:
            engines_api.get_app_key = get_app_key  # type: ignore[assignment]

    assert r.status_code == 503
    assert "app key is missing" in r.json()["detail"]
