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

from .gazetteer import Gazetteer, fold, gazetteer
from .regions import ADMIN1_CODES, METROS, NOISE, REGIONS

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


def _candidates(segment: str) -> list[list[str]]:
    """One group per token in the segment, each group widest spelling first.

    A caller stops at the first spelling that resolves, because a sub-word of a
    name that already resolved is noise: "north america" is a region, while its
    word "north" is a village in Cameroon.
    """
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
    """An ATS location code, where position carries meaning: country, then
    division, then city ("US-CA-Menlo Park", "IN-Karnataka-Bangalore").

    Reading those parts positionally is what stops "US-CA" resolving CA as
    Canada, which it does as a bare name.
    """
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
        country, names = METROS[token]
        for name in names:
            city = g.city(fold(name))
            if city is not None:
                seg.places.append(
                    Place(city.country, city.admin1_key, city.geonameid, city.name)
                )
        seg.countries.add(country)
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
    """Read a city with the 1 or 2 pieces after it (place-picker.md section 4).

    Returns the place and how many pieces were used. A candidate inside a named
    division wins, then one inside a named country; a qualifier naming a place
    that holds no candidate means the city isn't in our cut, so that division
    or country is the place. (None, 0) when nothing after it qualifies."""
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
    remote = remote_global = False
    remote_countries: set[str] = set()
    for chunk in SEGMENT_SPLIT.split(folded):
        if not chunk.strip():
            continue
        seg = _Segment(remote=bool(REMOTE.search(chunk)))
        _resolve_segment(chunk, g, seg)
        places.extend(seg.places)
        ambiguous.extend(seg.ambiguous)
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
    """Resolve the user's preference chips once, into the sets `match` compares.

    A picked chip (a dict from the location picker) is the place itself and is
    never re-guessed; a plain string chip from before the picker is resolved
    the old way (place-picker.md section 3). A city chip wants that city, not
    its country; the country only comes back as a home country, which decides
    whether a region-locked remote posting is reachable."""
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
                country, names = METROS[ident]
                for name in names:
                    found = g.city(fold(name))
                    if found is not None:
                        city_ids.add(found.geonameid)
                home.add(country)
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


def match(resolved: ResolvedLocation, wanted: Wanted) -> tuple[Verdict, str]:
    """The verdict and 1 line saying why, for the breakdown the user reads."""
    if wanted.empty:
        return Verdict.UNVERIFIABLE, "No location preference set, so nothing to compare."

    for place in resolved.places:
        if place.city_id is not None and place.city_id in wanted.city_ids:
            return Verdict.MATCH, f"{place.city} is a location you asked for."
        if place.admin1_key is not None and place.admin1_key in wanted.admin1_keys:
            where = place.city or place.admin1_key
            return Verdict.MATCH, f"{where} is inside a region you asked for."
        if place.country is not None and place.country in wanted.countries:
            return Verdict.MATCH, f"{place.city or place.country} is in a country you asked for."

    if resolved.remote and wanted.remote:
        if resolved.remote_global:
            return Verdict.MATCH, "Remote with no region restriction."
        reachable = wanted.countries | wanted.home_countries
        if resolved.remote_countries:
            shared = resolved.remote_countries & reachable
            if shared:
                return Verdict.MATCH, f"Remote and open to {', '.join(sorted(shared))}."
            scope = ", ".join(sorted(resolved.remote_countries))
            return Verdict.MISS, f"Remote, but only within {scope}."
        return (
            Verdict.UNVERIFIABLE,
            "Says remote without naming a region, so eligibility can't be read from the field.",
        )

    if resolved.places:
        named = ", ".join(p.city or p.country or "" for p in resolved.places if p.city or p.country)
        return Verdict.MISS, f"Located in {named}, which you did not ask for."
    return Verdict.UNVERIFIABLE, "No location could be read from the posting."


def verdicts(
    locations: Iterable[str], wanted: Wanted, g: Gazetteer | None = None
) -> list[tuple[Verdict, str]]:
    """Batch helper: 1 gazetteer load for many postings."""
    g = g or gazetteer()
    return [match(resolve_location(text, g), wanted) for text in locations]
