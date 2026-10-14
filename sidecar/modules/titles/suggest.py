"""Suggest real job titles while typing; never refuse one.

Loads the committed O*NET cut in `data/titles.tsv.gz` (built by `build_data.py`)
into a rank-ordered index, once per process, behind `suggest()`. O*NET's taxonomy
is too sparse to gate on: 1 of 93 posting titles on the maintainer's install
matched it exactly, and "backend" names none of its 56,495 titles
(`docs/internal/evidence/2026-10-01-location-resolution/README.md`, "Titles").
So this module only ever suggests: `suggest()` never excludes a reading of what
the user typed, and `is_known()` is advisory, not a gate.
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

from sidecar.modules.geo.gazetteer import fold

DATA = Path(__file__).parent / "data"

MIN_QUERY_LEN = 2
DEFAULT_LIMIT = 8


def _rows() -> list[tuple[str, str]]:
    blob = gzip.decompress((DATA / "titles.tsv.gz").read_bytes()).decode("utf-8")
    out = []
    for line in blob.splitlines():
        if not line:
            continue
        title, code = line.split("\t")
        out.append((title, code))
    return out


class TitleIndex:
    """Titles pre-sorted by display rank (shorter, then alphabetical). A prefix match
    outranks a substring, so the scan stops once it has `limit` prefix hits, which
    keeps a 2-letter query fast."""

    __slots__ = ("titles", "folded", "known")

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        pairs = sorted(((title, fold(title)) for title, _code in rows), key=_rank)
        self.titles = [p[0] for p in pairs]
        self.folded = [p[1] for p in pairs]
        self.known = frozenset(self.folded)


def _rank(pair: tuple[str, str]) -> tuple[int, str]:
    title, _folded = pair
    return (len(title), title.lower())


@lru_cache(maxsize=1)
def _index() -> TitleIndex:
    return TitleIndex(_rows())


def warm() -> None:
    """Pay the load cost here, off the event loop, before the first suggest."""
    _index()


def suggest(text: str, limit: int = DEFAULT_LIMIT) -> list[str]:
    if len(text) < MIN_QUERY_LEN:
        return []
    index = _index()
    query = fold(text)

    prefix: list[str] = []
    substring: list[str] = []
    for title, folded in zip(index.titles, index.folded, strict=True):
        if folded.startswith(query):
            prefix.append(title)
            if len(prefix) >= limit:
                break
        elif query in folded and len(substring) < limit:
            substring.append(title)

    return (prefix + substring)[:limit]


def is_known(text: str) -> bool:
    return fold(text) in _index().known
