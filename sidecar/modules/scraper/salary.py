"""Salary-string parsing — the prerequisite the salary filter needed.

Adapters carry `NormalizedJob.salary` as free text verbatim from the source
("$120k–$150k", "€60,000", "₹12L–₹18L per annum", "$50/hr"). A min/max
filter is only meaningful over a parsed range, so this module turns that
text into an annualized (amount_min, amount_max, currency) triple — and
returns None the moment it isn't confident (no number, ambiguous units).
The filter treats None as "no signal → pass" (rank-don't-gate), so an
unparsed string can never cost the user a real lead.

Zero deps, zero LLM — same contract as the rest of the module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Symbol/code → ISO currency. Symbol-ambiguous cases ($ = USD/CAD/AUD/SGD…)
# resolve to the dominant job-posting usage; an explicit code in the text
# always wins over a symbol.
_CURRENCY_CODES = {"USD", "EUR", "GBP", "INR", "CAD", "AUD", "SGD", "CHF", "JPY"}
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}

# Annualization factors — hours/weeks per year use the conventional US
# full-time figures (2080 h, 52 wk); they gate comparisons, not payroll.
_PERIOD_FACTORS = [
    (re.compile(r"(?:/|\bper\s+)h(?:ou)?r\b|\bhourly\b", re.IGNORECASE), 2080),
    (re.compile(r"(?:/|\bper\s+)day\b|\bdaily\b", re.IGNORECASE), 260),
    (re.compile(r"(?:/|\bper\s+)w(?:ee)?k\b|\bweekly\b", re.IGNORECASE), 52),
    (re.compile(r"(?:/|\bper\s+)mo(?:nth)?\b|\bmonthly\b", re.IGNORECASE), 12),
    (
        re.compile(
            r"(?:/|\bper\s+)y(?:ea)?r\b|\bper\s+annum\b|\bannuall?y\b|\bp\.?a\.?\b",
            re.IGNORECASE,
        ),
        1,
    ),
]

# One amount: digits (with , or . separators) plus an optional scale suffix.
# `L`/`lakh`/`crore` are Indian-market scales (₹12L = 1,200,000).
_AMOUNT = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(k|m|l|lakhs?|lacs?|crores?|cr)?\b",
    re.IGNORECASE,
)
_SCALES = {
    "k": 1_000,
    "m": 1_000_000,
    "l": 100_000,
    "lakh": 100_000,
    "lakhs": 100_000,
    "lac": 100_000,
    "lacs": 100_000,
    "crore": 10_000_000,
    "crores": 10_000_000,
    "cr": 10_000_000,
}


@dataclass
class SalaryRange:
    """Annualized parse of one salary string. A single stated value carries
    the same number in both bounds."""

    amount_min: float
    amount_max: float
    currency: str = ""  # ISO code, or "" when the text never said


def parse_salary(text: str) -> SalaryRange | None:
    """Best-effort parse; None whenever the text carries no usable number.

    Confidence rules: small unscaled numbers are noise ("5 days", "40 hrs"),
    not pay — skipped. Multiple amounts collapse to the widest range
    (min..max of everything parsed): wide is the conservative direction for
    a pass/fail gate — it can only make the filter pass more, never silently
    drop a lead over a mis-read bonus figure.
    """
    if not text.strip():
        return None

    currency = ""
    code = re.search(r"\b(" + "|".join(_CURRENCY_CODES) + r")\b", text, re.IGNORECASE)
    if code:
        currency = code.group(1).upper()
    else:
        for sym, iso in _CURRENCY_SYMBOLS.items():
            if sym in text:
                currency = iso
                break

    factor = 1
    for pattern, per_year in _PERIOD_FACTORS:
        if pattern.search(text):
            factor = per_year
            break

    candidates: list[tuple[float, str, int, int]] = []  # (value, scale, start, end)
    for m in _AMOUNT.finditer(text):
        raw, scale = m.group(1), (m.group(2) or "").lower()
        if raw in ("401", "403") and scale == "k":
            continue  # retirement-plan mentions ("401k match"), not pay
        candidates.append((float(raw.replace(",", "")), scale, m.start(), m.end()))

    # Range shorthand shares its scale ("120–150k", "12 to 18 lakhs"): a
    # scale-less low bound joined to a scaled high bound by a bare range
    # separator borrows the high bound's scale.
    if len(candidates) >= 2:
        v1, s1, a1, b1 = candidates[0]
        _, s2, a2, _ = candidates[1]
        if not s1 and s2 and re.fullmatch(r"\s*(?:-|–|—|to)[\s$€£₹¥]*", text[b1:a2], re.IGNORECASE):
            candidates[0] = (v1, s2, a1, b1)

    amounts: list[float] = []
    for value, scale, _, _ in candidates:
        value *= _SCALES.get(scale, 1)
        # Unscaled small numbers are usually years/hours/counts, not pay —
        # but "50/hr" and "12 lakh" are real; the scale or period disambiguates.
        if value < 10:
            continue
        if value < 1000 and factor == 1 and not scale:
            continue
        amounts.append(value * factor)

    if not amounts:
        return None
    return SalaryRange(min(amounts), max(amounts), currency)
