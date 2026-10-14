"""Cut the shipped title index from O*NET's Alternate Titles file (CC BY 4.0).

Run by hand (`python -m sidecar.modules.titles.build_data`); the output is
committed, so builds need no network. O*NET is sparse against real postings (1 of
93 titles matches exactly; evidence/2026-10-01-location-resolution, "Titles"),
so the index only suggests, never gates."""

from __future__ import annotations

import gzip
import sys
import urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"
TITLES_URL = (
    "https://www.onetcenter.org/dl_files/database/db_30_0_text/Alternate%20Titles.txt"
)

ALLOWED_HOST = "https://www.onetcenter.org/"


def _fetch(url: str) -> bytes:
    if not url.startswith(ALLOWED_HOST):
        raise ValueError(f"refusing to fetch outside {ALLOWED_HOST}: {url}")
    req = urllib.request.Request(  # noqa: S310 - scheme and host checked above
        url, headers={"User-Agent": "finds-you-jobs/titles-build"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - same
        return resp.read()


def build() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    text = _fetch(TITLES_URL).decode("utf-8")

    lines = text.splitlines()
    header = lines[0].split("\t")
    code_col = header.index("O*NET-SOC Code")
    title_col = header.index("Alternate Title")

    seen: set[str] = set()
    rows: list[str] = []
    for line in lines[1:]:
        if not line:
            continue
        fields = line.split("\t")
        code, title = fields[code_col], fields[title_col]
        key = title.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(f"{title}\t{code}")

    blob = ("\n".join(rows) + "\n").encode("utf-8")
    out = DATA / "titles.tsv.gz"
    out.write_bytes(gzip.compress(blob, 9, mtime=0))
    print(
        f"titles.tsv.gz: {len(rows)} rows, {len(blob) / 1e6:.2f} MB raw, "
        f"{out.stat().st_size / 1e6:.2f} MB gzipped"
    )


if __name__ == "__main__":
    sys.exit(build())
