# voyager_py/secure_store.py — GPL v3 (see LICENSE).
# SPDX-License-Identifier: GPL-3.0-only
#
# NEW code written for the finds-you-jobs fork (GPL subtree, no upstream source).
# Encrypted-at-rest storage for the LinkedIn storage-state file (NFR-SEC-01:
# "LinkedIn cookies are never stored in plaintext"). The MIT host owns the key
# (OS keychain via `keyring`, app-managed key file fallback) and hands it to
# this subprocess via the FYJ_SESSION_KEY environment variable — never argv
# (argv is visible in `ps`), never stdout/stderr (never logged).
"""Sealed JSON file store for the saved LinkedIn session.

File format when a key is present::

    {"fyj_sealed": 1, "token": "<Fernet token>"}

Readers accept both the sealed format and the legacy plaintext storage-state
JSON (pre-encryption files, and the standalone-CLI-without-key path), so an
existing session is never invalidated by the upgrade. Writers seal whenever
FYJ_SESSION_KEY is set; without a key (standalone CLI dogfood) they write
plaintext and log a warning — the app always sets the key.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from .errors import VoyagerError

logger = logging.getLogger("voyager_py.secure_store")

SESSION_KEY_ENV = "FYJ_SESSION_KEY"
SEALED_MARKER = "fyj_sealed"


class UnreadableStateFile(VoyagerError):
    """The storage-state file exists but cannot be parsed (corrupt/truncated).
    Distinct from a decryption failure so tolerant readers (session-status)
    can treat corrupt-as-absent while a key problem stays loud."""


def _session_key() -> bytes | None:
    key = os.environ.get(SESSION_KEY_ENV, "").strip()
    return key.encode() if key else None


def _fernet(key: bytes):
    from cryptography.fernet import Fernet

    try:
        return Fernet(key)
    except (ValueError, TypeError) as e:
        raise VoyagerError(f"invalid {SESSION_KEY_ENV}: not a valid Fernet key") from e


def load_state_file(path: str | Path) -> dict | None:
    """Read a storage-state file — sealed or legacy plaintext. Returns None when
    the file is missing; raises `VoyagerError` (verbatim, never swallowed) when
    the file is sealed but the key is absent or wrong."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise UnreadableStateFile(f"unreadable storage-state file {p}: {e}") from e
    if not isinstance(data, dict) or SEALED_MARKER not in data:
        return data if isinstance(data, dict) else None  # legacy plaintext
    key = _session_key()
    if key is None:
        raise VoyagerError(
            f"storage-state file {p} is encrypted but {SESSION_KEY_ENV} is not set "
            "— the host must pass the session-store key"
        )
    from cryptography.fernet import InvalidToken

    try:
        raw = _fernet(key).decrypt(str(data.get("token", "")).encode())
    except InvalidToken as e:
        raise VoyagerError(
            f"could not decrypt storage-state file {p}: wrong or rotated "
            f"{SESSION_KEY_ENV}"
        ) from e
    return json.loads(raw.decode())


def save_state_file(path: str | Path, state: dict) -> None:
    """Write a storage-state file atomically (temp + rename, same dir). Sealed
    when FYJ_SESSION_KEY is set; plaintext (with a warning) otherwise."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(state)
    key = _session_key()
    if key is None:
        logger.warning(
            "%s not set — writing PLAINTEXT storage-state to %s (standalone-CLI "
            "path; the app always encrypts)", SESSION_KEY_ENV, p,
        )
        payload = raw
    else:
        token = _fernet(key).encrypt(raw.encode()).decode()
        payload = json.dumps({SEALED_MARKER: 1, "token": token})
    fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=p.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
