"""Secrets-at-rest (NFR-SEC-01) — the app key and the sealing primitives.

One symmetric Fernet key per install seals every locally stored secret (BYOK
API keys and the LinkedIn session storage-state). There is exactly ONE store
per machine: the OS keychain via `keyring` (macOS Keychain, Windows Credential
Manager, Linux Secret Service). The owner-only key file is not a parallel store, it
is what a machine with no working keychain falls back to, and without it a
Linux box lacking a Secret Service could not save an API key at all.

Resolution (`get_app_key`), in order, resolved ONCE per data dir and cached:

1. env `FYJ_SESSION_KEY` — explicit override, read on every call so tests stay
   isolated from each other
2. the OS keychain
3. an existing owner-only key file
4. nothing anywhere: if this install has ever sealed a secret, RAISE
   (`AppKeyUnavailable`); otherwise it is a fresh profile, so mint a key and
   store it

Step 4 is the whole point. Before 2026-08-24 a keychain that THREW and a
keychain that was merely EMPTY both came back as None, so a store that stopped
answering was indistinguishable from a new install and the file path quietly
minted a SECOND key on top of data sealed with the first — every stored API key
silently unreadable, with no error anywhere. An error the user can act on beats
that every time.

Nothing rotates the key, so the cache never needs invalidating; `clear_key_cache`
exists for tests that swap a data dir's backing store underneath it.

Threat-model honesty (F-L2): on the key-FILE fallback the key sits beside the
ciphertext it protects, so an attacker with read access to the data dir gets
both — that path is obfuscation with an owner-only permission bar, not
encryption against them. `os.open` mode bits do not map onto Windows ACLs, so
there the bar is an explicit `icacls` grant instead (`_harden_owner_only`);
the keychain stays primary on every platform regardless.

The LinkedIn session-file seal/read/write helpers interoperate with
`referral_outreach/upstream/secure_store.py` (the GPL side) via the shared
sealed-JSON shape `{"fyj_sealed": 1, "token": "<Fernet token>"}` — the two
sides never import each other, only agree on that format and the key env var.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger("fyj.sidecar.security")

SESSION_KEY_ENV = "FYJ_SESSION_KEY"  # must match referral_outreach/upstream/secure_store.py
SEALED_MARKER = "fyj_sealed"         # must match referral_outreach/upstream/secure_store.py
KEYRING_SERVICE = "finds-you-jobs"
KEYRING_ACCOUNT = "session-store-key"
KEY_FILE_NAME = "session_store.key"


class AppKeyUnavailable(RuntimeError):
    """This install has sealed secrets but its key can no longer be found.

    Raised instead of minting a replacement, because a fresh key would leave
    every sealed secret permanently unreadable while the app carried on looking
    healthy. Recovery is to restore the keychain entry (or the key file), or to
    re-enter the affected secrets."""


def _new_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def _keyring_get() -> tuple[str | None, bool]:
    """`(key, answered)`. `answered` is False when the backend could not be
    consulted at all — no keyring package, no backend, locked, D-Bus down. The
    caller MUST NOT read a False as "no key here"; that conflation is what used
    to mint a second key over live data."""
    try:
        import keyring
    except ImportError:
        logger.warning("keyring not importable; falling back to the key file")
        return None, False
    try:
        return (keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) or None), True
    except Exception as e:  # noqa: BLE001 — any backend failure, uniformly
        logger.warning("keyring unreadable (%s: %s)", type(e).__name__, e)
        return None, False


def _keyring_put(key: str) -> bool:
    """Store the key and read it straight back. False when the backend refused
    or did not round-trip (writes accepted, reads empty), so the caller writes
    the key file instead of sealing with something it can never recover."""
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, key)
        if keyring.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT) == key:
            return True
        logger.warning("keyring did not round-trip the key; using the key file")
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("keyring unwritable (%s: %s); using the key file", type(e).__name__, e)
        return False


def _read_key_file(data_dir: Path) -> str | None:
    """The existing owner-only key file, or None. Never creates one — minting
    is the resolver's decision to make, and only after `_has_sealed_secret`
    says this is genuinely a new profile."""
    try:
        key = (data_dir / KEY_FILE_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return key or None


def _harden_owner_only(path: Path) -> None:
    """Make the key file owner-only on Windows, which has no POSIX mode bits.

    The 0o600 handed to `os.open` there sets only the read-only attribute, so
    the file inherits the data dir's ACL and `stat` reports 0o666 whatever we
    asked for. `icacls` (present in every Windows install) drops that
    inheritance and grants the running account alone. Best effort: the key is
    already on disk by now, so a failure is logged, never fatal."""
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME") or getpass.getuser()
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{user}" if domain else user
    # Absolute path, like the taskkill call in claude_engine.py (S607).
    icacls = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe"
    )
    try:
        proc = subprocess.run(  # noqa: S603
            [icacls, str(path), "/inheritance:r", "/grant:r", f"{account}:F"],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not restrict %s to its owner (%s)", path, exc)
        return
    if proc.returncode != 0:
        logger.warning(
            "could not restrict %s to its owner (icacls exit %d)", path, proc.returncode
        )


def _write_key_file(data_dir: Path, key: str) -> None:
    """App-managed key file, owner-only — the NFR-SEC-01 fallback.

    Honest guarantee (F-L2): this key lives in the SAME data dir as the
    ciphertext it seals, so against an attacker who can read the user's files
    it is obfuscation plus an owner-only permission bar, not real encryption —
    they can read the key exactly as the app does. True at-rest secrecy on
    this path exists only with the OS keychain (or the env override); the file
    fallback keeps secrets out of casual greps/backups, no more.

    Owner-only means 0600 on POSIX and an explicit ACL on Windows, which
    ignores the mode entirely (`_harden_owner_only`)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / KEY_FILE_NAME
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    _harden_owner_only(path)
    logger.info("created app-managed key at %s (owner-only)", path)


def _has_sealed_secret(data_dir: Path) -> bool:
    """Has this install ever sealed anything? Separates a genuinely fresh
    profile (mint a key) from one whose key store stopped answering (raise).

    Read directly with stdlib sqlite3 rather than through the ORM: this runs
    before the app is up, needs one existence check, and importing the db layer
    here would tie the key resolver to schema load order.

    On any doubt this answers False, which minting-side is the LESS bad error:
    a false True refuses to start a working fresh install, while a false False
    costs the user re-entering their API keys. Both are logged."""
    state = data_dir / "linkedin" / "storage_state.json"
    if state.exists():
        try:
            if SEALED_MARKER in json.loads(state.read_text(encoding="utf-8")):
                return True
        except (OSError, ValueError, TypeError):
            pass

    db_path = data_dir / "db.sqlite"
    if not db_path.exists():
        return False
    import sqlite3

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM engine_settings WHERE key_encrypted IS NOT NULL LIMIT 1"
            ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False  # no such table yet — a db created but never migrated
    except sqlite3.Error as e:
        logger.warning("could not check %s for sealed secrets (%s)", db_path, e)
        return False


# Resolved key per data dir. Nothing rotates the app key, so there is no
# invalidation path and every call after the first is a dict lookup — which is
# also what keeps `keyring` (a blocking, occasionally GUI-prompting call) off
# the request path entirely. See `resolve_app_key_once`.
_CACHE: dict[str, str] = {}


def clear_key_cache() -> None:
    """Drop the resolved-key cache. For tests that swap a data dir's backing
    store underneath the resolver; nothing in the app needs it."""
    _CACHE.clear()


def _resolve(data_dir: Path, *, use_keyring: bool) -> str:
    answered = True
    if use_keyring:
        existing, answered = _keyring_get()
        if existing:
            return existing

    from_file = _read_key_file(data_dir)
    if from_file:
        return from_file

    if _has_sealed_secret(data_dir):
        where = "could not be read" if not answered else "holds no key"
        raise AppKeyUnavailable(
            f"this install has encrypted secrets but its app key is missing: the "
            f"OS keychain {where} and there is no key file at "
            f"{data_dir / KEY_FILE_NAME}. Restore the keychain entry, or re-enter "
            f"the affected API keys to seal them with a new one. A replacement key "
            f"was NOT created, because that would have made the existing secrets "
            f"permanently unreadable."
        )

    fresh = _new_key()
    if not (use_keyring and _keyring_put(fresh)):
        _write_key_file(data_dir, fresh)
    return fresh


def get_session_key(data_dir: Path, *, use_keyring: bool = True) -> str:
    """The install's Fernet key. Raises `AppKeyUnavailable` when secrets exist
    but no store can produce the key that opens them."""
    # Read the env override ahead of the cache: tests set and clear it between
    # cases, and a cached value would leak one case's key into the next.
    env = os.environ.get(SESSION_KEY_ENV, "").strip()
    if env:
        return env
    cached = _CACHE.get(str(data_dir))
    if cached is not None:
        return cached
    resolved = _resolve(data_dir, use_keyring=use_keyring)
    _CACHE[str(data_dir)] = resolved
    return resolved


async def resolve_app_key_once(data_dir: Path) -> None:
    """Warm the cache during startup, on a worker thread.

    `keyring` is blocking and on some desktops opens a GUI prompt, so the first
    resolution must never happen on the event loop: the Tauri shell health-polls
    the sidecar on a 2s timeout and kills the process group on a single failure,
    so one prompt left waiting takes the backend down. Every later call is a
    cache hit. A failure here is logged, not raised — the app still starts, and
    the error surfaces on the first request that actually needs a secret."""
    import asyncio

    try:
        await asyncio.to_thread(get_session_key, data_dir)
    except AppKeyUnavailable as e:
        logger.error("%s", e)
    except Exception as e:  # noqa: BLE001 — never block boot on the key store
        logger.warning("could not resolve the app key at startup (%s)", e)


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


def _sealed_envelope(token: bytes) -> str:
    """The sealed-JSON envelope — `{"fyj_sealed": 1, "token": "<Fernet token>"}`.

    Constructed HERE and nowhere else on this side of the license firewall: it
    is the wire contract `referral_outreach/upstream/secure_store.py` (the GPL
    side) reads, and the two sides interoperate only while this shape has a
    single spelling. Takes the already-encrypted token so a caller that
    roundtrip-verifies (`seal_session_file`) writes the exact token it
    verified."""
    return json.dumps({SEALED_MARKER: 1, "token": token.decode()})


def _atomic_write_text(path: Path, payload: str) -> None:
    """Write `payload` to `path` via a tmp file in the SAME dir + `os.replace`,
    so the file is never observed half-written and a failed write leaves no tmp
    behind. `mkstemp` creates the tmp 0600 and `os.replace` carries that mode
    over, so a secret never lands world-readable.

    The host's ONE copy: the GPL subtree keeps its own (`secure_store`/`pacing`)
    — cross-boundary duplication is the license firewall's accepted cost, inside
    one side it is not (D-M4)."""
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
    _atomic_write_text(path, _sealed_envelope(token))
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

        payload = _sealed_envelope(Fernet(key.encode()).encrypt(json.dumps(state).encode()))
    else:
        payload = json.dumps(state)
    _atomic_write_text(path, payload)


def migrate_plaintext_session(data_dir: Path) -> bool:
    """Startup hook: if a pre-encryption plaintext session file exists under
    `<data-dir>/linkedin/`, seal it. Resolves the key ONLY when a file is
    present (so test/temp data dirs never touch the OS keychain)."""
    path = data_dir / "linkedin" / "storage_state.json"
    if not path.exists():
        return False
    return seal_session_file(path, get_session_key(data_dir))
