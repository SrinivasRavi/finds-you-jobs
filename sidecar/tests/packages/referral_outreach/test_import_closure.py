"""Import-closure guard for the GPLv3 `upstream/` subtree.

New finds-you-jobs-owned test (not carried from upstream). It walks every module
under `sidecar.packages.referral_outreach.upstream` and imports it, so a future
carry that forgets a module — the exact failure that let `company.py` ship
missing and unnoticed — fails loudly here instead of at first live use.
"""

from __future__ import annotations

import importlib
import pkgutil

from sidecar.packages.referral_outreach import upstream

# The core modules the facade drives directly. A deletion/rename must break this
# test, not just silently shrink the discovered set.
EXPECTED_MODULES = {
    "actions",
    "client",
    "company",
    "discovery",
    "errors",
    "pacing",
    "secure_store",
    "session",
    "url_utils",
    "voyager",
    "worker",
}


def _discovered_module_names() -> set[str]:
    return {name for _finder, name, _ispkg in pkgutil.iter_modules(upstream.__path__)}


def test_every_upstream_module_imports() -> None:
    discovered = _discovered_module_names()
    # Every discovered module must import cleanly (a broken carry raises here).
    for name in sorted(discovered):
        importlib.import_module(f"{upstream.__name__}.{name}")
    # And every module the facade depends on must actually be present.
    missing = EXPECTED_MODULES - discovered
    assert not missing, f"upstream subtree is missing carried modules: {sorted(missing)}"


def test_retired_cli_bridge_absent() -> None:
    # The JSON-CLI/subprocess bridge is deliberately not carried (provenance.md).
    discovered = _discovered_module_names()
    assert "cli" not in discovered
    assert "__main__" not in discovered
