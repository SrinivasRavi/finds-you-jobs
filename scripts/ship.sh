#!/usr/bin/env bash
#
# ship.sh — one-command release approval for finds-you-jobs.
# Running this script IS your approval for the actions it performs.
#
#   pnpm ship            (or: scripts/ship.sh)
#       Approve "merge to main": push the committed HEAD of `main` to
#       origin/main. Nothing else — no release, no site change.
#
#   pnpm ship release    (any argument triggers release mode)
#       Full beta release, on top of the push above:
#         1. Merge main -> release and push it, triggering the
#            "Release (beta, unsigned)" workflow, which builds + signs every
#            OS and publishes v<version>-beta plus the auto-update manifest.
#         2. Wait for that run and confirm the release actually published.
#         3. Point findsyoujobs.com's download links at v<version>-beta and
#            push the site (Cloudflare Pages auto-deploys).
#
# <version> is read from src-tauri/tauri.conf.json (numeric) + "-beta". Bump the
# version files + commit on main BEFORE running this — the script publishes
# what's committed, it does not commit for you.
#
# Requires: clean, committed tree on `main`; `gh` authenticated; and the Ed25519
# updater secret TAURI_SIGNING_PRIVATE_KEY set on the repo (else the release
# build fails by design). The site repo is expected at ~/dev/findsyoujobs-site
# (override with FYJ_SITE_DIR).

set -euo pipefail

SITE_DIR="${FYJ_SITE_DIR:-$HOME/dev/findsyoujobs-site}"
die() { echo "ship: $*" >&2; exit 1; }

# --- locate + sanity-check the repo -----------------------------------------
cd "$(git rev-parse --show-toplevel)" || die "not in a git repo"
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "not on 'main' — checkout main first"
[ -z "$(git status --porcelain)" ] || die "working tree not clean — commit your changes first"

VERSION=$(node -p "require('./src-tauri/tauri.conf.json').version")
TAG="v${VERSION}-beta"
RELEASE=0
[ "$#" -gt 0 ] && RELEASE=1

# In release mode, pin the from-source install scripts + README download link to
# this tag BEFORE publishing main, so the released source carries the right pin
# (RELEASING.md §2). The pipeline only ever creates the "-beta" tag, so that's
# what we pin to. Committed here because it's a purely derived, mechanical bump.
if [ "$RELEASE" -eq 1 ]; then
  echo "ship: pinning from-source installs + README to ${TAG}"
  perl -pi -e 's/^LATEST_TAG=.*/LATEST_TAG="'"$TAG"'"/'       scripts/setup.sh
  perl -pi -e 's/^\$LatestTag = .*/\$LatestTag = "'"$TAG"'"/' scripts/setup.ps1
  perl -pi -e 's{v[0-9]+\.[0-9]+\.[0-9]+-beta}{'"$TAG"'}g'    README.md
  if ! git diff --quiet -- scripts/setup.sh scripts/setup.ps1 README.md; then
    git add scripts/setup.sh scripts/setup.ps1 README.md
    git commit -s -m "release: pin from-source installs to ${TAG}

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  fi
fi

# --- always: publish main ----------------------------------------------------
echo "ship: pushing main -> origin/main"
git push origin main

# no argument → done (this was approval to publish main only)
if [ "$RELEASE" -eq 0 ]; then
  echo "ship: main published. Run 'pnpm ship release' to also cut ${TAG}."
  exit 0
fi

# --- release mode ------------------------------------------------------------
echo "ship: cutting ${TAG} (merge -> release -> build -> site)"
git checkout release
git pull --ff-only origin release
git merge --no-ff --signoff main -m "Merge branch 'main' into release — ${TAG}

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push origin release
RELEASE_SHA=$(git rev-parse HEAD)
git checkout main

# find the run for exactly this push (retry — it takes a moment to register)
echo "ship: locating the release run…"
RUN_ID=""
for _ in $(seq 1 12); do
  RUN_ID=$(gh run list --workflow=release.yml --branch=release --limit 10 \
    --json databaseId,headSha \
    -q "map(select(.headSha==\"${RELEASE_SHA}\")) | .[0].databaseId" || true)
  [ -n "$RUN_ID" ] && break
  sleep 5
done
[ -n "$RUN_ID" ] || die "could not find the release run for ${RELEASE_SHA}"

echo "ship: watching run ${RUN_ID} (~12 min)…"
# A single failed leg must not block the site if the release still published,
# so don't let a non-zero watch exit abort the script — gate on the release
# below instead.
gh run watch "$RUN_ID" || true

gh release view "$TAG" --json assets -q '.assets | length' >/dev/null 2>&1 \
  || die "release ${TAG} did not publish — leaving the site untouched"

# --- update findsyoujobs.com -------------------------------------------------
[ -d "$SITE_DIR/.git" ] || die "site repo not found at ${SITE_DIR} (set FYJ_SITE_DIR)"
echo "ship: pointing findsyoujobs.com at ${TAG}"
cd "$SITE_DIR"
git checkout main
git pull --ff-only origin main
OLD=$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+-beta' public/index.html | sort -u | head -1 || true)
if [ -n "$OLD" ] && [ "$OLD" != "${VERSION}-beta" ]; then
  # Replace the whole version token everywhere (download URLs, asset filenames,
  # and the visible label all use the same "<x.y.z>-beta" string).
  perl -pi -e "s/\Q${OLD}\E/${VERSION}-beta/g" public/index.html
  git add public/index.html
  git commit -s -m "site: point downloads at ${TAG}

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  git push origin main
  echo "ship: site updated ${OLD} -> ${VERSION}-beta and pushed"
else
  echo "ship: site already at ${VERSION}-beta (or no version token found) — no change"
fi

echo "ship: done — ${TAG} is live and findsyoujobs.com points at it."
