"""Cut the shipped gazetteer from the GeoNames dumps (CC BY 4.0).

Run by hand, not at boot: `python -m sidecar.modules.geo.build_data`. The 3 files
it writes are committed, so a build never needs the network. Every figure behind
the field choices is in `docs/internal/evidence/2026-10-01-location-resolution/`.

The whole point is size. The geonamescache package carrying this same data is
181 MB installed; the fields a location matcher actually reads compress to under
1.5 MB, which is small enough to ship inside the binary.
"""

from __future__ import annotations

import gzip
import io
import re
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

DATA = Path(__file__).parent / "data"
CITIES_URL = "https://download.geonames.org/export/dump/cities15000.zip"
ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
COUNTRY_URL = "https://download.geonames.org/export/dump/countryInfo.txt"

# An alias is kept only when it could plausibly appear in a job posting: latin
# script, no punctuation beyond what place names carry, long enough not to
# collide with a word. 195,295 of GeoNames' 11M alternate names survive this.
LATIN = re.compile(r"^[a-z0-9 .'/-]+$")
MIN_ALIAS = 3
MAX_ALIAS = 40
# Transliterations are kept only for cities a posting plausibly names. Dropping
# them below this floor cuts the loaded index by 60% and costs nothing we can
# measure: a 20,000-person town written in a local alias resolves to
# unverifiable, which is the honest answer for it anyway.
ALIAS_POPULATION_FLOOR = 200_000


def fold(value: str) -> str:
    """Lowercase, strip accents, normalize dashes: the one key shape."""
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[‐-―]", "-", folded).lower().strip()


ALLOWED_HOST = "https://download.geonames.org/"


def _fetch(url: str) -> bytes:
    if not url.startswith(ALLOWED_HOST):
        raise ValueError(f"refusing to fetch outside {ALLOWED_HOST}: {url}")
    req = urllib.request.Request(  # noqa: S310 - scheme and host checked above
        url, headers={"User-Agent": "finds-you-jobs/gazetteer-build"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - same
        return resp.read()


def build() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    raw = _fetch(CITIES_URL)
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        text = zf.read("cities15000.txt").decode("utf-8")

    cities: list[str] = []
    aliases: list[str] = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 15:
            continue
        geonameid, name, ascii_name, alt = f[0], f[1], f[2], f[3]
        country, admin1, admin2, population = f[8], f[10], f[11], f[14]
        cities.append(f"{geonameid}\t{name}\t{admin1}\t{country}\t{population}\t{admin2}")
        keys = {fold(name), fold(ascii_name)}
        if int(population or 0) >= ALIAS_POPULATION_FLOOR:
            keys.update(fold(a) for a in alt.split(",") if a)
        for key in sorted(keys):
            if MIN_ALIAS < len(key) < MAX_ALIAS and LATIN.match(key):
                aliases.append(f"{key}\t{geonameid}")

    admin1_rows = [
        line for line in _fetch(ADMIN1_URL).decode("utf-8").splitlines() if line.count("\t") >= 2
    ]
    # countryInfo.txt is tab-separated with a commented header; columns 0, 1 and 4
    # are the 2-letter code, the 3-letter code and the English name.
    countries = []
    for line in _fetch(COUNTRY_URL).decode("utf-8").splitlines():
        if line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) > 4 and f[0]:
            countries.append(f"{f[0]}\t{f[1]}\t{f[4]}")

    for name, rows in (
        ("cities.tsv.gz", cities),
        ("aliases.tsv.gz", aliases),
        ("admin1.tsv.gz", admin1_rows),
        ("countries.tsv.gz", countries),
    ):
        blob = ("\n".join(rows) + "\n").encode("utf-8")
        (DATA / name).write_bytes(gzip.compress(blob, 9, mtime=0))
        print(f"{name}: {len(rows)} rows, {len(blob) / 1e6:.2f} MB raw, "
              f"{(DATA / name).stat().st_size / 1e6:.2f} MB gzipped")


if __name__ == "__main__":
    sys.exit(build())
