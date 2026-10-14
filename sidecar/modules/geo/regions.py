"""The 2 tables no gazetteer carries: multi-country regions and metro nicknames.

Editorial and small on purpose: GeoNames doesn't know "EMEA" or "Bay Area". A
region maps to the countries a remote role there is actually offered to; `None`
means every country ("global", "anywhere")."""

from __future__ import annotations

# A remote posting scoped to the key is open to the value's countries.
REGIONS: dict[str, frozenset[str] | None] = {
    "global": None,
    "worldwide": None,
    "anywhere": None,
    "international": None,
    "us": frozenset({"US"}),
    "usa": frozenset({"US"}),
    "u.s.": frozenset({"US"}),
    "u.s.a.": frozenset({"US"}),
    "united states": frozenset({"US"}),
    "america": frozenset({"US"}),
    "canada": frozenset({"CA"}),
    "north america": frozenset({"US", "CA", "MX"}),
    "americas": frozenset({"US", "CA", "MX", "BR", "AR", "CL", "CO"}),
    "latam": frozenset({"BR", "MX", "AR", "CL", "CO", "PE", "UY"}),
    "latin america": frozenset({"BR", "MX", "AR", "CL", "CO", "PE", "UY"}),
    "uk": frozenset({"GB"}),
    "united kingdom": frozenset({"GB"}),
    "europe": frozenset(
        {"DE", "FR", "GB", "IE", "NL", "ES", "IT", "PL", "PT", "SE", "DK", "FI", "NO", "CH", "AT",
         "BE", "CZ", "RO", "GR", "HU", "UA"}
    ),
    "eu": frozenset(
        {"DE", "FR", "IE", "NL", "ES", "IT", "PL", "PT", "SE", "DK", "FI", "AT", "BE", "CZ", "RO",
         "GR", "HU"}
    ),
    "emea": frozenset(
        {"DE", "FR", "GB", "IE", "NL", "ES", "IT", "PL", "PT", "SE", "DK", "FI", "NO", "CH", "AT",
         "BE", "CZ", "RO", "GR", "HU", "UA", "AE", "SA", "IL", "ZA", "EG", "NG", "KE", "TR"}
    ),
    "apac": frozenset({"IN", "SG", "AU", "NZ", "JP", "KR", "CN", "HK", "TW", "MY", "ID", "PH", "TH",
                       "VN"}),
    "asia": frozenset({"IN", "SG", "JP", "KR", "CN", "HK", "TW", "MY", "ID", "PH", "TH", "VN", "AE",
                       "SA", "IL", "TR", "PK", "BD", "LK"}),
    "anz": frozenset({"AU", "NZ"}),
    "mena": frozenset({"AE", "SA", "IL", "EG", "QA", "KW", "BH", "OM", "JO", "MA", "TN", "DZ"}),
    "africa": frozenset({"ZA", "NG", "KE", "EG", "GH", "ET", "MA", "TZ", "UG", "RW"}),
}

# A metro nickname maps to its country and the cities it stands for. Cities are
# canonical GeoNames names, resolved through the gazetteer at match time, so a
# posting in any of them satisfies a preference naming the metro and the other
# way round.
METROS: dict[str, tuple[str, tuple[str, ...]]] = {
    "bay area": ("US", ("San Francisco", "Oakland", "San Jose", "Fremont", "Berkeley",
                        "Palo Alto", "Mountain View", "Sunnyvale", "Santa Clara", "Redwood City",
                        "San Mateo", "Menlo Park", "Cupertino", "Hayward", "Daly City")),
    "sf bay area": ("US", ("San Francisco", "Oakland", "San Jose", "Fremont", "Palo Alto",
                           "Mountain View", "Sunnyvale", "Santa Clara", "Menlo Park")),
    "silicon valley": ("US", ("San Jose", "Palo Alto", "Mountain View", "Sunnyvale",
                              "Santa Clara", "Cupertino", "Menlo Park", "Redwood City")),
    "nyc": ("US", ("New York City", "Brooklyn", "Queens", "Jersey City", "Hoboken")),
    "new york metro": ("US", ("New York City", "Brooklyn", "Queens", "Jersey City", "Newark")),
    "dmv": ("US", ("Washington", "Arlington", "Alexandria", "Bethesda", "Silver Spring")),
    "greater boston": ("US", ("Boston", "Cambridge", "Somerville", "Newton", "Quincy")),
    "greater seattle": ("US", ("Seattle", "Bellevue", "Redmond", "Kirkland", "Tacoma")),
    "socal": ("US", ("Los Angeles", "San Diego", "Irvine", "Pasadena", "Santa Monica")),
    "ncr": ("IN", ("New Delhi", "Delhi", "Gurugram", "Noida", "Faridabad", "Ghaziabad")),
    "delhi ncr": ("IN", ("New Delhi", "Delhi", "Gurugram", "Noida", "Faridabad", "Ghaziabad")),
    "mmr": ("IN", ("Mumbai", "Thane", "Navi Mumbai", "Kalyan")),
    "greater london": ("GB", ("London", "Croydon", "Bromley", "Richmond")),
    "randstad": ("NL", ("Amsterdam", "Rotterdam", "The Hague", "Utrecht")),
}

# A metro defined by its official counties or districts (place-picker.md 10.4):
# every city in them is a member, so a data update adds cities by itself. The Bay
# Area is its 9 counties by FIPS code; a metro absent here keeps its list above.
METRO_DIVISIONS: dict[str, tuple[str, frozenset[str]]] = {
    key: ("US.CA", frozenset({"001", "013", "041", "055", "075", "081", "085", "095", "097"}))
    for key in ("bay area", "sf bay area")
}

# Words a posting puts in its location field that name no place: office labels,
# building names, department suffixes. Dropping them is what lets "Bengaluru-HQ"
# and "Bangalore - Engineering" resolve to Bengaluru.
NOISE: frozenset[str] = frozenset(
    {
        "hq", "headquarters", "mso", "office", "offices", "campus", "building", "site",
        "engineering", "sales", "support", "corporate", "various", "multiple", "other",
        "travel required", "travel-required", "flexible", "tbd", "n/a", "na", "none",
    }
)

# The postal abbreviations for divisions GeoNames keys by number, so the index
# can't learn them from the key the way it learns "US.CA" (place-picker.md
# section 4). Canada's provinces and territories, Australia's states and
# territories; each maps to its GeoNames admin1 key.
ADMIN1_CODES: dict[str, dict[str, str]] = {
    "CA": {
        "ab": "CA.01", "bc": "CA.02", "mb": "CA.03", "nb": "CA.04", "nl": "CA.05",
        "ns": "CA.07", "on": "CA.08", "pe": "CA.09", "qc": "CA.10", "sk": "CA.11",
        "yt": "CA.12", "nt": "CA.13", "nu": "CA.14",
    },
    "AU": {
        "act": "AU.01", "nsw": "AU.02", "nt": "AU.03", "qld": "AU.04", "sa": "AU.05",
        "tas": "AU.06", "vic": "AU.07", "wa": "AU.08",
    },
}
