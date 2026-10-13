"""F-P10 boundary guard: core never imports the GPLv3 `upstream/` subtree.

`sidecar.app.*` and `sidecar.modules.*` reach the LinkedIn core only through
the AGPL facade; this walks every core module's AST and fails on an
`upstream.*` import. AST only, so prose naming `upstream.worker` doesn't trip
it."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

# `.../sidecar/tests/packages/referral_outreach/<this file>` -> the `sidecar/` dir.
SIDECAR = Path(__file__).resolve().parents[3]
CORE_ROOTS = (SIDECAR / "app", SIDECAR / "modules")

# The import target core is not allowed to name. Substring match catches both the
# absolute path (`sidecar.packages.referral_outreach.upstream...`) and any
# relative form (`...packages.referral_outreach.upstream`), while leaving the
# facade (`referral_outreach.facade`) and the package root (`referral_outreach`)
# free to import.
FORBIDDEN = "referral_outreach.upstream"


def _forbidden_imports(source: str, filename: str = "<test>") -> list[tuple[int, str]]:
    """Every `import`/`from ... import` in `source` whose target names the GPL
    upstream subtree, as `(lineno, target)` pairs."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            # A relative `from ...pkg import x` has module=`pkg`, level>0; keep the
            # leading dots so the substring test still sees the full path.
            targets = [("." * node.level) + (node.module or "")]
        else:
            continue
        hits.extend((node.lineno, t) for t in targets if FORBIDDEN in t)
    return hits


def _core_py_files() -> Iterator[Path]:
    for root in CORE_ROOTS:
        for path in root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def test_core_never_imports_referral_upstream() -> None:
    offenders = [
        f"{path.relative_to(SIDECAR.parent)}:{lineno} imports {target}"
        for path in _core_py_files()
        for lineno, target in _forbidden_imports(path.read_text(encoding="utf-8"), str(path))
    ]
    assert not offenders, (
        "core must reach the referral upstream only through the F-P10 facade "
        "(`sidecar.packages.referral_outreach` / `.facade`), never `upstream.*`:\n"
        + "\n".join(offenders)
    )


def test_the_guard_catches_a_direct_upstream_import() -> None:
    # Guard the guard: a direct upstream import must be flagged, the facade must not.
    assert _forbidden_imports("from sidecar.packages.referral_outreach.upstream import worker")
    assert _forbidden_imports(
        "from sidecar.packages.referral_outreach.upstream.pacing import Pacer"
    )
    assert not _forbidden_imports(
        "from sidecar.packages.referral_outreach.facade import Pacer, worker_module\n"
        "from sidecar.packages.referral_outreach import PacingProfile\n"
    )
