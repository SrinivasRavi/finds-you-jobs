"""PyInstaller entry point only — not part of the app's own module graph.

PyInstaller's `Analysis` runs its entry script directly as `__main__` with no
package context, which breaks `sidecar/app/__main__.py`'s relative imports
(`from .logging_setup import ...`). `runpy.run_module` reproduces exactly what
`python -m sidecar.app` does — including the package context those relative
imports need — without changing `__main__.py` itself. Referenced by
sidecar/fyj-sidecar.spec.
"""

import runpy

if __name__ == "__main__":
    runpy.run_module("sidecar.app", run_name="__main__")
