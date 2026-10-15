"""Covers: the chat agent's tool list (audit-2026-10-14.md section 8.11, step 3).

Every route is a tool or excluded with a reason, so a new route can't reach the
agent, or miss it, by accident. No tool can change the user's own limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from sidecar.app.chat.tools import EXCLUDED, IRREVERSIBLE, READ, TOOLS, catalog, routes
from sidecar.app.main import create_app

from ._support import TOKEN


@pytest.fixture(scope="module")
def spec(tmp_path_factory: pytest.TempPathFactory) -> dict:
    app = create_app(token=TOKEN, original_ppid=None,
                     data_dir=Path(tmp_path_factory.mktemp("d")), enable_scheduler=False)
    return app.openapi()


def test_every_route_is_a_tool_or_excluded_with_a_reason(spec: dict) -> None:
    names = set(routes(spec))
    in_tools = {t.endpoint for t in TOOLS.values()}
    dev = {n for n in names if n.startswith("dev_")}  # dev knobs: absent from a release
    unclassified = names - in_tools - set(EXCLUDED) - dev
    assert not unclassified, f"classify these routes in chat/tools.py: {sorted(unclassified)}"
    assert not (in_tools | set(EXCLUDED)) - names, "a listed route no longer exists"
    both = {t.endpoint for t in TOOLS.values() if not t.fixed} & set(EXCLUDED)
    assert not both, both
    assert all(reason.strip() for reason in EXCLUDED.values())


_USER_OWNED = ("/api/settings", "/api/engines", "/api/linkedin/connect", "/api/dev",
               "/api/discovery/credentials", "/api/schedules", "/api/profile")


def test_no_tool_writes_settings_keys_or_accounts(spec: dict) -> None:
    by_name = routes(spec)
    for name, tool in TOOLS.items():
        route = by_name[tool.endpoint]
        if route.method == "GET":
            continue
        assert not route.path.startswith(_USER_OWNED), name


def test_a_read_tool_is_a_get_and_a_send_or_submit_is_irreversible(spec: dict) -> None:
    by_name = routes(spec)
    for name, tool in TOOLS.items():
        # preview_job fetches a URL and stores nothing, over POST.
        if tool.effect == READ and name != "preview_job":
            assert by_name[tool.endpoint].method == "GET", name
    assert TOOLS["reach_out"].effect == IRREVERSIBLE
    assert TOOLS["submit_apply_run"].effect == IRREVERSIBLE


def test_the_catalog_names_each_tools_arguments(spec: dict) -> None:
    by_tool = {t["tool"]: t for t in catalog(spec)}
    assert by_tool["get_job"]["args"] == {"job_id": "path"}
    assert "kind" not in by_tool["start_scan"]["args"]  # pinned to "scan"
    assert by_tool["reach_out"]["cost"] == "LinkedIn meter"
    assert isinstance(by_tool["reach_out"]["args"]["body"], dict)
    assert all(t["does"] for t in by_tool.values())
