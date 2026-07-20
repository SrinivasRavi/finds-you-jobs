"""Secrets-at-rest (NFR-SEC-01) — the app key and the sealing primitives.

One symmetric Fernet key per install seals every locally stored secret (BYOK
API keys and the LinkedIn session storage-state). Key resolution
(`get_app_key`): env `FYJ_SESSION_KEY` (tests/dev override) → OS keychain via
`keyring` (service "finds-you-jobs") → an app-managed key file under the data
dir with owner-only permissions (the NFR's stated fallback when no keychain
backend exists). The key is never logged and never passed via argv.

The LinkedIn session-file seal/read/write helpers interoperate with
`referral_outreach/upstream/secure_store.py` (the GPL side) via the shared
sealed-JSON shape `{"fyj_sealed": 1, "token": "<Fernet token>"}` — the two
sides never import each other, only agree on that format and the key env var.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("fyj.sidecar.security")

SESSION_KEY_ENV = "FYJ_SESSION_KEY"  # must match referral_outreach/upstream/secure_store.py
SEALED_MARKER = "fyj_sealed"         # must match referral_outreach/upstream/secure_store.py
KEYRING_SERVICE = "finds-you-jobs"
KEYRING_ACCOUNT = "session-store-key"
KEY_FILE_NAME = "session_store.key"


def _new_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _key_from_keyring() -> str | None:
    """Get-or-create the key in the OS keychain. None when no usable backend
    (headless Linux, locked keychain, …) — callers fall back to the key file."""
    try:
        import keyring
    except ImportError:
        logger.warning("keyring not importable; using the key-file fallback")
        return None
    try:
        existing = keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        if existing:
            return existing
        fresh = _new_key()
        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, fresh)
        # Read-back so a silently-broken backend (writes accepted, reads empty)
        # falls through to the file instead of sealing with an unrecoverable key.
        if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) == fresh:
            return fresh
        logger.warning("keyring backend did not round-trip the key; using the key-file fallback")
        return None
    except Exception as e:  # noqa: BLE001 — any backend failure must not break boot
        logger.warning("keyring failed (%s: %s); using the key-file fallback", type(e).__name__, e)
        return None


def _key_from_file(data_dir: Path) -> str:
    """App-managed key file, owner-only perms (0600) — the NFR-SEC-01 fallback."""
    path = data_dir / KEY_FILE_NAME
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if key:
            return key
    data_dir.mkdir(parents=True, exist_ok=True)
    fresh = _new_key()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(fresh)
    logger.info("created app-managed key at %s (0600)", path)
    return fresh


def get_session_key(data_dir: Path, *, use_keyring: bool = True) -> str:
    """The install's Fernet key. Resolution order: env override → OS keychain →
    app-managed key file (0600)."""
    env = os.environ.get(SESSION_KEY_ENV, "").strip()
    if env:
        return env
    if use_keyring:
        from_keyring = _key_from_keyring()
        if from_keyring:
            return from_keyring
    return _key_from_file(data_dir)


# The same app-managed Fernet key seals BYOK API keys at rest (NFR-SEC-01,
# FR-SET-06) and, later, the LinkedIn session file. One key, all secret kinds
# — the env var / keychain account / key-file are shared deliberately: there is
# a single "app key" per install. `get_app_key` is the intention-revealing name
# for that broader use; it is `get_session_key` unchanged.
get_app_key = get_session_key


def seal_secret(plaintext: str, key: str) -> bytes:
    """Fernet-encrypt a secret (e.g. a BYOK API key) for storage in an opaque
    BLOB. Returns the token bytes — never the plaintext. `key` is a Fernet key
    from `get_app_key`."""
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).encrypt(plaintext.encode())


def open_secret(token: bytes, key: str) -> str:
    """Decrypt a `seal_secret` token back to the plaintext secret."""
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).decrypt(token).decode()


def mask_key(plaintext: str) -> str:
    """A non-secret display hint for a stored key — e.g. `sk-…abc4`. Reveals at
    most the last 4 chars, and only when the key is long enough that those 4 do
    not materially expose it. Short/empty keys mask to `…` entirely. Never store
    or log the plaintext; store this hint in `EngineSettings.key_ref`."""
    plaintext = plaintext.strip()
    if len(plaintext) < 8:
        return "…"
    prefix = plaintext[:3] if plaintext[:3].isascii() and "-" in plaintext[:5] else ""
    return f"{prefix}…{plaintext[-4:]}"


def seal_session_file(path: Path, key: str) -> bool:
    """One-time migration: encrypt a legacy plaintext storage-state file in
    place. Roundtrip-verified before the atomic replace — on ANY doubt the
    original file is left untouched (an intact plaintext session beats a
    destroyed one; the gap is then loud in the logs, not silent).

    Returns True when the file was migrated, False when there was nothing to
    do (missing, already sealed) or the migration could not be verified."""
    if not path.exists():
        return False
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("session file %s unreadable (%s) — not migrating", path, e)
        return False
    if not isinstance(data, dict) or SEALED_MARKER in data:
        return False  # already sealed (or not a state dict)

    from cryptography.fernet import Fernet

    f = Fernet(key.encode())
    token = f.encrypt(raw.encode())
    if json.loads(f.decrypt(token).decode()) != data:  # roundtrip verify
        logger.error("seal roundtrip mismatch for %s — leaving plaintext untouched", path)
        return False
    payload = json.dumps({SEALED_MARKER: 1, "token": token.decode()})
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    logger.info("migrated %s to encrypted-at-rest (NFR-SEC-01)", path)
    return True


def read_session_state(path: Path, key: str) -> tuple[dict, bool]:
    """Load a storage-state file, transparently unsealing a Fernet-sealed one
    (`{"fyj_sealed": 1, "token": …}`). Returns `(state_dict, was_sealed)` — so a
    caller that mutates and re-persists can reseal in the SAME format. Legacy
    plaintext files come back with `was_sealed=False`. Raises on missing/corrupt/
    undecryptable input: callers (the dev fault-injection tool) surface that
    honestly rather than silently no-op'ing on a sealed file."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    was_sealed = bool(isinstance(data, dict) and data.get(SEALED_MARKER))
    if was_sealed:
        from cryptography.fernet import Fernet

        plaintext = Fernet(key.encode()).decrypt(str(data["token"]).encode()).decode()
        state = json.loads(plaintext)
    else:
        state = data
    if not isinstance(state, dict):
        raise ValueError("session state is not a JSON object")
    return state, was_sealed


def write_session_state(path: Path, state: dict, key: str, *, sealed: bool) -> None:
    """Persist a storage-state dict, resealing (same format as `seal_session_file`)
    when `sealed`, else writing plaintext. Atomic replace — the file is never left
    half-written."""
    if sealed:
        from cryptography.fernet import Fernet

        token = Fernet(key.encode()).encrypt(json.dumps(state).encode())
        payload = json.dumps({SEALED_MARKER: 1, "token": token.decode()})
    else:
        payload = json.dumps(state)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def migrate_plaintext_session(data_dir: Path) -> bool:
    """Startup hook: if a pre-encryption plaintext session file exists under
    `<data-dir>/linkedin/`, seal it. Resolves the key ONLY when a file is
    present (so test/temp data dirs never touch the OS keychain)."""
    path = data_dir / "linkedin" / "storage_state.json"
    if not path.exists():
        return False
    return seal_session_file(path, get_session_key(data_dir))
