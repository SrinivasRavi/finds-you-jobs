# GeoNames, CC BY 4.0

The shipped gazetteer at `sidecar/modules/geo/data/` is a cut of the GeoNames
geographical database, used under the Creative Commons Attribution 4.0 International
license, whose full text sits beside this file as `LICENSE-CC-BY-4.0.txt`.

- **Source:** <https://download.geonames.org/export/dump/>
- **Files read:** `cities15000.zip` (`cities15000.txt`), `admin1CodesASCII.txt`,
  `countryInfo.txt`
- **Dump date:** 2026-10-01
- **Attribution required by the license:** "This product includes data created by
  GeoNames (https://www.geonames.org), used under CC BY 4.0." The app carries that line
  in its about surface and in `THIRD_PARTY_NOTICES.md`.

## What was changed, which CC BY 4.0 requires stating

The cut is a reduction, never an addition. `sidecar/modules/geo/build_data.py` is the
only thing that writes those files, and it is the record of every change:

- **Kept, per city:** geonameid, name, admin1 code, country code, population. Dropped:
  latitude, longitude, feature class and code, cc2, admin2 to admin4, elevation, dem,
  timezone, modification date.
- **Kept, per alternate name:** the latin-script spellings of cities with at least
  200,000 people, folded to lowercase with accents stripped and dashes normalized.
  Dropped: every non-latin script, and the alternate names of smaller cities.
- **Kept, per admin1 division:** the GeoNames key, the name and the ascii name.
- **Kept, per country:** the 2-letter code, the 3-letter code and the English name.
- **Format:** tab-separated text, gzipped. No values were edited, corrected or invented.

Regenerate with `python -m sidecar.modules.geo.build_data`, which downloads the current
dumps and rewrites all 4 files. Re-run it and the dump date above changes with it.
