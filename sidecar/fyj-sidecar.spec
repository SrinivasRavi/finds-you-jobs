# PyInstaller onedir spec for the finds-you-jobs sidecar (FastAPI + uvicorn).
#
# Produces `dist/fyj-sidecar/fyj-sidecar[.exe]` plus its `_internal/` support
# tree — this is what `tauri.conf.json`'s `bundle.resources` ships as
# `sidecar/` inside the packaged app, and what `sidecar.rs`'s prod path
# (`PROD_SIDECAR_REL = "sidecar/fyj-sidecar"`) spawns at runtime
# (docs/internal/distribution.md §8).
#
# Onedir, not onefile: onefile self-extracts to a temp dir on every launch,
# which would show up as extra sidecar-handshake latency on every app start.
#
# Built per-OS, natively, in CI — PyInstaller does not cross-compile.
#
#   uv run pyinstaller sidecar/fyj-sidecar.spec --noconfirm
#
# Run from the repo root (so relative paths below resolve); works the same
# from any cwd since every path here is computed from SPECPATH.

import os
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(SPECPATH))  # sidecar/ -> repo root
SIDECAR = os.path.join(REPO_ROOT, "sidecar")

# Non-Python resource files the app reads by path at runtime (everything here
# uses the `Path(__file__).resolve().parent / "..."` convention already in
# the codebase — see sidecar/app/db/migrate.py, sidecar/modules/*/prompt.py,
# sidecar/modules/networker/playbooks.py, sidecar/app/seed.py,
# sidecar/app/logging_setup.py). None of these are reached by a Python
# `import`, so PyInstaller's static analysis can't find them on its own —
# they must be listed explicitly as data files.
datas = [
    (os.path.join(SIDECAR, "app", "db", "alembic.ini"), "sidecar/app/db"),
    # Whole alembic/ tree as data, not analyzed code: migration scripts are
    # exec'd by Alembic's own directory scan, never `import`ed by name, so
    # PyInstaller's import-following analysis would otherwise skip them.
    (os.path.join(SIDECAR, "app", "db", "alembic"), "sidecar/app/db/alembic"),
    (os.path.join(SIDECAR, "modules", "scraper", "registry", "portals-all.toml"), "sidecar/modules/scraper/registry"),
    (os.path.join(SIDECAR, "modules", "scraper", "registry", "portals-india.toml"), "sidecar/modules/scraper/registry"),
    (os.path.join(SIDECAR, "modules", "scraper", "registry", "portals-remote.toml"), "sidecar/modules/scraper/registry"),
    (os.path.join(SIDECAR, "modules", "scraper", "portals.example.toml"), "sidecar/modules/scraper"),
    (os.path.join(SIDECAR, "modules", "networker", "draft-referral-skill.md"), "sidecar/modules/networker"),
    (os.path.join(SIDECAR, "modules", "networker", "playbooks"), "sidecar/modules/networker/playbooks"),
    (os.path.join(SIDECAR, "modules", "scorer", "score-job-skill.md"), "sidecar/modules/scorer"),
    (os.path.join(SIDECAR, "modules", "tailorer", "tailor-resume-skill.md"), "sidecar/modules/tailorer"),
    (os.path.join(SIDECAR, "modules", "coverletterer", "cover-letter-skill.md"), "sidecar/modules/coverletterer"),
    # Skyvern-derived DOM utility script the Applier's browser agent injects —
    # loaded by path at runtime (sidecar/packages/jobapplier/upstream/page_utils.py),
    # never `import`ed, so it needs the same explicit-datas treatment.
    (os.path.join(SIDECAR, "packages", "jobapplier", "upstream", "domUtils.js"), "sidecar/packages/jobapplier/upstream"),
]

# keyring's backend selection is dynamic (importlib.metadata entry points),
# which PyInstaller's static import-following can't see — force the one
# backend relevant to the OS this spec is actually being run on (PyInstaller
# never cross-compiles, so sys.platform here IS the target platform) plus the
# universal no-op fallback.
# sidecar.app.__main__ is never `import`ed by anything (that's normal for a
# `__main__.py` — it's meant to be run, not imported), and the entry point
# above reaches it only via runpy's dynamic, string-based lookup at runtime,
# which PyInstaller's static analysis can't see through. Force it so the file
# actually gets bundled and is there for runpy to find.
hiddenimports = ["sidecar.app.__main__", "keyring.backends.fail"]
if sys.platform == "darwin":
    hiddenimports += ["keyring.backends.macOS"]
elif sys.platform == "win32":
    hiddenimports += ["keyring.backends.Windows"]
else:
    hiddenimports += ["keyring.backends.SecretService", "keyring.backends.kwallet"]

a = Analysis(
    [os.path.join(SIDECAR, "_pyinstaller_entry.py")],
    pathex=[REPO_ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    # 'py': collect sidecar/**/*.py as real files on disk (not compiled into
    # the PYZ archive) so `Path(__file__).resolve().parent`-style resource
    # loading throughout the codebase keeps resolving to real paths, exactly
    # as it does unfrozen — the alternative would mean patching every one of
    # those call sites just for packaging, which the "surgical changes" rule
    # says not to do when a PyInstaller-level fix covers all of them at once.
    # logfire's pydantic-plugin integration calls inspect.getsource() at
    # import time (to patch pydantic's schema validator) — that fails once
    # pydantic is compiled into the PYZ archive with no on-disk source for
    # inspect to read, even though nothing about its behavior actually
    # differs. Keeping it collected as real files sidesteps the failure
    # without touching the app's actual observability config.
    module_collection_mode={"sidecar": "py", "pydantic": "py"},
    excludes=["sidecar.tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="fyj-sidecar",
    console=True,  # no GUI of its own; stdout carries the PORT=/TOKEN= handshake
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="fyj-sidecar",
)
