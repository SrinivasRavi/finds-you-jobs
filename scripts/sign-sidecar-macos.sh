#!/usr/bin/env bash
# Deep-sign every Mach-O binary inside the PyInstaller sidecar dist before
# `tauri build` copies it into the .app's Resources. Tauri signs the app
# bundle but does not recurse into resource directories, and notarization
# rejects any embedded Mach-O that lacks a Developer ID signature with a
# secure timestamp (seen live 2026-08-08; docs/internal/apple-signing.md
# section 7). Run after PyInstaller, before `pnpm tauri build`.
set -euo pipefail

DIST="${1:-dist/fyj-sidecar}"
[ -d "$DIST" ] || { echo "sign-sidecar-macos: $DIST not found; build the sidecar first" >&2; exit 1; }
: "${APPLE_SIGNING_IDENTITY:?sign-sidecar-macos: APPLE_SIGNING_IDENTITY is not set}"

# Flatten symlinks first (cp -RL dereferences them into real files). The
# PyInstaller tree symlinks _internal/Python into Python.framework; Tauri's
# resource copy dereferences all symlinks anyway, which turns a framework-
# context signature into an invalid one on the flattened copy (notarization
# rejected exactly this, 2026-08-08). Signing the already-flattened layout
# makes the signatures match what actually ships. Shipped v0.5.x apps run
# this flattened layout, so it is known-good at runtime.
if find "$DIST" -type l | read -r _; then
  tmp="${DIST}.deref.$$"
  cp -RpL "$DIST" "$tmp"
  rm -rf "$DIST"
  mv "$tmp" "$DIST"
  echo "sign-sidecar-macos: flattened symlinks in $DIST"
fi

# Drop the Python.framework remnant. After flattening, the bootloader dlopens
# _internal/Python directly and no binary links the framework path (verified
# by a boot test 2026-08-08). Keeping the directory makes Apple validate it
# as a framework *bundle*, which the flattened layout can never satisfy —
# notarization rejected exactly that. Deleting it also drops 3 duplicate
# copies of the 5 MB Python library.
rm -rf "$DIST/_internal/Python.framework"

# Mach-O detection by content, not extension: the tree holds .so/.dylib files
# but also extensionless executables (bundled node, python launchers).
count=0
while IFS= read -r -d '' f; do
  case "$(file -b "$f")" in
    Mach-O*) printf '%s\0' "$f"; count=$((count + 1)) ;;
  esac
done < <(find "$DIST" -type f -print0) \
  | xargs -0 -n 32 codesign --force --options runtime --timestamp \
      --sign "$APPLE_SIGNING_IDENTITY"

echo "sign-sidecar-macos: signed Mach-O binaries in $DIST as '$APPLE_SIGNING_IDENTITY'"
