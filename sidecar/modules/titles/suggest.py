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
import re
from functools import lru_cache
from pathlib import Path

from sidecar.modules.geo.gazetteer import fold

DATA = Path(__file__).parent / "data"

MIN_QUERY_LEN = 2
_GAPS = re.compile(r"[\s\-]+")
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

    __slots__ = ("titles", "folded", "compact", "known")

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        pairs = sorted(((title, fold(title)) for title, _code in rows), key=_rank)
        self.titles = [p[0] for p in pairs]
        self.folded = [p[1] for p in pairs]
        self.compact = [_compact(f) for f in self.folded]
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


def _compact(folded: str) -> str:
    """Spaces and hyphens out, so "backend" and "back-end" meet "Back End"."""
    return _GAPS.sub("", folded)


def suggest(
    text: str, limit: int = DEFAULT_LIMIT, *, board_titles: list[str] | None = None
) -> list[str]:
    """Titles for what's typed, best first: a title the user's own board carries,
    then a prefix match, a word starting with it, and a plain substring last.
    Spacing is ignored, and nothing typed is ever refused."""
    if len(text) < MIN_QUERY_LEN:
        return []
    index = _index()
    query = fold(text)
    tight = _compact(query)
    board = " | ".join(_compact(fold(t)) for t in board_titles or ())

    tiers: tuple[list[str], ...] = ([], [], [], [])  # on board, prefix, word, substring
    for title, folded, compact in zip(index.titles, index.folded, index.compact, strict=True):
        if folded.startswith(query) or compact.startswith(tight):
            tier = 1
        elif f" {query}" in folded:
            tier = 2
        elif query in folded or tight in compact:
            tier = 3
        else:
            continue
        if board and compact in board:
            tier = 0
        if len(tiers[tier]) < limit:
            tiers[tier].append(title)
        if len(tiers[0]) >= limit or (not board and len(tiers[1]) >= limit):
            break
    return [t for tier in tiers for t in tier][:limit]


def is_known(text: str) -> bool:
    return fold(text) in _index().known
