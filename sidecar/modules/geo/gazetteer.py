"""The shipped place index: names in, places out, no network and no model.

Loads the 4 committed GeoNames cuts in `data/` (built by `build_data.py`) into 3
dicts, once per process, behind `gazetteer()`. The load is pure CPU and takes
long enough to matter on the event loop, so the first caller should be a worker
thread; `warm()` exists for a caller that wants to pay the cost deliberately.

Every lookup takes a folded key (`fold()`): lowercase, accents stripped, dashes
normalized. An alias that names several places keeps all of them, biggest first,
and is listed in `ambiguous` (docs/internal/place-picker.md).
"""

from __future__ import annotations

import bisect
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
    admin1_keys: frozenset[str]
    country_names: dict[str, str]
    # Every city of a name, biggest first, and the sorted names for prefix search.
    alias_to_ids: dict[str, tuple[int, ...]]
    sorted_aliases: tuple[str, ...]
    # Display names, and every division a label can mean ("punjab" is 2).
    admin1_name: dict[str, str]
    admin1_keys_named: dict[str, frozenset[str]]
    country_name: dict[str, str]

    def city(self, folded: str) -> City | None:
        city_id = self.alias_to_id.get(folded)
        return self.cities.get(city_id) if city_id is not None else None

    def cities_named(self, folded: str) -> list[City]:
        return [self.cities[i] for i in self.alias_to_ids.get(folded, ())]

    def aliases_starting(self, prefix: str, *, cap: int = 4000) -> list[str]:
        """Aliases beginning with `prefix`, at most `cap`, in sorted order."""
        start = bisect.bisect_left(self.sorted_aliases, prefix)
        out: list[str] = []
        for alias in self.sorted_aliases[start : start + cap]:
            if not alias.startswith(prefix):
                break
            out.append(alias)
        return out

    def has_admin1(self, key: str) -> bool:
        """True when `US.WA` is a real division, which is how an ATS code reads."""
        return key in self.admin1_keys

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

    ids_by_alias: dict[str, set[int]] = {}
    for alias, raw_id in _rows("aliases.tsv.gz"):
        ids_by_alias.setdefault(alias, set()).add(int(raw_id))
    alias_to_ids = {
        alias: tuple(sorted(ids, key=lambda i: -cities[i].population))
        for alias, ids in ids_by_alias.items()
    }
    alias_to_id = {alias: ids[0] for alias, ids in alias_to_ids.items()}
    ambiguous = {alias for alias, ids in alias_to_ids.items() if len(ids) > 1}

    admin1_to_country: dict[str, str] = {}
    admin1_to_key: dict[str, str] = {}
    admin1_keys: set[str] = set()
    admin1_name: dict[str, str] = {}
    keys_named: dict[str, set[str]] = {}
    for row in _rows("admin1.tsv.gz"):
        key, country = row[0], row[0].split(".")[0]
        admin1_name[key] = row[1]
        for label in (row[1], row[2]):
            keys_named.setdefault(fold(label), set()).add(key)
        labels = [row[1], row[2]]
        # An alphabetic admin1 suffix IS how postings write the division:
        # "US-WA-Bellevue", "CA-ON". Numeric suffixes (India, Brazil) are codes
        # nobody types, so they stay out of the name index.
        admin1_keys.add(key)
        suffix = key.split(".", 1)[1] if "." in key else ""
        if suffix.isalpha():
            labels.append(suffix)
        for label in labels:
            admin1_to_country.setdefault(fold(label), country)
            admin1_to_key.setdefault(fold(label), key)

    country_names: dict[str, str] = {}
    country_name: dict[str, str] = {}
    for iso2, iso3, name in _rows("countries.tsv.gz"):
        country_name[iso2] = name
        for label in (iso2, iso3, name):
            country_names.setdefault(fold(label), iso2)

    return Gazetteer(
        cities=cities,
        alias_to_id=alias_to_id,
        ambiguous=frozenset(ambiguous),
        admin1_to_country=admin1_to_country,
        admin1_to_key=admin1_to_key,
        admin1_keys=frozenset(admin1_keys),
        country_names=country_names,
        alias_to_ids=alias_to_ids,
        sorted_aliases=tuple(sorted(alias_to_ids)),
        admin1_name=admin1_name,
        admin1_keys_named={k: frozenset(v) for k, v in keys_named.items()},
        country_name=country_name,
    )


def warm() -> None:
    """Pay the load cost here, off the event loop, before the first resolve."""
    gazetteer()
