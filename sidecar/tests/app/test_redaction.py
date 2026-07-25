"""Covers: the secret-redaction net (defense-in-depth for logs + flight recorder).

Three planes:

1. The pure `redact()` function — every known secret shape is scrubbed, and a
   curated set of legitimate log content (UUID op-ids, hex hashes, prose, URLs)
   is left untouched (precision-biased: false positives corrupt logs).
2. The `RedactionFilter` — scrubs a record's message/args and NEVER raises, even
   on a malformed record.
3. The flight-recorder write path — a real `RotatingFileHandler` wired by
   `setup_flight_recorder` scrubs a secret out of both a message and a traceback
   before it hits disk, without changing the log schema.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from pathlib import Path

from sidecar.app.logging_setup import LOGGER_NAME, setup_flight_recorder
from sidecar.app.observability.redaction import (
    RedactingFormatter,
    RedactionFilter,
    redact,
)
from sidecar.app.security import _new_key, seal_secret

# ---------------------------------------------------------------------------
# 1. The pure redactor — positive cases (every secret shape is scrubbed)
# ---------------------------------------------------------------------------


def test_redacts_real_fernet_token() -> None:
    """Seal a real secret via security.py and assert the token is scrubbed."""
    token = seal_secret("my-byok-api-key", _new_key()).decode()
    assert token.startswith("gAAAAA")
    out = redact(f"persisting sealed key {token} to the store")
    assert token not in out
    assert "«redacted:fernet»" in out


def test_redacts_provider_api_keys() -> None:
    openai = "sk-proj-abcDEF1234567890abcDEF1234567890xyz"
    anthropic = "sk-ant-api03-Abc123Def456Ghi789Jkl012Mno345"
    assert redact(f"key={openai}") == "key=«redacted:api-key»"
    # Anthropic gets its stricter label (matched before the generic sk- rule).
    assert redact(f"key={anthropic}") == "key=«redacted:anthropic-key»"


def test_redacts_scraper_tokens() -> None:
    apify = "apify_api_AbCdEf0123456789AbCdEf0123456789xyzz"
    brave = "BSAabcDEF1234567890abcDEF12345"
    assert "«redacted:apify-token»" in redact(f"apify token {apify}")
    assert apify not in redact(f"apify token {apify}")
    assert "«redacted:brave-key»" in redact(f"brave key {brave}")
    assert brave not in redact(f"brave key {brave}")


def test_redacts_auth_contexts() -> None:
    bearer = redact("Authorization: Bearer eyAbc123.def456.ghi789xyz")
    assert bearer == "Authorization: Bearer «redacted:bearer-token»"
    param = redact("GET /sse?token=aB3xY9zK-mN2pQ7 HTTP/1.1")
    assert param == "GET /sse?token=«redacted:token-param» HTTP/1.1"


def test_redacts_li_at_cookie_both_forms() -> None:
    pair = redact("cookie: li_at=AQEDAReallyLongValue123456789ABC; JSESSIONID=x")
    assert pair == "cookie: li_at=«redacted:li_at»; JSESSIONID=x"
    # Storage-state cookie-dict form — value scrubbed, surrounding JSON intact.
    dict_form = redact(
        '{"name": "li_at", "value": "AQEDAReallyLongSecretValue987", "expires": 1}'
    )
    assert "AQEDAReallyLongSecretValue987" not in dict_form
    assert '"value": "«redacted:li_at»"' in dict_form


def test_high_entropy_catch_all() -> None:
    """A mixed-class base64-ish run ≥40 chars that slipped every known prefix."""
    secret = "Zm9vAAbarBAZqux12CD34ef56GH78ij90KLmnop99RS"  # noqa: S105 — 43ch fixture
    out = redact(f"leaked blob {secret} here")
    assert secret not in out
    assert "«redacted:high-entropy»" in out


# ---------------------------------------------------------------------------
# 1b. The pure redactor — negative cases (legitimate content is preserved)
# ---------------------------------------------------------------------------


def test_does_not_redact_uuids() -> None:
    """Op-ids (canonical UUID) and batch-ids (uuid4().hex) must survive."""
    op_id = str(uuid.uuid4())
    batch_id = uuid.uuid4().hex
    text = f"operation {op_id} batch {batch_id} started"
    assert redact(text) == text


def test_does_not_redact_hashes_prose_or_urls() -> None:
    sha = hashlib.sha256(b"resume").hexdigest()  # 64 lowercase hex
    cases = [
        f"artifact digest {sha} verified",
        "commit a1b2c3d applied to main",
        "the tailorer scored 12 jobs in 3.4s and saved 5",
        "GET https://boards.greenhouse.io/acme/jobs/12345 -> 200",
        "loaded 1024 rows from applications table",
    ]
    for text in cases:
        assert redact(text) == text, f"false positive on: {text}"


def test_redact_is_total_on_edge_inputs() -> None:
    assert redact("") == ""
    assert redact("no secrets here") == "no secrets here"
    # Non-str is coerced, never raises.
    assert redact(12345) == "12345"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. The logging.Filter — scrubs records, never raises
# ---------------------------------------------------------------------------


def _record(msg: str, args: tuple[object, ...] | None = None) -> logging.LogRecord:
    return logging.LogRecord(
        name="fyj.sidecar.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


def test_filter_scrubs_message_with_args() -> None:
    f = RedactionFilter()
    rec = _record("saving key %s for user %s", ("sk-ant-api03-Abc123Def456Ghi789Jkl", 7))
    assert f.filter(rec) is True
    out = rec.getMessage()
    assert "sk-ant-" not in out
    assert "«redacted:anthropic-key»" in out
    assert "user 7" in out  # non-secret arg preserved


def test_filter_leaves_clean_record_untouched() -> None:
    f = RedactionFilter()
    rec = _record("scan complete: %d jobs", (42,))
    assert f.filter(rec) is True
    assert rec.getMessage() == "scan complete: 42 jobs"


def test_filter_never_raises_on_malformed_record() -> None:
    """A record whose args can't be % -merged must not blow up the filter."""
    f = RedactionFilter()
    rec = _record("bad format %d %d", ("only-one-arg",))  # too few args → getMessage raises
    # filter() must swallow it and keep the record.
    assert f.filter(rec) is True


# ---------------------------------------------------------------------------
# 3. The flight-recorder write path — message + traceback scrubbed on disk
# ---------------------------------------------------------------------------


def test_flight_recorder_scrubs_secret_in_message(tmp_path: Path) -> None:
    log_path = setup_flight_recorder(tmp_path)
    logger = logging.getLogger(LOGGER_NAME)
    secret = "sk-proj-SECRET1234567890abcdefGHIJKLMNOP"  # noqa: S105 — test fixture
    try:
        logger.info("engine call with key %s", secret)
        for h in logger.handlers:
            h.flush()
        contents = log_path.read_text(encoding="utf-8")
    finally:
        _detach_file_handlers(logger, log_path)
    assert secret not in contents
    assert "«redacted:api-key»" in contents


def test_flight_recorder_scrubs_secret_in_traceback(tmp_path: Path) -> None:
    log_path = setup_flight_recorder(tmp_path)
    logger = logging.getLogger(LOGGER_NAME)
    secret = "apify_api_TRACEBACK0123456789abcdefGHIJ0000"  # noqa: S105 — test fixture
    try:
        try:
            raise ValueError(f"boom while using {secret}")
        except ValueError:
            logger.exception("operation failed")
        for h in logger.handlers:
            h.flush()
        contents = log_path.read_text(encoding="utf-8")
    finally:
        _detach_file_handlers(logger, log_path)
    assert secret not in contents  # the secret lived only in the traceback text
    assert "«redacted:apify-token»" in contents
    assert "Traceback" in contents  # schema/traceback still present, just scrubbed


def test_flight_recorder_wiring_installs_redaction(tmp_path: Path) -> None:
    """The handler carries both legs of the net (filter + redacting formatter)."""
    log_path = setup_flight_recorder(tmp_path)
    logger = logging.getLogger(LOGGER_NAME)
    try:
        handlers = [
            h
            for h in logger.handlers
            if getattr(h, "baseFilename", None) == str(log_path.resolve())
        ]
        assert handlers, "flight recorder handler not found"
        h = handlers[0]
        assert any(isinstance(flt, RedactionFilter) for flt in h.filters)
        assert isinstance(h.formatter, RedactingFormatter)
    finally:
        _detach_file_handlers(logger, log_path)


def _detach_file_handlers(logger: logging.Logger, log_path: Path) -> None:
    """Remove the tmp_path file handler so the test doesn't leak handlers across
    the module-scoped LOGGER_NAME logger."""
    for h in list(logger.handlers):
        if getattr(h, "baseFilename", None) == str(log_path.resolve()):
            h.close()
            logger.removeHandler(h)
