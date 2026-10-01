"""The shipped place index: names in, places out, no network and no model.

Loads the 4 committed GeoNames cuts in `data/` (built by `build_data.py`) into 3
dicts, once per process, behind `gazetteer()`. The load is pure CPU and takes
long enough to matter on the event loop, so the first caller should be a worker
thread; `warm()` exists for a caller that wants to pay the cost deliberately.

Every lookup takes a folded key (`fold()`): lowercase, accents stripped, dashes
normalized. An alias that names 2 places resolves to the larger one and is
listed in `ambiguous`, so a caller can ask the user instead of guessing.
"""

from __future__ import annotations

import gzip
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data"

_DASHES = re.compile(r"[‐-―]")


def fold(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _DASHES.sub("-", folded).lower().strip()


@dataclass(frozen=True, slots=True)
class City:
    geonameid: int
    name: str
    admin1: str
    country: str
    population: int

    @property
    def admin1_key(self) -> str:
        """The `US.CA` form GeoNames keys admin1 divisions by."""
        return f"{self.country}.{self.admin1}"


@dataclass(frozen=True, slots=True)
class Gazetteer:
    cities: dict[int, City]
    alias_to_id: dict[str, int]
    ambiguous: frozenset[str]
    admin1_to_country: dict[str, str]
    admin1_to_key: dict[str, str]
    country_names: dict[str, str]

    def city(self, folded: str) -> City | None:
        city_id = self.alias_to_id.get(folded)
        return self.cities.get(city_id) if city_id is not None else None

    def admin1(self, folded: str) -> str | None:
        """The ISO-2 country a state, province or region name belongs to."""
        return self.admin1_to_country.get(folded)

    def country(self, folded: str) -> str | None:
        return self.country_names.get(folded)


def _rows(name: str) -> list[list[str]]:
    blob = gzip.decompress((DATA / name).read_bytes()).decode("utf-8")
    return [line.split("\t") for line in blob.splitlines() if line]


@lru_cache(maxsize=1)
def gazetteer() -> Gazetteer:
    cities: dict[int, City] = {}
    for row in _rows("cities.tsv.gz"):
        city = City(int(row[0]), row[1], row[2], row[3], int(row[4] or 0))
        cities[city.geonameid] = city

    alias_to_id: dict[str, int] = {}
    ambiguous: set[str] = set()
    for alias, raw_id in _rows("aliases.tsv.gz"):
        city_id = int(raw_id)
        held = alias_to_id.get(alias)
        if held is None:
            alias_to_id[alias] = city_id
            continue
        if held == city_id:
            continue
        ambiguous.add(alias)
        if cities[city_id].population > cities[held].population:
            alias_to_id[alias] = city_id

    admin1_to_country: dict[str, str] = {}
    admin1_to_key: dict[str, str] = {}
    for row in _rows("admin1.tsv.gz"):
        key, country = row[0], row[0].split(".")[0]
        labels = [row[1], row[2]]
        # An alphabetic admin1 suffix IS how postings write the division:
        # "US-WA-Bellevue", "CA-ON". Numeric suffixes (India, Brazil) are codes
        # nobody types, so they stay out of the name index.
        suffix = key.split(".", 1)[1] if "." in key else ""
        if suffix.isalpha():
            labels.append(suffix)
        for label in labels:
            admin1_to_country.setdefault(fold(label), country)
            admin1_to_key.setdefault(fold(label), key)

    country_names: dict[str, str] = {}
    for iso2, iso3, name in _rows("countries.tsv.gz"):
        for label in (iso2, iso3, name):
            country_names.setdefault(fold(label), iso2)

    return Gazetteer(
        cities=cities,
        alias_to_id=alias_to_id,
        ambiguous=frozenset(ambiguous),
        admin1_to_country=admin1_to_country,
        admin1_to_key=admin1_to_key,
        country_names=country_names,
    )


def warm() -> None:
    """Pay the load cost here, off the event loop, before the first resolve."""
    gazetteer()
