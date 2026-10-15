"""The chat agent's tools: the app's own routes (audit-2026-10-14.md section 8.11).

AGPL-3.0-only. Every route is listed here once, as a tool or as excluded with a
reason, and a test fails on a route that is neither. A tool's description and
arguments come from the OpenAPI schema, so a route change reaches the agent with
no edit here. The agent can call nothing that isn't in `TOOLS`."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# What a call can spend, so the agent can say what a plan costs.
FREE, LLM, LINKEDIN, APPLICATION = "free", "LLM spend", "LinkedIn meter", "application"
# What a call changes.
READ, REVERSIBLE, IRREVERSIBLE = "read", "reversible", "irreversible"


@dataclass(frozen=True)
class Tool:
    endpoint: str  # the route function's name
    cost: str
    effect: str
    fixed: dict[str, str] = field(default_factory=dict)  # path values the tool pins


def _t(endpoint: str, cost: str = FREE, effect: str = READ, **fixed: str) -> Tool:
    return Tool(endpoint, cost, effect, fixed)


TOOLS: dict[str, Tool] = {
    # Jobs
    "list_jobs": _t("list_jobs"),
    "board": _t("board"),
    "get_job": _t("get_job"),
    "get_job_factors": _t("get_job_factors"),
    "get_job_duplicate": _t("get_job_duplicate"),
    "scan_progress": _t("scan_progress"),
    "scoring_retryable": _t("scoring_retryable"),
    "explain_cost": _t("explain_cost"),
    "preview_job": _t("preview_job"),
    "start_scan": _t("create_operation", FREE, REVERSIBLE, kind="scan"),
    "create_job": _t("create_job", LLM, REVERSIBLE),
    "update_job": _t("update_job", FREE, REVERSIBLE),
    "attach_job_link": _t("attach_job_link", FREE, REVERSIBLE),
    "dismiss_job_duplicate": _t("dismiss_job_duplicate", FREE, REVERSIBLE),
    "tombstone_job": _t("tombstone_job", FREE, REVERSIBLE),
    "explain_job": _t("explain_job", LLM, REVERSIBLE),
    "rescore_job": _t("rescore_job", LLM, REVERSIBLE),
    "scoring_retry": _t("scoring_retry", LLM, REVERSIBLE),
    # Applications and the Applier
    "list_applications": _t("list_applications"),
    "get_application": _t("get_application"),
    "application_activity": _t("application_activity"),
    "list_apply_runs": _t("list_apply_runs"),
    "get_apply_run": _t("get_apply_run"),
    "create_application": _t("create_application", FREE, REVERSIBLE),
    "create_manual_application": _t("create_manual_application", FREE, REVERSIBLE),
    "update_application": _t("update_application", FREE, REVERSIBLE),
    "delete_application": _t("delete_application", FREE, REVERSIBLE),
    "generate_packet": _t("generate_packet", LLM, REVERSIBLE),
    "patch_artifact": _t("patch_artifact", FREE, REVERSIBLE),
    "start_apply": _t("start_apply", APPLICATION, IRREVERSIBLE),
    "submit_apply_run": _t("submit_apply_run", APPLICATION, IRREVERSIBLE),
    "cancel_apply_run": _t("cancel_apply_run", FREE, REVERSIBLE),
    # Operations and cost
    "list_operations": _t("list_operations"),
    "get_operation": _t("get_operation"),
    "cost_totals": _t("cost_totals"),
    "retry_operation": _t("retry_operation", LLM, REVERSIBLE),
    "cancel_operation": _t("cancel_operation", FREE, REVERSIBLE),
    # Networking
    "application_networking": _t("application_networking"),
    "list_contacts": _t("list_contacts"),
    "list_referral_candidates": _t("list_referral_candidates"),
    "referrals_quota": _t("referrals_quota"),
    "linkedin_session": _t("linkedin_session"),
    "create_contact": _t("create_contact", FREE, REVERSIBLE),
    "update_contact": _t("update_contact", FREE, REVERSIBLE),
    "discover_referrals": _t("discover_referrals", LINKEDIN, REVERSIBLE),
    "draft_referral": _t("draft_referral", LLM, REVERSIBLE),
    "reach_out": _t("reach_out", LINKEDIN, IRREVERSIBLE),
    "networking_contact_sync": _t("networking_contact_sync", LINKEDIN, REVERSIBLE),
    "linkedin_search": _t("linkedin_search", LINKEDIN, REVERSIBLE),
    # Discovery and settings, read only
    "get_profile": _t("get_profile"),
    "get_settings": _t("get_settings"),
    "list_schedules": _t("list_schedules"),
    "list_discovery_sources": _t("list_discovery_sources"),
    "list_watched_companies": _t("list_watched_companies"),
    "discovery_analytics": _t("discovery_analytics"),
    "suggest_places": _t("suggest_places"),
    "list_countries": _t("list_countries"),
    "suggest_titles": _t("suggest_titles"),
    "watch_company": _t("watch_company", FREE, REVERSIBLE),
    "unwatch_company": _t("unwatch_company", FREE, REVERSIBLE),
}

_USER_ONLY = (
    "the user's own settings, keys or accounts; "
    "an agent that could change them could switch its own limits off"
)
EXCLUDED: dict[str, str] = {
    "healthz": "the shell's health poll",
    "events": "a stream, not a call",
    "shutdown": "stops the app",
    "export_pdf": "writes a file where the user picks",
    "browser_install_status": "a system check",
    "install_browser": "a system download",
    "scheduler_status": "a system check",
    "resume_scheduler": "a system switch",
    "empty_trash": "deletes for good; only a click may",
    "upsert_profile": "replaces the master resume; only the user edits it",
    "extract_application_profile": "rewrites the applicant details from the resume",
    "patch_application_profile": "the applicant details are the user's own answers",
    "replace_settings": _USER_ONLY,
    "update_settings": _USER_ONLY,
    "update_schedule": _USER_ONLY,
    "run_schedule": "the autopilot schedule runs as the app; start_scan covers search",
    "list_prompts": "prompt tuning is the user's",
    "set_prompt": _USER_ONLY,
    "reset_prompt": _USER_ONLY,
    "test_prompt": "prompt tuning is the user's",
    "attach_application_document": "a file upload",
    "detach_application_document": "removes a file the user attached",
    "download_document": "a file, not text",
    "get_apply_run_screenshot": "an image, not text",
    "show_apply_run": "moves a window the user is watching",
    "attest_apply_run": "records that the user submitted by hand; only the user knows",
    "create_operation": "the generic enqueue; start_scan is the 1 kind the agent starts this way",
    "get_operation_spans": "debug detail",
    "linkedin_connect": _USER_ONLY,
    "linkedin_disconnect": _USER_ONLY,
    "linkedin_validate": _USER_ONLY,
    "linkedin_resume": _USER_ONLY,
    "linkedin_set_rate_limits": _USER_ONLY,
    "view_browser_page": "the live view opens on a click; browser tools come in part B",
    "verify_engine": _USER_ONLY,
    "list_engines": _USER_ONLY,
    "save_engine": _USER_ONLY,
    "delete_engine": _USER_ONLY,
    "toggle_discovery_source": _USER_ONLY,
    "list_discovery_credentials": _USER_ONLY,
    "save_discovery_credential": _USER_ONLY,
    "delete_discovery_credential": _USER_ONLY,
    "ingest_resume": "a file upload",
    "list_chats": "the chat itself",
    "send_chat_message": "the chat itself",
    "get_chat": "the chat itself",
    "confirm_chat_card": "a card is answered by the user's click",
    "dismiss_chat_card": "a card is answered by the user's click",
}


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    summary: str
    params: dict[str, str]  # name -> "path" | "query"
    body: dict[str, Any] | None


def routes(spec: dict[str, Any]) -> dict[str, Route]:
    """Every route in the OpenAPI schema, keyed by its function's name (the
    operationId without the path and method FastAPI appends)."""
    out: dict[str, Route] = {}
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            suffix = re.sub(r"\W", "_", path) + "_" + method
            name = op["operationId"].removesuffix(suffix)
            params = {p["name"]: p["in"] for p in op.get("parameters", [])}
            content = (op.get("requestBody") or {}).get("content", {})
            body = content.get("application/json", {}).get("schema")
            text = (op.get("description") or op.get("summary") or "").strip()
            out[name] = Route(method.upper(), path, text.split("\n\n")[0], params, body)
    return out


def _shape(schema: dict[str, Any], comps: dict[str, Any], depth: int = 0) -> Any:
    """A schema as the model reads it: field names to type names, 2 levels deep."""
    if "$ref" in schema:
        schema = comps.get(schema["$ref"].rsplit("/", 1)[-1], {})
    if "anyOf" in schema:
        kinds = [_shape(s, comps, depth) for s in schema["anyOf"] if s.get("type") != "null"]
        return kinds[0] if len(kinds) == 1 else kinds
    if schema.get("enum"):
        return " | ".join(map(str, schema["enum"]))
    if schema.get("type") == "object" and "properties" in schema and depth < 2:
        req = set(schema.get("required", []))
        return {
            (k if k in req else k + "?"): _shape(v, comps, depth + 1)
            for k, v in schema["properties"].items()
        }
    if schema.get("type") == "array":
        return [_shape(schema.get("items", {}), comps, depth + 1)]
    return schema.get("type", "any")


def catalog(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The tool list the model reads: name, what it does, cost, effect, arguments."""
    by_name, comps = routes(spec), spec.get("components", {}).get("schemas", {})
    out = []
    for name, tool in TOOLS.items():
        route = by_name[tool.endpoint]
        args: dict[str, Any] = {
            k + ("" if where == "path" else "?"): where
            for k, where in route.params.items()
            if k not in tool.fixed
        }
        if route.body is not None:
            args["body"] = _shape(route.body, comps)
        out.append({"tool": name, "does": route.summary, "cost": tool.cost,
                    "effect": tool.effect, "args": args})
    return out
