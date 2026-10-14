"""Read a posting's location field, and say whether the user can take the job.

The same place arrives 6 ways ("Bengaluru-HQ", "IN-Karnataka-Bangalore",
"Bangalore, India - Remote"); this turns the string into places, then into
`MATCH`, `MISS` or `UNVERIFIABLE` against the user's locations. `UNVERIFIABLE`
is the honest answer for a bare "Remote" beside foreign cities
(`docs/internal/evidence/2026-10-01-location-resolution/`). Same-name places, and
why a city is read with the pieces after it: docs/internal/place-picker.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from .gazetteer import City, Gazetteer, fold, gazetteer
from .regions import ADMIN1_CODES, METRO_DIVISIONS, METROS, NOISE, REGIONS

SEGMENT_SPLIT = re.compile(r"[;|]|\bor\b")
TOKEN_SPLIT = re.compile(r"[,/()\[\]•]|\s-\s|\s+-|-\s+|\s{2,}")
REMOTE = re.compile(
    r"\bremote(?:[- ]first|[- ]friendly|[- ]eligible|[- ]ok)?\b|work from home|\bwfh\b"
    r"|fully distributed|\bdistributed\b|\banywhere\b"
)
HYBRID = re.compile(r"\bhybrid\b|\bflex(?:ible)?[- ]office\b")
ONSITE = re.compile(r"\bon[- ]?site\b|\bin[- ]office\b")
# A country prefix in an ATS code, which is how Workday and friends write a
# location: "US-CA-Menlo Park", "IN-Karnataka-Bangalore", "DE-Berlin-Trion Building".
ATS_PREFIX = re.compile(r"^([a-z]{2})-(.+)$")


_METRO_MEMBERS: dict[tuple[int, str], tuple[City, ...]] = {}


def metro_cities(g: Gazetteer, key: str) -> tuple[City, ...]:
    """A metro's member cities, in its own country: every city in its official
    divisions when it has them, else its listed cities (the biggest Santa Clara is
    in Cuba, so a name is looked up inside the metro's country)."""
    if (id(g), key) in _METRO_MEMBERS:
        return _METRO_MEMBERS[(id(g), key)]
    country, names = METROS[key]
    if key in METRO_DIVISIONS:
        admin1_key, codes = METRO_DIVISIONS[key]
        found = tuple(sorted(
            (c for c in g.cities.values() if c.admin1_key == admin1_key and c.admin2 in codes),
            key=lambda c: -c.population,
        ))
    else:
        found = tuple(
            c for c in (
                next((c for c in g.cities_named(fold(n)) if c.country == country), None)
                for n in names
            ) if c is not None
        )
    _METRO_MEMBERS[(id(g), key)] = found
    return found


class Verdict(StrEnum):
    MATCH = "match"
    MISS = "miss"
    UNVERIFIABLE = "unverifiable"


class Attendance(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


@dataclass(frozen=True, slots=True)
class Place:
    country: str | None
    admin1_key: str | None = None
    city_id: int | None = None
    city: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedLocation:
    raw: str
    places: tuple[Place, ...] = ()
    remote: bool = False
    remote_global: bool = False
    remote_countries: frozenset[str] = frozenset()
    attendance: Attendance | None = None
    ambiguous: tuple[str, ...] = ()
    # The metros the text named ("Bay Area"), so a display names the metro, not
    # its first of 83 cities.
    metros: tuple[str, ...] = ()

    @property
    def primary(self) -> Place | None:
        """The 1 place to store beside the job. A field naming several cities has
        no primary, so a caller stores the country they agree on, if they do."""
        if len(self.places) == 1:
            return self.places[0]
        cities = [p for p in self.places if p.city_id is not None]
        if len(cities) == 1:
            return cities[0]
        countries = {p.country for p in self.places if p.country}
        if len(countries) == 1:
            return Place(country=countries.pop())
        return None

    @property
    def countries(self) -> frozenset[str]:
        return frozenset(p.country for p in self.places if p.country)

    @property
    def remote_scope(self) -> str:
        """The storable form: "", "global", "unscoped", or sorted country codes."""
        if not self.remote:
            return ""
        if self.remote_global:
            return "global"
        if self.remote_countries:
            return ",".join(sorted(self.remote_countries))
        return "unscoped"


@dataclass(frozen=True, slots=True)
class Wanted:
    """What the user asked for, resolved once from their preference chips."""

    remote: bool = False
    city_ids: frozenset[int] = frozenset()
    admin1_keys: frozenset[str] = frozenset()
    countries: frozenset[str] = frozenset()
    # Countries the user is in, implied by the cities and divisions they named.
    # A remote posting scoped to one of these is one they can take; a non-remote
    # posting in one is not, unless they named that country outright.
    home_countries: frozenset[str] = frozenset()
    unresolved: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.remote or self.city_ids or self.admin1_keys or self.countries)


@dataclass(slots=True)
class _Segment:
    remote: bool = False
    remote_global: bool = False
    countries: set[str] = field(default_factory=set)
    places: list[Place] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    metros: list[str] = field(default_factory=list)


def _candidates(segment: str) -> list[list[str]]:
    """1 group per token, each widest spelling first. A caller stops at the first
    that resolves: "north america" is a region; "north" is a village in Cameroon."""
    segment = REMOTE.sub(",", HYBRID.sub(",", ONSITE.sub(",", segment)))
    groups: list[list[str]] = []
    for part in TOKEN_SPLIT.split(segment):
        part = part.strip(" -.")
        if not part or part in NOISE:
            continue
        spellings = [part]
        for word in re.split(r"[\s-]+", part):
            word = word.strip(" -.")
            if word and word != part and word not in NOISE:
                spellings.append(word)
        groups.append(spellings)
    return groups


def _resolve_ats(part: str, g: Gazetteer, seg: _Segment) -> bool:
    """An ATS location code read by position: country, division, city ("US-CA-Menlo
    Park"), which stops "US-CA" reading CA as Canada."""
    prefix = ATS_PREFIX.match(part)
    if prefix is None:
        return False
    country = g.country(prefix.group(1))
    if country is None:
        return False
    seg.countries.add(country)
    city: Place | None = None
    admin1_key: str | None = None
    for piece in prefix.group(2).split("-"):
        piece = piece.strip()
        folded = fold(piece)
        if not folded or folded in NOISE:
            continue
        key = f"{country}.{piece.upper()}"
        if g.has_admin1(key):
            admin1_key = key
            continue
        named = g.admin1_to_key.get(folded)
        if named is not None and named.startswith(f"{country}."):
            admin1_key = named
            continue
        found = g.city(folded)
        if found is not None and found.country == country:
            city = Place(found.country, found.admin1_key, found.geonameid, found.name)
    if city is not None:
        seg.places.append(city)
    elif admin1_key is not None:
        seg.places.append(Place(country, admin1_key))
    else:
        seg.places.append(Place(country=country))
    return True


def _resolve_token(token: str, g: Gazetteer, seg: _Segment) -> bool:
    if token in REGIONS:
        covered = REGIONS[token]
        if covered is None:
            seg.remote_global = True
            return True
        seg.countries |= covered
        # A region naming 1 country IS a place ("USA Remote"); a region naming
        # many only scopes the remote offer, so it claims no place.
        if len(covered) == 1:
            seg.places.append(Place(country=next(iter(covered))))
        return True
    if token in METROS:
        for city in metro_cities(g, token):
            seg.places.append(Place(city.country, city.admin1_key, city.geonameid, city.name))
        seg.countries.add(METROS[token][0])
        seg.metros.append(token)
        return True
    country = g.country(token)
    if country is not None:
        seg.countries.add(country)
        seg.places.append(Place(country=country))
        return True
    city = g.city(token)
    if city is not None:
        seg.places.append(Place(city.country, city.admin1_key, city.geonameid, city.name))
        seg.countries.add(city.country)
        if token in g.ambiguous:
            seg.ambiguous.append(token)
        return True
    division = g.admin1(token)
    if division is not None:
        seg.places.append(Place(division, g.admin1_to_key.get(token)))
        seg.countries.add(division)
        return True
    return False


def _qualifiers(token: str, cands: list, g: Gazetteer) -> tuple[set[str], set[str]]:
    """The divisions and countries 1 piece names, for qualifying a city.

    A short alphabetic piece is tried as a division code of each candidate's
    country ("ME" beside Portland is Maine) before it counts as a country."""
    divisions = set(g.admin1_keys_named.get(token, ()))
    for table in ADMIN1_CODES.values():
        if token in table:
            divisions.add(table[token])
    if token.isalpha() and len(token) <= 3:
        for city in cands:
            key = f"{city.country}.{token.upper()}"
            if g.has_admin1(key):
                divisions.add(key)
    countries: set[str] = set()
    country = g.country(token)
    if country is not None:
        countries.add(country)
    region = REGIONS.get(token)
    if region is not None and len(region) == 1:
        countries |= region
    return divisions, countries


def _qualified_city(
    cands: list, following: list[list[str]], g: Gazetteer
) -> tuple[Place | None, int]:
    """Read a city with up to 2 pieces after it (place-picker.md section 4); returns
    the place and pieces used. A candidate in a named division wins, then in a named
    country; a qualifier holding no candidate becomes the place. (None, 0) if none."""
    divisions: set[str] = set()
    countries: set[str] = set()
    used = 0
    for group in following[:2]:
        divs, ctrs = _qualifiers(group[0], cands, g)
        if not divs and not ctrs:
            break
        divisions |= divs
        countries |= ctrs
        used += 1
    if not used:
        return None, 0
    for city in cands:
        if city.admin1_key in divisions:
            return Place(city.country, city.admin1_key, city.geonameid, city.name), used
    for city in cands:
        if city.country in countries and not divisions - {
            k for k in divisions if k.startswith(f"{city.country}.")
        }:
            return Place(city.country, city.admin1_key, city.geonameid, city.name), used
    for city in cands:
        if city.country in countries:
            return Place(city.country, city.admin1_key, city.geonameid, city.name), used
    if len(divisions) == 1:
        key = next(iter(divisions))
        return Place(key.split(".")[0], key), used
    if len(countries) == 1:
        return Place(country=next(iter(countries))), used
    return None, 0


def _is_city_name(token: str, g: Gazetteer) -> bool:
    return token not in REGIONS and token not in METROS and g.country(token) is None


def _metro_qualifiers(country: str, following: list[list[str]], g: Gazetteer) -> int:
    """How many of the pieces after a metro name its own division or country."""
    n = 0
    for spellings in following:
        piece = spellings[0]
        own_division = g.has_admin1(f"{country}.{piece.upper()}") or any(
            k.startswith(f"{country}.") for k in g.admin1_keys_named.get(piece, ())
        )
        if not (own_division or g.country(piece) == country):
            break
        n += 1
    return n


def _resolve_segment(chunk: str, g: Gazetteer, seg: _Segment) -> None:
    """Every piece of 1 segment, each city read with the pieces that follow it."""
    groups = _candidates(chunk)
    i = 0
    while i < len(groups):
        spellings = groups[i]
        whole = spellings[0]
        if _resolve_ats(whole, g, seg):
            i += 1
            continue
        if whole in METROS:
            # "Bay Area, CA": the pieces after a metro that name its own state or
            # country qualify it, as they do a city, and add no place.
            _resolve_token(whole, g, seg)
            i += 1 + _metro_qualifiers(METROS[whole][0], groups[i + 1 :], g)
            continue
        place: Place | None = None
        skip = 0
        if _is_city_name(whole, g):
            cands = g.cities_named(whole)
            if cands:
                place, skip = _qualified_city(cands, groups[i + 1 :], g)
            else:
                # "Toronto ON": the qualifier is the last word of the same piece.
                words = whole.split()
                head = " ".join(words[:-1])
                if len(words) >= 2 and _is_city_name(head, g) and g.cities_named(head):
                    place, _ = _qualified_city(g.cities_named(head), [[words[-1]]], g)
        if place is not None:
            seg.places.append(place)
            if place.country:
                seg.countries.add(place.country)
            i += 1 + skip
            continue
        for spelling in spellings:
            if _resolve_token(spelling, g, seg):
                break
        i += 1


def resolve_location(text: str, g: Gazetteer | None = None) -> ResolvedLocation:
    """Parse 1 posting location field. Never raises, never fetches."""
    g = g or gazetteer()
    raw = text or ""
    folded = fold(raw)
    if not folded:
        return ResolvedLocation(raw=raw)

    attendance: Attendance | None = None
    if ONSITE.search(folded):
        attendance = Attendance.ONSITE
    if HYBRID.search(folded):
        attendance = Attendance.HYBRID

    places: list[Place] = []
    ambiguous: list[str] = []
    metros: list[str] = []
    remote = remote_global = False
    remote_countries: set[str] = set()
    for chunk in SEGMENT_SPLIT.split(folded):
        if not chunk.strip():
            continue
        seg = _Segment(remote=bool(REMOTE.search(chunk)))
        _resolve_segment(chunk, g, seg)
        places.extend(seg.places)
        ambiguous.extend(seg.ambiguous)
        metros.extend(seg.metros)
        if seg.remote:
            remote = True
            remote_global = remote_global or seg.remote_global
            remote_countries |= seg.countries
        elif seg.remote_global:
            # "Global" without a remote word still scopes a remote sibling segment.
            remote_global = True

    if remote and attendance is None:
        attendance = Attendance.REMOTE

    deduped: list[Place] = []
    for place in places:
        if place not in deduped:
            deduped.append(place)
    return ResolvedLocation(
        raw=raw,
        places=tuple(deduped),
        remote=remote,
        remote_global=remote_global,
        remote_countries=frozenset(remote_countries),
        attendance=attendance,
        ambiguous=tuple(dict.fromkeys(ambiguous)),
        metros=tuple(dict.fromkeys(metros)),
    )


def chip_name(chip: object) -> str:
    """The short word a chip stands for: what discovery's word filter matches."""
    if isinstance(chip, dict):
        return str(chip.get("name") or chip.get("label") or "")
    return str(chip)


def chip_label(chip: object) -> str:
    """The full name a person reads, and what a search query and a prompt get."""
    if isinstance(chip, dict):
        return str(chip.get("label") or chip.get("name") or "")
    return str(chip)


def resolve_wanted(chips: Sequence[object], g: Gazetteer | None = None) -> Wanted:
    """Resolve the user's chips once into the sets `match` compares. A picked chip is
    the place itself; an old string chip resolves the old way (place-picker.md
    section 3). A city chip wants the city; its country only counts as home."""
    g = g or gazetteer()
    remote = False
    city_ids: set[int] = set()
    admin1_keys: set[str] = set()
    countries: set[str] = set()
    home: set[str] = set()
    unresolved: list[str] = []
    for raw in chips:
        if isinstance(raw, dict):
            level, ident = str(raw.get("level") or ""), str(raw.get("id") or "")
            if level == "remote":
                remote = True
                continue
            if level == "city" and ident.isdigit() and int(ident) in g.cities:
                city = g.cities[int(ident)]
                city_ids.add(city.geonameid)
                home.add(city.country)
                continue
            if level == "admin1" and g.has_admin1(ident):
                admin1_keys.add(ident)
                home.add(ident.split(".")[0])
                continue
            if level == "country" and ident in g.country_name:
                countries.add(ident)
                home.add(ident)
                continue
            if level == "metro" and ident in METROS:
                city_ids.update(c.geonameid for c in metro_cities(g, ident))
                home.add(METROS[ident][0])
                continue
            if level == "region" and ident in REGIONS:
                covered = REGIONS[ident]
                if covered is None:
                    remote = True
                else:
                    countries |= covered
                continue
        chip = chip_name(raw)
        folded = fold(chip)
        if not folded:
            continue
        bare_remote = bool(REMOTE.fullmatch(folded)) or folded in {"remote", "anywhere"}
        if REMOTE.search(folded):
            remote = True
            if bare_remote:
                continue
        seg = _Segment()
        _resolve_segment(folded, g, seg)
        if seg.places:
            for place in seg.places:
                if place.city_id is not None:
                    city_ids.add(place.city_id)
                elif place.admin1_key is not None:
                    admin1_keys.add(place.admin1_key)
                elif place.country is not None:
                    countries.add(place.country)
                if place.country is not None:
                    home.add(place.country)
        elif seg.countries:
            countries |= seg.countries
            home |= seg.countries
        elif not REMOTE.search(folded):
            unresolved.append(chip)
    return Wanted(
        remote=remote,
        city_ids=frozenset(city_ids),
        admin1_keys=frozenset(admin1_keys),
        countries=frozenset(countries),
        home_countries=frozenset(home),
        unresolved=tuple(unresolved),
    )


# The line saying why, by key, so a screen can word it in the user's language
# (audit section 5); `match` returns the English.
LOCATION_REASONS: dict[str, str] = {
    "loc.no_prefs": "No location preference set, so nothing to compare.",
    "loc.city": "{city} is a location you asked for.",
    "loc.region": "{where} is inside a region you asked for.",
    "loc.country": "{where} is in a country you asked for.",
    "loc.remote_any": "Remote with no region restriction.",
    "loc.remote_open": "Remote and open to {countries}.",
    "loc.remote_only": "Remote, but only within {scope}.",
    "loc.remote_unscoped": (
        "Says remote without naming a region, so eligibility can't be read from the field."
    ),
    "loc.elsewhere": "Located in {named}, which you did not ask for.",
    "loc.unreadable": "No location could be read from the posting.",
}


def match_reason(
    resolved: ResolvedLocation, wanted: Wanted
) -> tuple[Verdict, str, dict[str, str]]:
    """The verdict, and the key and facts of the line saying why."""
    if wanted.empty:
        return Verdict.UNVERIFIABLE, "loc.no_prefs", {}

    for place in resolved.places:
        if place.city_id is not None and place.city_id in wanted.city_ids:
            return Verdict.MATCH, "loc.city", {"city": place.city or ""}
        if place.admin1_key is not None and place.admin1_key in wanted.admin1_keys:
            return Verdict.MATCH, "loc.region", {"where": place.city or place.admin1_key}
        if place.country is not None and place.country in wanted.countries:
            return Verdict.MATCH, "loc.country", {"where": place.city or place.country}

    if resolved.remote and wanted.remote:
        if resolved.remote_global:
            return Verdict.MATCH, "loc.remote_any", {}
        reachable = wanted.countries | wanted.home_countries
        if resolved.remote_countries:
            shared = resolved.remote_countries & reachable
            if shared:
                return Verdict.MATCH, "loc.remote_open", {"countries": ", ".join(sorted(shared))}
            scope = ", ".join(sorted(resolved.remote_countries))
            return Verdict.MISS, "loc.remote_only", {"scope": scope}
        return Verdict.UNVERIFIABLE, "loc.remote_unscoped", {}

    if resolved.places:
        named = ", ".join(p.city or p.country or "" for p in resolved.places if p.city or p.country)
        return Verdict.MISS, "loc.elsewhere", {"named": named}
    return Verdict.UNVERIFIABLE, "loc.unreadable", {}


def match(resolved: ResolvedLocation, wanted: Wanted) -> tuple[Verdict, str]:
    """The verdict and 1 line saying why, for the breakdown the user reads."""
    verdict, key, args = match_reason(resolved, wanted)
    return verdict, LOCATION_REASONS[key].format(**args)


def verdicts(
    locations: Iterable[str], wanted: Wanted, g: Gazetteer | None = None
) -> list[tuple[Verdict, str]]:
    """Batch helper: 1 gazetteer load for many postings."""
    g = g or gazetteer()
    return [match(resolve_location(text, g), wanted) for text in locations]
