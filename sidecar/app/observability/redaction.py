"""Secret-redaction net for logs + the flight recorder (defense-in-depth).

The codebase keeps every user secret (Fernet-sealed BYOK keys, the Apify/Brave
tokens, the LinkedIn `li_at` cookie and the sealed-session token) out of log
lines, op snapshots and span attributes by **source discipline** — the writing
code is careful never to place a secret into a message. This module is the
safety net for the day some future code path slips one in: a pure `redact()`
that scrubs known secret shapes out of an arbitrary string, plus a
`logging.Filter` and a `logging.Formatter` that run it on the flight recorder.

It is a *net, not a license to log secrets* — source discipline stays the
primary control. Precision is favoured over recall on purpose: a redactor that
mangled normal log content (op ids, UUIDs, hashes, prose, URLs) would be worse
than useless, so we match specific known prefixes and only fall back to a
tightly-constrained high-entropy rule.

What IS caught
--------------
- Fernet tokens (`seal_secret` output / the sealed-session `token`): the
  base64url `gAAAAA…` shape Fernet always emits.
- Provider API keys: OpenAI / OpenRouter `sk-…`, Anthropic `sk-ant-…`
  (a stricter label, matched first).
- Apify tokens: `apify_api_…`.
- Brave Search keys: `BSA…`.
- `Authorization: Bearer <token>` (and bare `Bearer <token>`).
- `token=<value>` query-string / form params.
- The LinkedIn `li_at` cookie value — both the `li_at=<value>` header pair form
  and the storage-state `{"name": "li_at", "value": "<value>"}` cookie-dict form.
- A conservative high-entropy catch-all for `[A-Za-z0-9_-]` runs of length ≥ 40
  that mix upper, lower AND digit classes and are not UUID-shaped — this rescues
  a base64-ish secret that slipped the known prefixes.

What is deliberately NOT caught (to avoid corrupting normal logs)
-----------------------------------------------------------------
- UUIDs (canonical dashed form, and the 32-char lowercase-hex `uuid4().hex`):
  these are our operation ids / batch ids and are pure lowercase hex, so the
  catch-all's "must contain an uppercase letter" clause skips them.
- Lowercase-hex hashes (sha256 digests, git shas, short hex): pure lowercase hex
  → no uppercase → skipped by the catch-all.
- A bare UUID that happens to be the loopback API bearer token, when it appears
  WITHOUT an `Authorization:`/`Bearer`/`token=` context — indistinguishable from
  an op-id UUID by shape alone, so we rely on the auth-context rules (which do
  catch it) rather than nuking every UUID in the logs.
- Ordinary prose and plain URLs without embedded credentials.
"""

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
    """Return `text` with every known secret shape replaced by `«redacted:…»`.

    Pure and total: never raises for a `str` input, returns the input unchanged
    when nothing matches. Non-`str` input is coerced with `str()` first so the
    filter can hand us whatever a log record carried.
    """
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
    """Scrub secret-shaped substrings from a record's message (and its args).

    Attached to a handler so it also sees records propagated from child loggers.
    A `logging.Filter` must never raise — a throwing filter silently breaks the
    whole logging call — so every step is wrapped and the record is always kept
    (`return True`), modified only when redaction actually changed something.
    """

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
    """A formatter that scrubs the FULLY rendered line — message AND traceback.

    The `RedactionFilter` cleans `record.msg`/`args`, but an exception's
    traceback text is produced by the formatter (`formatException`), so the
    flight-recorder handler wraps its formatter in this to catch a secret that
    surfaced inside a traceback frame. Schema/format string are unchanged; only
    the final string is passed through `redact`.
    """

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
