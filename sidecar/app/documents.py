"""Content-addressed blob store for uploaded documents (FR-TR manual-add).

The resume / cover letter a user attaches to a manually-logged application is
raw binary (PDF/DOCX/…), which the markdown-everywhere Artifact pipeline can't
hold. We store each file once, keyed by the SHA-256 of its bytes: the hash IS
the on-disk filename, so re-uploading identical bytes writes nothing new and the
DB `documents` row (see `db/models.Document`) dedups on the same hash. Single
blob, referenced by any number of `application_documents` links.

**The app layer owns storage** (architecture §4.0 — the DB indexes, the disk
holds bytes). Blobs live at `<data_dir>/documents/<sha256>`. These helpers are
synchronous and CPU/IO-bound (hashing + file write); call sites on the event
loop wrap them in `asyncio.to_thread` (async-first directive).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .db.database import resolve_data_dir

# Accepted upload types (extension → mime), kept small on purpose: the formats a
# resume/cover letter is actually submitted as. The Applier never reads these —
# they are a record of what the user sent, downloadable verbatim.
ALLOWED_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".rtf": "application/rtf",
}

# Hard cap per file — a resume/cover letter is small; this only guards against a
# stray huge upload, never a real document.
MAX_BYTES = 10 * 1024 * 1024  # 10 MiB


class DocumentTooLarge(ValueError):
    """Upload exceeded `MAX_BYTES`."""


class UnsupportedDocumentType(ValueError):
    """Upload's extension is not in `ALLOWED_TYPES`."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def mime_for_filename(filename: str) -> str:
    """The mime for a filename by extension, or a safe binary default."""
    return ALLOWED_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def validate(filename: str, data: bytes) -> None:
    """Reject too-large or unsupported uploads before we hash/store them."""
    if len(data) > MAX_BYTES:
        raise DocumentTooLarge(
            f"file is {len(data)} bytes; limit is {MAX_BYTES} bytes"
        )
    if Path(filename).suffix.lower() not in ALLOWED_TYPES:
        allowed = ", ".join(sorted(ALLOWED_TYPES))
        raise UnsupportedDocumentType(f"{filename!r}: allowed types are {allowed}")


def _documents_dir(data_dir: Path | None = None) -> Path:
    return resolve_data_dir(data_dir) / "documents"


def blob_path(sha256: str, data_dir: Path | None = None) -> Path:
    return _documents_dir(data_dir) / sha256


def store_bytes(data: bytes, data_dir: Path | None = None) -> str:
    """Write `data` to its content-addressed blob (no-op if already present) and
    return its sha256. Dedup happens here (existing blob = skip write) and again
    at the DB row (unique sha256), so identical bytes never duplicate."""
    digest = sha256_hex(data)
    path = blob_path(digest, data_dir)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return digest
