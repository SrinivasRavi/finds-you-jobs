// Assemble the Tauri updater manifest (latest.json) from the built updater
// artifacts and their .sig files. Run in the release job AFTER every OS leg's
// artifacts are merged into one directory.
//
// Why this exists (rather than tauri-action doing it): our releases are GitHub
// *pre-releases* (v0.5.x-beta), and GitHub's `/releases/latest/` endpoint skips
// pre-releases — so the app can't poll a "latest release" URL. Instead we emit
// latest.json here and publish it to a fixed `latest` tag, giving the updater a
// stable endpoint (see tauri.conf.json plugins.updater.endpoints) that keeps
// working no matter how the per-version releases are flagged.
//
// The manifest `version` must be the strictly-numeric bundle version ("0.5.5")
// so Tauri's semver compare against the running app triggers correctly; the
// download URLs use the "-beta" tag + stamped filenames of the actual assets.
//
// Usage: node build-updater-manifest.mjs <distDir> <numericVersion> <tag> <outPath>

import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [distDir, version, tag, outPath] = process.argv.slice(2);
if (!distDir || !version || !tag || !outPath) {
  console.error(
    "usage: build-updater-manifest.mjs <distDir> <numericVersion> <tag> <outPath>",
  );
  process.exit(1);
}

const BASE = `https://github.com/SrinivasRavi/finds-you-jobs/releases/download/${tag}`;

// Map an updater artifact filename to Tauri's platform key. The updater target
// per OS is: macOS → .app.tar.gz, Windows → the NSIS -setup.exe, Linux →
// .AppImage. (.dmg/.msi/.deb/.rpm are first-install installers, not update
// packages.)
// The platform KEYS below are Tauri's own updater identifiers and must not
// change. What changed is the filenames they are read out of: the macOS
// artifacts are named `-arm64` / `-intel` for the humans downloading them
// (release.yml), not `aarch64` / `x64`.
function platformFor(name) {
  if (name.endsWith(".app.tar.gz")) {
    if (name.includes("-arm64.")) return "darwin-aarch64";
    if (name.includes("-intel.")) return "darwin-x86_64";
    // Refuse to guess. The previous version defaulted an unrecognised macOS
    // bundle to Intel, so a rename would have silently pointed every Apple
    // Silicon install at the wrong binary instead of failing.
    console.warn(`unrecognised macOS updater artifact, skipping: ${name}`);
    return null;
  }
  if (name.endsWith("-setup.exe")) return "windows-x86_64";
  if (name.endsWith(".AppImage")) return "linux-x86_64";
  return null;
}

const files = readdirSync(distDir);
const platforms = {};
for (const name of files) {
  const key = platformFor(name);
  if (!key) continue;
  const sigName = `${name}.sig`;
  if (!files.includes(sigName)) {
    console.warn(`no signature (${sigName}) for ${name} — skipping ${key}`);
    continue;
  }
  const signature = readFileSync(join(distDir, sigName), "utf8").trim();
  platforms[key] = { signature, url: `${BASE}/${encodeURIComponent(name)}` };
}

if (Object.keys(platforms).length === 0) {
  console.error("no signed updater artifacts found — refusing to write an empty manifest");
  process.exit(1);
}

const manifest = {
  version,
  notes: `finds-you-jobs ${tag}`,
  pub_date: new Date().toISOString(),
  platforms,
};

writeFileSync(outPath, JSON.stringify(manifest, null, 2));
console.log(`wrote ${outPath} for ${Object.keys(platforms).join(", ")}`);
console.log(JSON.stringify(manifest, null, 2));
