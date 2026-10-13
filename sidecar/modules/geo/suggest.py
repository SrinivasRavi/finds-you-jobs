"""What the location picker offers as the user types (place-picker.md section 3).

Every suggestion names its place in full ("London, Ontario, Canada"), so the
user picks the place itself and the scorer never has to guess which London.
Answered from the shipped gazetteer in memory: no network, no model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .gazetteer import Gazetteer, fold, gazetteer
from .regions import METROS, REGIONS

LIMIT = 8
# A suggestion ranks by population: a city's own, and for a division or a
# country the total of its cities in our cut, so California beats Calabarzon and
# Berlin beats Bermuda. An exact match counts 10 times (place-picker.md section 3).
_EXACT = 10.0
_REGION, _METRO, _REMOTE = 1e8, 2e6, 3e10
# Regions that only restate 1 country, which the country suggestion covers,
# and the global words, which "Remote" covers.
_SKIP_REGIONS = frozenset(
    {"us", "usa", "u.s.", "u.s.a.", "united states", "america", "canada", "uk",
     "united kingdom", "global", "worldwide", "anywhere", "international"}
)
_DISPLAY = {
    "emea": "EMEA", "apac": "APAC", "latam": "LATAM", "anz": "ANZ", "mena": "MENA", "eu": "EU",
    "nyc": "NYC", "dmv": "DMV", "ncr": "NCR", "mmr": "MMR", "socal": "SoCal",
    "sf bay area": "SF Bay Area", "delhi ncr": "Delhi NCR",
}
_SPACES = re.compile(r"[\s,]+")


@dataclass(frozen=True, slots=True)
class Suggestion:
    label: str
    name: str
    level: str
    id: str
    exact: bool
    weight: float

    def as_chip(self) -> dict[str, str]:
        """What a location chip stores (place-picker.md section 3)."""
        return {"label": self.label, "name": self.name, "level": self.level, "id": self.id}


_TOTALS: dict[int, tuple[dict[str, float], dict[str, float]]] = {}


def _totals(g: Gazetteer) -> tuple[dict[str, float], dict[str, float]]:
    """Total city population per division and per country, once per gazetteer."""
    if id(g) in _TOTALS:
        return _TOTALS[id(g)]
    divisions: dict[str, float] = {}
    countries: dict[str, float] = {}
    for city in g.cities.values():
        divisions[city.admin1_key] = divisions.get(city.admin1_key, 0.0) + city.population
        countries[city.country] = countries.get(city.country, 0.0) + city.population
    _TOTALS[id(g)] = (divisions, countries)
    return divisions, countries


def _display(key: str) -> str:
    return _DISPLAY.get(key) or key.title()


def city_label(g: Gazetteer, geonameid: int) -> str:
    city = g.cities[geonameid]
    division = g.admin1_name.get(city.admin1_key, "")
    parts = [city.name, division, g.country_name.get(city.country, "")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def place_label(g: Gazetteer, place: Any) -> str:
    """1 resolved place, named the way the picker names it: "City, Division,
    Country", "Division, Country" or "Country"; "" when nothing was resolved."""
    if place.city_id is not None and place.city_id in g.cities:
        return city_label(g, place.city_id)
    country = g.country_name.get(place.country or "", "")
    if place.admin1_key:
        return ", ".join(p for p in (g.admin1_name.get(place.admin1_key, ""), country) if p)
    return country


def suggest(query: str, limit: int = LIMIT, g: Gazetteer | None = None) -> list[Suggestion]:
    q = _SPACES.sub(" ", fold(query)).strip()
    if len(q) < 2:
        return []
    g = g or gazetteer()
    division_pop, country_pop = _totals(g)
    found: list[Suggestion] = []

    if "remote".startswith(q):
        found.append(Suggestion("Remote", "Remote", "remote", "remote", q == "remote", _REMOTE))
    for key in REGIONS:
        if key not in _SKIP_REGIONS and key.startswith(q):
            name = _display(key)
            found.append(Suggestion(name, name, "region", key, key == q, _REGION))
    for key, (country, _cities) in METROS.items():
        if key.startswith(q):
            name = _display(key)
            label = f"{name}, {g.country_name.get(country, country)}"
            found.append(Suggestion(label, name, "metro", key, key == q, _METRO))
    for label_key, iso2 in g.country_names.items():
        if label_key.startswith(q) and (len(label_key) > 3 or label_key == q):
            name = g.country_name.get(iso2, iso2)
            # A country always outweighs a mid-size city of the same letters.
            weight = max(country_pop.get(iso2, 0.0), 1e6)
            found.append(Suggestion(name, name, "country", iso2, label_key == q, weight))
    for label_key, keys in g.admin1_keys_named.items():
        if label_key.startswith(q):
            for key in keys:
                name = g.admin1_name.get(key, label_key)
                country = g.country_name.get(key.split(".")[0], "")
                label = f"{name}, {country}" if country else name
                weight = division_pop.get(key, 0.0)
                found.append(Suggestion(label, name, "admin1", key, label_key == q, weight))

    seen_cities: set[int] = set()
    for alias in g.aliases_starting(q):
        for city_id in g.alias_to_ids[alias]:
            if city_id in seen_cities:
                continue
            city = g.cities[city_id]
            # A prefix match through an alternate name must look like one a
            # user types: 1 word ("bangalore" finds Bengaluru, "London Borough
            # of Brent" doesn't make Brent a London) and 4+ letters in ("can"
            # doesn't reach Guangzhou through "Canton").
            if alias != q and not fold(city.name).startswith(q) and (
                " " in alias or len(q) < 4
            ):
                continue
            seen_cities.add(city_id)
            found.append(
                Suggestion(
                    city_label(g, city_id), city.name, "city", str(city_id),
                    alias == q, float(city.population),
                )
            )
    if not seen_cities and " " in q:
        found.extend(_city_with_qualifier(q, g))

    found.sort(key=lambda s: (-(s.weight * (_EXACT if s.exact else 1.0)), s.label))
    out: list[Suggestion] = []
    labels: set[str] = set()
    for s in found:
        if s.label not in labels:
            labels.add(s.label)
            out.append(s)
        if len(out) == limit:
            break
    return out


def _city_with_qualifier(q: str, g: Gazetteer) -> list[Suggestion]:
    """ "london ontario": a city name, then words its division or country starts with."""
    words = q.split()
    for cut in range(len(words) - 1, 0, -1):
        head, rest = " ".join(words[:cut]), " ".join(words[cut:])
        cands = g.cities_named(head)
        if not cands:
            continue
        hits = []
        for city in cands:
            division = fold(g.admin1_name.get(city.admin1_key, ""))
            country = fold(g.country_name.get(city.country, ""))
            if division.startswith(rest) or country.startswith(rest):
                hits.append(
                    Suggestion(
                        city_label(g, city.geonameid), city.name, "city",
                        str(city.geonameid), True, float(city.population),
                    )
                )
        if hits:
            return hits
    return []
