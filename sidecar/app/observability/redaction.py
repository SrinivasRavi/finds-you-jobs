"""Secret-redaction net for logs and the flight recorder (defense in depth).

Source discipline keeps secrets out of logs; this `redact()`, plus a Filter and
a Formatter, is the net for a slip. Precision over recall. Caught: Fernet
tokens, `sk-`/`sk-ant-` keys, Apify and Brave keys, Bearer tokens, `token=`
params, `li_at` values, and a high-entropy run of 40+ mixing upper, lower and
digits. Not caught: UUIDs and lowercase-hex hashes (no uppercase), a bare UUID
token outside an auth context, plain prose and URLs."""

from __future__ import annotations

import logging
import re

_MARKER_PREFIX = "«redacted:"  # «redacted:<reason>»
_MARKER_SUFFIX = "»"


def _marker(reason: str) -> str:
    return f"{_MARKER_PREFIX}{reason}{_MARKER_SUFFIX}"


# Each rule replaces a full-match secret with a stable marker, or (when a rule
# has a capturing group) redacts only the captured secret and keeps its
# surrounding context (`li_at=…`, `Bearer …`) so the log line stays readable.
# Ordered specific → general; earlier replacements insert markers whose
# guillemets/colon break later rules, so no rule re-matches a redaction.
_FULL_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Fernet token — the version byte + timestamp make it always start `gAAAAA`.
    (re.compile(r"gAAAAA[0-9A-Za-z_\-]{20,}={0,2}"), "fernet"),
    # Anthropic first (stricter), then the general OpenAI/OpenRouter `sk-` shape.
    (re.compile(r"sk-ant-[0-9A-Za-z_\-]{16,}"), "anthropic-key"),
    (re.compile(r"sk-[0-9A-Za-z_\-]{16,}"), "api-key"),
    (re.compile(r"apify_api_[0-9A-Za-z]{20,}"), "apify-token"),
    (re.compile(r"BSA[0-9A-Za-z_\-]{20,}"), "brave-key"),
)

# Context-preserving rules: (pattern, reason). Group 1 is kept, group 2 redacted.
_GROUP_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Storage-state cookie dict: {"name": "li_at", "value": "<secret>"} —
    # name-then-value ordering, tolerant of whitespace. The closing quote is a
    # lookahead so it is not consumed (keeps the 2-group replacement uniform).
    (
        re.compile(
            r'("name"\s*:\s*"li_at"\s*,\s*"value"\s*:\s*")([^"]{6,})(?=")'
        ),
        "li_at",
    ),
    # Cookie header / pair form: li_at=<secret>
    (re.compile(r"(\bli_at=)([0-9A-Za-z%._\-]{15,})"), "li_at"),
    # Authorization: Bearer <token>  /  bare  Bearer <token>
    (re.compile(r"(?i)(bearer\s+)([0-9A-Za-z._\-]{12,})"), "bearer-token"),
    # token=<value> query-string / form param
    (re.compile(r"(?i)(\btoken=)([0-9A-Za-z._\-]{8,})"), "token-param"),
)

# High-entropy backstop: contiguous secret-alphabet run long enough that it is
# very unlikely to be legitimate log content, further gated in `_redact_entropy`.
_ENTROPY_RE = re.compile(r"[0-9A-Za-z_\-]{40,}")
_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


def _redact_entropy(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        tok = match.group(0)
        if _UUID_RE.match(tok):  # (can't match here — no dashes — but explicit)
            return tok
        has_upper = any(c.isupper() for c in tok)
        has_lower = any(c.islower() for c in tok)
        has_digit = any(c.isdigit() for c in tok)
        # Require all three classes: spares lowercase-hex hashes/UUID-hex and
        # ALL-CAPS or digit-only identifiers, catches base64-ish secrets.
        if has_upper and has_lower and has_digit:
            return _marker("high-entropy")
        return tok

    return _ENTROPY_RE.sub(repl, text)


def redact(text: str) -> str:
    """`text` with every known secret shape replaced by `«redacted:…»`. Total: never
    raises, returns the input unchanged when nothing matches; non-str is coerced."""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    for pattern, reason in _FULL_RULES:
        text = pattern.sub(_marker(reason), text)
    for pattern, reason in _GROUP_RULES:
        marker = _marker(reason)
        text = pattern.sub(lambda m, _mk=marker: f"{m.group(1)}{_mk}", text)
    text = _redact_entropy(text)
    return text


def _safe_redact(text: str) -> str:
    """`redact` wrapped so a logging path can never be broken by a redactor bug."""
    try:
        return redact(text)
    except Exception:  # noqa: BLE001 — logging must survive a broken redactor
        return text


class RedactionFilter(logging.Filter):
    """Scrub secret-shaped substrings from a record's message and args. A filter
    must never raise, so every step is guarded and the record is always kept."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 — stdlib name
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — malformed msg/args: leave untouched
            return True
        redacted = _safe_redact(message)
        if redacted != message:
            # Collapse msg+args into the already-formatted, scrubbed string so
            # the handler's formatter re-renders nothing secret-bearing.
            record.msg = redacted
            record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Scrub the fully rendered line, traceback included, which the formatter
    produces after `RedactionFilter` runs."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            return _safe_redact(super().format(record))
        except Exception:  # noqa: BLE001 — never break the write path
            return super().format(record)


def attach_redaction(handler: logging.Handler) -> None:
    """Install the redaction net on a handler: the filter (message/args) and,
    wrapping any existing formatter's format string, the redacting formatter
    (whole-line incl. traceback). Idempotent — safe to call more than once."""
    if not any(isinstance(f, RedactionFilter) for f in handler.filters):
        handler.addFilter(RedactionFilter())
    existing = handler.formatter
    if not isinstance(existing, RedactingFormatter):
        redacting: RedactingFormatter = _wrap_formatter(existing)
        handler.setFormatter(redacting)


def _wrap_formatter(existing: logging.Formatter | None) -> RedactingFormatter:
    """Rebuild `existing`'s format string as a `RedactingFormatter` (keeps the
    same rendered layout; adds the whole-line scrub)."""
    fmt: str | None = getattr(existing, "_fmt", None) if existing else None
    datefmt: str | None = getattr(existing, "datefmt", None) if existing else None
    return RedactingFormatter(fmt, datefmt=datefmt)


# Re-export for callers that want the primitive without importing the module dunder.
__all__: list[str] = [
    "RedactingFormatter",
    "RedactionFilter",
    "attach_redaction",
    "redact",
]
