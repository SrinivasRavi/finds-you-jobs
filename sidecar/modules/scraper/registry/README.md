# Scraper source registry

Generated portals configs for the M3 Scraper, built from a verified list of
company job boards — no live probing done here, the CSVs are the source of
truth.

- **Provenance**: `docs/agent-labs/source-registry/` (`registry_india.csv`,
  `registry_remote.csv`, `REPORT.md`), generated 2026-07-07.
- `portals-india.toml` — one source per India-CSV row (250 companies with
  ≥1 India-located role).
- `portals-remote.toml` — one source per remote-CSV row (182 companies with
  ≥1 India-open/worldwide remote role).
- `portals-all.toml` — union of both, deduped by `url` (315 sources).

Filter/scan blocks in `portals-india.toml` and `portals-all.toml` carry over
from the original G3 gate config (that narrow 12-source config was rejected +
deleted; `gen.py`'s `TITLE_BLOCK`/location blocks are its surviving record):
the maintainer's SWE/backend/forward-deployed-engineer hunt (title allow-list
since expanded to the full SE-alias set), India remote-or-Mumbai/Pune/Bengaluru. `portals-remote.toml` uses the same title
filter with a narrower location `always_allow` (India only — the CSV rows
are already remote/worldwide by construction).

## Running a scan (dogfood CLI)

The scraper is a standalone silo: config in, JSONL out. Sanity-check what
resolves first (no network), then scan.

```
# Dry-run — print the adapter that claims each board, no HTTP
uv run python -m sidecar.modules.scraper \
    --portals sidecar/modules/scraper/registry/portals-all.toml --dry-run

# Full registry scan — all 315 boards (~8 min, ~36k fetched → ~1.3k kept)
uv run python -m sidecar.modules.scraper \
    --portals sidecar/modules/scraper/registry/portals-all.toml \
    --out sidecar/modules/scraper/out/registry-scan_all.jsonl

# India-only (250 boards) or remote-only (182 boards)
uv run python -m sidecar.modules.scraper \
    --portals sidecar/modules/scraper/registry/portals-india.toml \
    --out sidecar/modules/scraper/out/registry-scan_india.jsonl
```

Override the baked-in filters per run (no config edit needed) — each flag is
repeatable and **replaces** the config value:

```
uv run python -m sidecar.modules.scraper \
    --portals sidecar/modules/scraper/registry/portals-all.toml \
    --title-allow "software engineer" --title-allow "backend engineer" \
    --location-allow india --location-allow remote --location-allow bengaluru \
    --location-always-allow india \
    --per-source-cap 50 --max-age-days 30 \
    --out sidecar/modules/scraper/out/my-hunt.jsonl
```

Available flags (see `python -m sidecar.modules.scraper --help`):
`--title-allow` / `--title-block`, `--location-allow` / `--location-block` /
`--location-always-allow`, `--max-age-days`, `--per-source-cap`, `--timeout`.

**Output:** matched rows → JSONL (`--out`, or stdout if omitted). Diagnostics →
stderr: per-source `fetched / kept / http_calls / latency`, verbatim errors, a
`TOTAL` line, and a `USAGE` JSON dump (the `Usage` contract each source hands
back).

## Regenerate

```
uv run python -m sidecar.modules.scraper.registry.gen
```

Reads the two CSVs from `docs/agent-labs/source-registry/` (override with
`--source-dir`) and rewrites the three `portals-*.toml` files in this
directory. See `gen.py`'s module docstring for the `company`-field
convention (why greenhouse rows omit it and lever/ashby rows set it).
