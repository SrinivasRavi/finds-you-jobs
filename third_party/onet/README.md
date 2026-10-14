# O*NET, CC BY 4.0

The title-suggestion index at `sidecar/modules/titles/data/titles.tsv.gz` is a cut of
O*NET's Alternate Titles file, used under the Creative Commons Attribution 4.0
International license, whose full text sits beside this file.

- **Source:** <https://www.onetcenter.org/dl_files/database/db_30_0_text/Alternate%20Titles.txt>
- **Version:** O*NET database release 30.0, downloaded 2026-10-01
- **Shape as downloaded:** tab-separated with a header row (`O*NET-SOC Code`,
  `Alternate Title`, `Short Title`, `Source(s)`), 56,495 data rows
- **Attribution required by the license, and carried in the app's About surface:**
  "This product includes information from O*NET OnLine by the U.S. Department of Labor,
  Employment and Training Administration (USDOL/ETA), used under CC BY 4.0. O*NET is a
  trademark of USDOL/ETA."

## What was changed, which CC BY 4.0 requires stating

Column selection and deduplication only. `sidecar/modules/titles/build_data.py` is the
executable record.

- **Kept:** the `Alternate Title` and `O*NET-SOC Code` columns.
- **Dropped:** the `Short Title` and `Source(s)` columns, and 10,408 titles that are
  case-insensitive duplicates of an earlier row. 46,087 titles remain.
- **Format:** tab-separated text, gzipped.
- **No title string was edited.** Every kept title is byte-identical to its upstream
  spelling, and nothing was added.

## What this data is not used for

Gating. The measured reason is in
`docs/internal/evidence/2026-10-01-location-resolution/README.md`: 1 of 93 real posting
titles on the maintainer's install matches an O*NET title exactly, and the word "backend"
appears in none of the 56,495. A picker that accepted only these titles would refuse
"Backend Engineer", so the index suggests and the user's own text always wins.

Regenerate with `python -m sidecar.modules.titles.build_data`.
