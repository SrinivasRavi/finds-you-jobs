# finds-you-jobs — AGPL-3.0-only. finds-you-jobs-owned (no upstream code).
# ruff: noqa: E501 — the RULES table is a data table of regex patterns; wrapping
# the patterns across lines hurts readability more than the width does.
"""Deterministic decider: fills a job-application form with no LLM.

The code rung of the ladder (applier.md Option 11). Called once per loop turn in
place of the model. It walks the observation in document order, classifies the
first eligible unfilled field against an ordered rule table (semantics ported
from the proven interpreter under
``docs/internal/applier/evidence/2026-08-12-deterministic-repair/``), grounds the
value in ``ApplyRequest.profile_facts`` / ``preferences`` / ``approved_links``,
and emits exactly one Action from the same vocabulary the model uses — there is
still no submit tool anywhere.

A field no rule can ground is reported (when required) or skipped (when
optional), never filled with a first option or a default yes: the wrong fills in
the deterministic baseline were all fallback-to-first or default-to-yes. No
persona literal lives in this module; every value flows from the request.

Each field gets exactly one decision, recorded in ``done`` at decision time —
one attempt per field, ever. That is the livelock fix by construction: the
decider never re-issues an action, so it never re-verifies a settled field.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field

from .actions import Action
from .observe import Observation, ObservedElement
from .types import ApplyRequest

# ---------------------------------------------------------------------------
# Shared option-preference lists (first regex that hits any option wins)
# ---------------------------------------------------------------------------

DECLINE_PREFS = [
    r"decline to (self.?identify|answer|state|specify|disclose)",
    r"prefer not to (say|answer|disclose|respond|identify|specify)",
    r"(don'?t|do not) wish to (answer|self.?identify|disclose|say|provide)",
    r"(don'?t|do not) want to (answer|disclose|self.?identify|provide)",
    r"^i (would )?prefer not",
    r"^decline\b",
    r"prefer not",
    r"choose not to",
    r"^not specified$",
]
YES_PREFS = [r"^\s*yes\s*$", r"^\s*y\s*$", r"^\s*true\s*$", r"^yes[,.]?\s+(i|it|we|of course)\b"]
NO_PREFS = [r"^\s*no\s*$", r"^\s*n\s*$", r"^\s*false\s*$", r"^no[,.]?\s+(i|it|we)\b", r"^not\b", r"^none\b"]
_NOISH = re.compile(r"^(no|none|false)\b", re.I)

# ---------------------------------------------------------------------------
# Value grounding: derivers over the profile facts (no persona literal here)
# ---------------------------------------------------------------------------


def _pre_paren(s: str | None) -> str | None:
    return (s or "").split("(")[0].strip() or None


def _re1(pat: str, s: str | None) -> str | None:
    m = re.search(pat, s or "")
    return m.group(1).strip() if m else None


def _csv(s: str | None, idx: int) -> str | None:
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    if not parts:
        return None
    try:
        return parts[idx]
    except IndexError:
        return None


def _fmt_money(unit: str, amount: str | None) -> str | None:
    return f"{unit} {amount} per year" if amount else None


def _monthly(annual: str | None) -> str | None:
    if not annual:
        return None
    try:
        return f"{int(annual.replace(',', '')) // 12:,}"
    except ValueError:
        return None


_DERIVERS = {
    "first_name": lambda p: (
        _re1(r"\(first ([^,]+),", p.get("legal name"))
        or ((_pre_paren(p.get("legal name")) or "").split(" ") or [None])[0]
        or None
    ),
    "last_name": lambda p: (
        _re1(r"last ([^)]+)\)", p.get("legal name"))
        or ((_pre_paren(p.get("legal name")) or "").split(" ") or [None])[-1]
    ),
    "full_name": lambda p: _pre_paren(p.get("legal name")),
    "email": lambda p: (p.get("email") or "").strip() or None,
    "phone_full": lambda p: (p.get("phone") or "").strip() or None,
    "phone_cc": lambda p: _re1(r"^(\+\d{1,3})\b", p.get("phone")),
    "phone_national": lambda p: re.sub(r"^\+\d{1,3}\s*", "", p.get("phone", "")).strip() or None,
    "location_full": lambda p: _pre_paren(p.get("location")),
    "city": lambda p: _csv(_pre_paren(p.get("location")), 0),
    "state": lambda p: _csv(_pre_paren(p.get("location")), 1),
    "country": lambda p: _csv(_pre_paren(p.get("location")), -1),
    "citizenship": lambda p: (p.get("citizenship") or "").strip() or None,
    "home_country": lambda p: (
        _re1(r"authori[sz]ed in ([A-Za-z ]+?)(?: only)?[;,]", p.get("work authorization"))
        or (p.get("citizenship") or "").strip()
        or None
    ),
    "current_title": lambda p: _re1(r"^([^,;(]+?),", p.get("current role")),
    "current_company": lambda p: _re1(r"\bat\s+([^\s(;,]+)", p.get("current role")),
    "years_total": lambda p: _re1(r"(\d+)\s*\+?\s*year", p.get("experience")),
    "salary_year_usd": lambda p: _fmt_money("USD", _re1(r"USD\s*([\d,]+)\s*/\s*year", p.get("salary expectation"))),
    "salary_year_inr": lambda p: _fmt_money("INR", _re1(r"INR\s*([\d,]+)\s*/\s*year", p.get("salary expectation"))),
    "salary_month_usd": lambda p: _monthly(_re1(r"USD\s*([\d,]+)\s*/\s*year", p.get("salary expectation"))),
    "notice_days": lambda p: "0" if re.search(r"immediate|no notice", p.get("availability", ""), re.I) else None,
    "start_when": lambda p: "Immediately" if re.search(r"immediate", p.get("availability", ""), re.I) else None,
    "postal_code": lambda p: _re1(r"\b(\d{5,6})\b", _pre_paren(p.get("location")) or ""),
    "street_address": lambda p: None,  # never held as a fact today; report when required
    # 6+ years of professional experience is sufficient evidence of adulthood.
    "adult": lambda p: "Yes" if int(_re1(r"(\d+)\s*\+?\s*year", p.get("experience")) or 0) >= 6 else None,
}


def _derive(name: str, profile: dict[str, str]) -> str | None:
    fn = _DERIVERS.get(name)
    return fn(profile) if fn else None


def _link_of(kind: str, links: tuple[str, ...]) -> str | None:
    if kind == "linkedin":
        return next((u for u in links if "linkedin.com" in u.lower()), None)
    if kind == "github":
        return next((u for u in links if "github.com" in u.lower()), None)
    return next(  # portfolio: the first link that is neither linkedin nor github
        (u for u in links if "linkedin.com" not in u.lower() and "github.com" not in u.lower()),
        None,
    )


def _value_of(src: str, request: ApplyRequest) -> str | None:
    scope, _, name = src.partition(":")
    if scope == "profile":
        return (request.profile_facts.get(name, "") or "").strip() or None
    if scope == "pref":
        return (request.preferences.get(name, "") or "").strip() or None
    if scope == "derived":
        return _derive(name, request.profile_facts)
    if scope == "link":
        return _link_of(name, request.approved_links)
    if scope == "literal":
        return name
    return None


# ---------------------------------------------------------------------------
# Trap / eligibility predicates
# ---------------------------------------------------------------------------

_HP_ATTR = re.compile(
    r"(^|[^a-z0-9])("
    r"hp[_-]|honey-?pot|honey|hpcsaf|beecatcher|winnie|bot[_-]?(field|trap)|leave[_-]?(this|blank)"
    r")",
    re.I,
)
_HP_LABEL = re.compile(
    r"for robots only|do not enter if you'?re human|leave (this )?(field )?blank|honeypot", re.I
)
_WIDGET_ATTR = re.compile(r"captcha|turnstile|\bnonce\b", re.I)
_PAYLOAD_ATTR = re.compile(r"\bxml\b|resume-?text|resumetext|parsed[_-]?resume|\bjson\b", re.I)
_OTP_ATTR = re.compile(
    r"one[-_]?time|\botp\b|\btotp\b|security-input|verification[-_]?code|\bmfa\b", re.I
)
_FILLABLE_INPUT_TYPES = {"", "text", "email", "tel", "number", "url", "search", "date"}
_PLACEHOLDER_OPTION = re.compile(r"^\s*$|^-+$|^(please\s+)?(select|choose|pick)\b", re.I)


def _kind_of(el: ObservedElement) -> str | None:
    """Map an ObservedElement onto the rule vocabulary. None = not a question."""
    tag = el.tag.lower()
    typ = str(el.attributes.get("type", "")).lower()
    if tag == "textarea":
        return "textarea"
    if tag == "select":
        return "select_native"
    if (el.role or "").lower() == "combobox" or el.attributes.get("aria-autocomplete"):
        return "combobox"
    if tag == "input":
        if typ == "file":
            return "file"
        if typ == "checkbox":
            return "checkbox"
        if typ == "radio":
            return "radio_group"
        if typ == "date":
            return "datepicker_custom"
        if typ in _FILLABLE_INPUT_TYPES:
            return {"email": "email", "tel": "tel", "number": "number"}.get(typ, "text")
    # A drag-drop upload zone: a non-input control that announces an upload and
    # wraps a hidden file input the executor bridges to (dropzones outnumber
    # plain file inputs 20 to 3 on the corpus).
    if el.interactable:
        blob = f"{el.label} {el.text} {el.attributes.get('aria-label', '')} {el.attributes.get('class', '')}".lower()
        if re.search(
            r"drag.?(and.?)?drop|drop.?zone|upload (your |a )?(resume|cv|file|document)"
            r"|attach (your |a )?(resume|cv|file)|drag your (resume|cv|file)|choose (a )?file",
            blob,
        ):
            return "dragdrop_zone"
    return None


# ---------------------------------------------------------------------------
# One logical form question and the decider's cross-turn memory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    key: str
    kind: str
    label: str
    attr_hay: str
    trap_hay: str
    required: bool
    options: tuple[str, ...]
    elements: tuple[ObservedElement, ...]


@dataclass(frozen=True)
class _Pending:
    key: str
    value_regex: str


@dataclass
class DecideState:
    done: set[str] = dc_field(default_factory=set)
    filled: int = 0
    declined: int = 0
    reported: int = 0
    pending: _Pending | None = None
    rules: list[dict] | None = None


def _hay(el: ObservedElement) -> str:
    a = el.attributes
    return " ".join(str(a.get(k) or "") for k in ("name", "id", "autocomplete"))


def _trap_hay(el: ObservedElement, label: str) -> str:
    a = el.attributes
    extra = " ".join(str(a.get(k) or "") for k in ("data-automation-id", "class", "aria-label", "placeholder"))
    return f"{_hay(el)} {extra} {label}"


def _field_required(elements: tuple[ObservedElement, ...], label: str) -> bool:
    for el in elements:
        a = el.attributes
        if a.get("required") in (True, "true", ""):
            return True
        if a.get("aria-required") in (True, "true"):
            return True
    return re.search(r"[*✱]", label) is not None


def _field_key(el: ObservedElement, ordinal: int) -> str:
    a = el.attributes
    raw = "|".join(
        (
            str(el.frame_index),
            el.tag.lower(),
            str(a.get("type", "")).lower(),
            str(a.get("name", "")),
            str(a.get("id", "")),
            el.label[:80],
        )
    )
    return f"{raw}#{ordinal}"


def _collect_fields(obs: Observation) -> list[Field]:
    """Fold the flat element list into logical fields in document order.
    Radios and multi-checkboxes sharing a (frame, name) fold into one group."""
    fields: list[Field] = []
    seen_groups: set[str] = set()
    key_counts: dict[str, int] = {}
    # Pre-index checkbox names to detect groups (2+ share a name).
    checkbox_names: dict[tuple[int, str], int] = {}
    for el in obs.elements:
        if el.tag.lower() == "input" and str(el.attributes.get("type", "")).lower() == "checkbox":
            name = str(el.attributes.get("name") or "")
            if name:
                checkbox_names[(el.frame_index, name)] = checkbox_names.get((el.frame_index, name), 0) + 1

    for el in obs.elements:
        kind = _kind_of(el)
        if kind is None:
            continue
        name = str(el.attributes.get("name") or "")
        typ = str(el.attributes.get("type", "")).lower()

        group_kind = None
        if kind == "radio_group" and name:
            group_kind = "radio_group"
        elif typ == "checkbox" and name and checkbox_names.get((el.frame_index, name), 0) >= 2:
            group_kind = "checkbox_group"

        if group_kind is not None:
            gkey = f"{el.frame_index}|{group_kind}|{name}"
            if gkey in seen_groups:
                continue
            seen_groups.add(gkey)
            members = tuple(
                m
                for m in obs.elements
                if m.frame_index == el.frame_index
                and str(m.attributes.get("name") or "") == name
                and str(m.attributes.get("type", "")).lower() == typ
            )
            member_labels = tuple(m.label for m in members if m.label)
            humanized = re.sub(r"[_\[\]\-]+", " ", name).strip()
            label = f"{humanized} {' '.join(member_labels)}".strip()
            fields.append(
                Field(
                    key=f"{gkey}#0",
                    kind=group_kind,
                    label=label,
                    attr_hay=_hay(el),
                    trap_hay=_trap_hay(el, label),
                    required=_field_required(members, label),
                    options=member_labels,
                    elements=members,
                )
            )
            continue

        # A scalar field (a lone radio with no group folds to a 1-option group).
        raw = _field_key(el, 0).rsplit("#", 1)[0]
        ordinal = key_counts.get(raw, 0)
        key_counts[raw] = ordinal + 1
        label = f"{el.label} {el.text}".strip()
        if kind == "select_native":
            opts = el.options
        elif kind == "radio_group":
            opts = (el.label,) if el.label else ()
        else:
            opts = ()
        fields.append(
            Field(
                key=f"{raw}#{ordinal}",
                kind=kind,
                label=label,
                attr_hay=_hay(el),
                trap_hay=_trap_hay(el, label),
                required=_field_required((el,), label),
                options=opts,
                elements=(el,),
            )
        )
    return fields


def _prefilled(f: Field) -> bool:
    if f.kind in ("checkbox", "checkbox_group", "radio_group"):
        return any(m.attributes.get("checked") is True for m in f.elements)
    if f.kind == "select_native":
        sel = str(f.elements[0].attributes.get("selected") or "")
        return bool(sel.strip()) and not _PLACEHOLDER_OPTION.match(sel)
    val = f.elements[0].value or f.elements[0].attributes.get("value") or ""
    return bool(str(val).strip())


def _is_password_or_otp(f: Field) -> bool:
    el = f.elements[0]
    if str(el.attributes.get("type", "")).lower() == "password":
        return True
    if str(el.attributes.get("autocomplete", "")).lower() == "one-time-code":
        return True
    return _OTP_ATTR.search(f.attr_hay) is not None


def _is_hidden(f: Field) -> bool:
    el = f.elements[0]
    a = el.attributes
    if str(a.get("type", "")).lower() == "hidden":
        return True
    if a.get("aria-hidden") in (True, "true"):
        return True
    if not el.interactable:
        return True
    style = re.sub(r"\s+", "", str(a.get("style", ""))).lower()
    return "display:none" in style or "visibility:hidden" in style


def _is_trap(f: Field) -> bool:
    """Never fill a trap. Tiebreak (measured): a VISIBLE required field is never
    a honeypot. The hidden gate has already run, so a required-and-hidden mirror
    is gone before we get here."""
    if _WIDGET_ATTR.search(f.trap_hay) or _PAYLOAD_ATTR.search(f.trap_hay):
        return True
    if f.required:
        return False
    return bool(_HP_ATTR.search(f.trap_hay) or _HP_LABEL.search(f.label))


# ---------------------------------------------------------------------------
# Rule matching, option selection, combobox commit
# ---------------------------------------------------------------------------


def _matches(rule: dict, f: Field) -> bool:
    w = rule["when"]
    if "kind" in w and f.kind not in w["kind"]:
        return False
    if "label" in w and not re.search(w["label"], f.label, re.I):
        return False
    if "label_not" in w and re.search(w["label_not"], f.label, re.I):
        return False
    if "attr" in w and not re.search(w["attr"], f.attr_hay, re.I):
        return False
    if "attr_not" in w and re.search(w["attr_not"], f.attr_hay, re.I):
        return False
    if "required" in w and f.required != bool(w["required"]):
        return False
    return True


def _first_match(rules: list[dict], f: Field) -> dict | None:
    return next((r for r in rules if _matches(r, f)), None)


def _pick_option(options: tuple[str, ...], prefs: list[str]) -> str | None:
    for pat in prefs:
        for opt in options:
            if re.search(pat, opt, re.I):
                return opt
    return None


def _member_for_option(f: Field, option: str) -> ObservedElement:
    for m in f.elements:
        if m.label.strip() == option.strip():
            return m
    return f.elements[0]


def _find_open_option(obs: Observation, pending: _Pending) -> ObservedElement | None:
    for el in obs.elements:
        role = (el.role or "").lower()
        if role == "option" or el.tag.lower() == "option":
            if re.search(pending.value_regex, f"{el.label} {el.text}", re.I):
                return el
    return None


# ---------------------------------------------------------------------------
# Placeholder compilation (grounds knockout rules from the profile facts)
# ---------------------------------------------------------------------------

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def _compiled_rules(request: ApplyRequest, state: DecideState) -> list[dict]:
    if state.rules is not None:
        return state.rules
    profile = request.profile_facts
    home = _derive("home_country", profile)
    phone_cc = _derive("phone_cc", profile)
    subs = {
        "home_country": home,
        "phone_cc": phone_cc,
        "phone_cc_esc": re.escape(phone_cc) if phone_cc else None,
        "phone_cc_digits": re.sub(r"\D", "", phone_cc) if phone_cc else None,
    }

    def sub(text: str) -> str | None:
        out = text
        for name in _PLACEHOLDER.findall(text):
            val = subs.get(name)
            if val is None:
                return None  # signal: drop this rule
            out = out.replace("{" + name + "}", val)
        return out

    compiled: list[dict] = []
    for rule in RULES:
        new_when: dict = {}
        drop = False
        for k, v in rule["when"].items():
            if isinstance(v, str):
                s = sub(v)
                if s is None:
                    drop = True
                    break
                new_when[k] = s
            else:
                new_when[k] = v
        if drop:
            continue
        then = dict(rule["then"])
        if "option_prefs" in then:
            new_prefs = []
            for p in then["option_prefs"]:
                s = sub(p)
                if s is not None:
                    new_prefs.append(s)
            then["option_prefs"] = new_prefs
        compiled.append({"id": rule["id"], "when": new_when, "then": then})
    state.rules = compiled
    return compiled


# ---------------------------------------------------------------------------
# Resolution: (rule, field) -> Action, honestly
# ---------------------------------------------------------------------------


def _ungrounded(f: Field, state: DecideState) -> Action | None:
    state.declined += 1
    if f.required:
        state.reported += 1
        return Action(
            tool="report_blocked",
            args={
                "kind": "ungrounded_field",
                "detail": "no grounded answer for a required field",
                "field_label": (f.label or f.attr_hay)[:120],
            },
        )
    return None


def _resolve(rule: dict | None, f: Field, request: ApplyRequest, state: DecideState) -> Action | None:
    if rule is None:
        return _ungrounded(f, state)
    then = rule["then"]
    src = then["source"]

    if src == "leave_blank":
        state.declined += 1
        return None
    if src == "report":
        return _ungrounded(f, state)
    if src == "upload:resume":
        resume = next((a for a in request.artifacts if a.kind == "resume"), None)
        if resume is None:
            return _ungrounded(f, state)
        state.filled += 1
        return Action(
            tool="upload_artifact",
            args={"element_id": f.elements[0].element_id, "artifact_id": resume.artifact_id},
        )

    for req_key in then.get("requires", ()):
        scope, _, name = req_key.partition(":")
        pool = request.profile_facts if scope == "profile" else request.preferences
        if not (pool.get(name, "") or "").strip():
            return _ungrounded(f, state)

    value = _value_of(src, request)
    if src not in ("decline", "consent") and value is None:
        return _ungrounded(f, state)

    prefs = list(then.get("option_prefs", ()))
    if src == "decline":
        prefs = prefs or DECLINE_PREFS
    if src == "consent":
        prefs = prefs or YES_PREFS

    if f.kind == "checkbox":
        if src == "decline" or _NOISH.match(value or ""):
            state.declined += 1
            return None
        state.filled += 1
        return Action(tool="check", args={"element_id": f.elements[0].element_id})

    if f.kind in ("select_native", "radio_group", "checkbox_group"):
        if prefs:
            got = _pick_option(f.options, prefs)
        else:
            got = _pick_option(f.options, [r"^\s*" + re.escape(str(value)) + r"\s*$"])
        if got is None:
            return _ungrounded(f, state)
        if f.kind == "select_native":
            state.filled += 1
            return Action(tool="select", args={"element_id": f.elements[0].element_id, "option": got})
        member = _member_for_option(f, got)
        state.filled += 1
        return Action(tool="check", args={"element_id": member.element_id})

    # text / textarea / email / tel / number / combobox / datepicker_custom
    # A decline or consent source that reached a free-text field (a demographic
    # question rendered as a textarea, say) has no value to type: declining a
    # free-text field means leaving it blank, never typing the word "None".
    if value is None:
        state.declined += 1
        return None
    if f.kind == "combobox":
        state.pending = _Pending(f.key, re.escape(value)[:120])
    state.filled += 1
    return Action(tool="fill", args={"element_id": f.elements[0].element_id, "value": value[:4000]})


def decide_next(obs: Observation, request: ApplyRequest, state: DecideState) -> Action:
    """One turn: exactly one Action out."""
    rules = _compiled_rules(request, state)

    # 1. Combobox commit: last turn typed into a typeahead; click an open option.
    if state.pending is not None:
        target = _find_open_option(obs, state.pending)
        state.pending = None
        if target is not None:
            return Action(tool="click", args={"element_id": target.element_id})

    # 2/3. First eligible unfilled field wins; skips are silent.
    for f in _collect_fields(obs):
        if f.key in state.done:
            continue
        if _prefilled(f) or _is_password_or_otp(f) or _is_hidden(f) or _is_trap(f):
            state.done.add(f.key)
            continue
        rule = _first_match(rules, f)
        action = _resolve(rule, f, request, state)
        state.done.add(f.key)
        if action is not None:
            return action

    # 4. Nothing eligible left.
    extra = f" ({state.reported} reported)" if state.reported else ""
    return Action(
        tool="finish",
        args={"reason": f"deterministic fill complete; {state.filled} fields filled, {state.declined} declined{extra}"},
    )


class Decider:
    """The decider as a loop engine seam. Holds the per-run ``DecideState`` and
    exposes ``decide(obs, request) -> Action`` (the ``DeciderEngine`` protocol).
    Zero model spend."""

    def __init__(self) -> None:
        self.state = DecideState()

    def decide(self, obs: Observation, request: ApplyRequest) -> Action:
        return decide_next(obs, request, self.state)


# ---------------------------------------------------------------------------
# The rule table. First match wins; order is the contract. Placeholders
# {home_country}/{phone_cc_esc}/{phone_cc_digits} are compiled from the profile;
# a rule whose placeholder cannot be derived is dropped for the run.
# ---------------------------------------------------------------------------

TEXTY = ["text", "email", "tel", "number", "combobox", "other", "textarea", "select_native", "datepicker_custom"]
LOCY = ["text", "combobox", "select_native", "other", "number", "email", "tel"]

RULES: list[dict] = [
    # -- traps (belt; the _is_trap predicate is the braces and runs first) --
    {"id": "trap-honeypot",
     "when": {"kind": ["text", "textarea", "email", "tel", "number"],
              "attr": r"(^|[^a-z0-9])(hp[_-]|honey-?pot|honey|hpcsaf|beecatcher|winnie|bot[_-]?(field|trap)|leave[_-]?(this|blank))"},
     "then": {"source": "leave_blank"}},
    {"id": "trap-captcha", "when": {"attr": r"captcha|turnstile|\bnonce\b"}, "then": {"source": "leave_blank"}},
    {"id": "trap-serialized-payload",
     "when": {"attr": r"\bxml\b|resume-?text|resumetext|parsed[_-]?resume|\bjson\b"},
     "then": {"source": "leave_blank"}},

    # -- demographics / EEO: decline, never infer --
    {"id": "pronouns-attr", "when": {"attr": r"pronoun"}, "then": {"source": "leave_blank"}},
    {"id": "pronouns-label", "when": {"label": r"pronoun"}, "then": {"source": "leave_blank"}},
    {"id": "demo-checkbox-group-blank",
     "when": {"kind": ["checkbox", "checkbox_group"],
              "label": r"sexual orientation|\blgbt\b|transgender|gender identity|\bgender\b|hispanic|latin[oax]|\brace\b|ethnic|veteran|military service|armed forces|disabilit|\bdisabled\b|pronoun"},
     "then": {"source": "leave_blank"}},
    {"id": "demo-checkbox-attr-blank",
     "when": {"kind": ["checkbox", "checkbox_group"],
              "attr": r"\beeo\b|gender|\brace\b|ethnic|hispanic|veteran|military|disab|demograph|orientation|pronoun|self.?identif"},
     "then": {"source": "leave_blank"}},
    {"id": "demo-orientation", "when": {"label": r"sexual orientation|\blgbt|transgender|gender identity"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-gender-label", "when": {"label": r"\bgender\b|^\s*sex\s*$|identify my gender|what gender"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-gender-attr", "when": {"attr": r"gender|eeo.{0,3}sex"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-race-label", "when": {"label": r"hispanic|latino|latinx|\brace\b|ethnic"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-race-attr", "when": {"attr": r"\brace\b|ethnic|hispanic"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-veteran", "when": {"label": r"veteran|military service|armed forces|protected veteran"},
     "then": {"source": "decline", "requires": ("pref:demographics",),
              "option_prefs": DECLINE_PREFS + [r"^i am not a (protected )?veteran", r"^not a (protected )?veteran", r"i identify as not", r"^\s*no\s*$"]}},
    {"id": "demo-veteran-attr", "when": {"attr": r"veteran|military"},
     "then": {"source": "decline", "requires": ("pref:demographics",),
              "option_prefs": DECLINE_PREFS + [r"^i am not a (protected )?veteran", r"^not a (protected )?veteran", r"^\s*no\s*$"]}},
    {"id": "demo-disability", "when": {"label": r"disabilit|disabled|\bdisab"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-disability-attr", "when": {"attr": r"disab"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},
    {"id": "demo-dob", "when": {"label": r"date of birth|birth date|birthday|\bdob\b"}, "then": {"source": "report"}},
    {"id": "demo-age-range", "when": {"label": r"age range|age group|what is your age|which age"},
     "then": {"source": "decline", "requires": ("pref:demographics",)}},

    # -- names (preferred-name before first-name) --
    {"id": "middle-name", "when": {"label": r"middle name|middle initial"}, "then": {"source": "leave_blank"}},
    {"id": "first-name-attr",
     "when": {"kind": TEXTY, "attr": r"given-name|first[_ -]?name|firstname|\bfname\b", "attr_not": r"preferred|nick"},
     "then": {"source": "derived:first_name"}},
    {"id": "first-name-label",
     "when": {"kind": TEXTY, "label": r"^\s*\*?\s*first name|^\s*given name|^\s*legal first name", "label_not": r"preferred|nick"},
     "then": {"source": "derived:first_name"}},
    {"id": "preferred-name",
     "when": {"kind": TEXTY, "label": r"preferred (first )?name|nickname|goes by|name you go by"},
     "then": {"source": "derived:first_name"}},
    {"id": "preferred-name-attr", "when": {"kind": TEXTY, "attr": r"preferred[_ -]?name|nickname"},
     "then": {"source": "derived:first_name"}},
    {"id": "last-name-attr",
     "when": {"kind": TEXTY, "attr": r"family-name|last[_ -]?name|lastname|surname|\blname\b"},
     "then": {"source": "derived:last_name"}},
    {"id": "last-name-label",
     "when": {"kind": TEXTY, "label": r"^\s*\*?\s*last name|^\s*surname|^\s*family name|^\s*legal last name"},
     "then": {"source": "derived:last_name"}},
    {"id": "full-name-attr",
     "when": {"kind": TEXTY, "attr": r"(^|\s|\[)(name|cname|fullname|full[_-]name|legal[_-]?name|_systemfield_name)(\s|$|\])",
              "attr_not": r"first|last|middle|preferred|company|employer|school|user|file|reference|nick"},
     "then": {"source": "derived:full_name"}},
    {"id": "full-name-label",
     "when": {"kind": TEXTY, "label": r"^\s*\*?\s*(full|legal|candidate|your)?\s*name\b",
              "label_not": r"first|last|middle|preferred|nick|company|employer|school|university|file|user ?name|reference|manager|emergency|parent"},
     "then": {"source": "derived:full_name"}},

    # -- email / phone --
    {"id": "email-attr", "when": {"kind": TEXTY, "attr": r"e-?mail"}, "then": {"source": "derived:email"}},
    {"id": "email-label",
     "when": {"kind": ["email", "text"], "label": r"^\s*\*?\s*(confirm(ation)?( your)? )?e-?mail|e-?mail address"},
     "then": {"source": "derived:email"}},
    {"id": "phone-country-code",
     "when": {"kind": TEXTY, "label": r"(telephone|phone|dial|mobile|country) code"},
     "then": {"source": "derived:phone_cc", "option_prefs": [r"{phone_cc_esc}\b", r"\(\+?{phone_cc_digits}\)", r"^{home_country}\b"]}},
    {"id": "phone-country-code-attr",
     "when": {"kind": TEXTY, "attr": r"country.?code|dial.?code|phone.?prefix"},
     "then": {"source": "derived:phone_cc", "option_prefs": [r"{phone_cc_esc}\b", r"\(\+?{phone_cc_digits}\)", r"^{home_country}\b"]}},
    {"id": "phone-national-only", "when": {"kind": ["tel", "text", "number"], "label": r"\+\d{1,3}"},
     "then": {"source": "derived:phone_national"}},
    {"id": "phone-label",
     "when": {"kind": ["tel", "text", "number"], "label": r"phone|mobile|telephone|cell|contact number"},
     "then": {"source": "derived:phone_full"}},
    {"id": "phone-attr", "when": {"kind": TEXTY, "attr": r"\bphone\b|(^|\s)tel(\s|$)|mobile"},
     "then": {"source": "derived:phone_full"}},

    # -- location --
    {"id": "current-location-label",
     "when": {"kind": LOCY, "label": r"current location|^\s*\*?\s*location\b|location \(city\)|where are you (currently )?(based|located)|city and (state|country)|city, (state|province|country)"},
     "then": {"source": "derived:location_full"}},
    {"id": "current-location-attr",
     "when": {"kind": LOCY, "attr": r"(^|\s|\[|-)location(\s|$|\]|-)|candidate-location"},
     "then": {"source": "derived:location_full"}},
    {"id": "ashby-location-typeahead",
     "when": {"kind": ["combobox"], "label": r"^\s*start typing\.*\s*$",
              "attr_not": r"school|univ|college|degree|major|company|employer|title|source|referr"},
     "then": {"source": "derived:location_full"}},
    {"id": "city-attr", "when": {"kind": LOCY, "attr": r"(^|\s|\[|-)city(\s|$|\]|-)|\bcity\b|\btown\b"},
     "then": {"source": "derived:city"}},
    {"id": "city-label", "when": {"kind": LOCY, "label": r"^\s*\*?\s*city\b|^\s*town\b"}, "then": {"source": "derived:city"}},
    {"id": "state-attr", "when": {"kind": LOCY, "attr": r"\bstate\b|province|\bregion\b"}, "then": {"source": "derived:state"}},
    {"id": "state-label", "when": {"kind": LOCY, "label": r"^\s*\*?\s*(state|province|region)\b|state/province"},
     "then": {"source": "derived:state"}},
    {"id": "postal-code-attr", "when": {"kind": LOCY, "attr": r"postal|postcode|\bzip\b"}, "then": {"source": "derived:postal_code"}},
    {"id": "postal-code-label", "when": {"kind": LOCY, "label": r"postal code|post code|\bzip\b|pincode|pin code"},
     "then": {"source": "derived:postal_code"}},
    {"id": "street-address-line",
     "when": {"kind": LOCY, "label": r"address line|street address|^\s*street\b|apartment|suite|\bapt\b"},
     "then": {"source": "derived:street_address"}},
    {"id": "address-required-combined",
     "when": {"kind": ["text", "combobox"], "attr": r"(^|[\s\[])address(\s|$|\])", "required": True,
              "attr_not": r"e-?mail", "label_not": r"line ?[12]|street|apartment|\bapt\b|suite"},
     "then": {"source": "derived:location_full"}},
    {"id": "address-generic", "when": {"kind": LOCY, "attr": r"address", "attr_not": r"e-?mail"},
     "then": {"source": "derived:street_address"}},
    {"id": "country-attr", "when": {"kind": LOCY, "attr": r"(^|\s|\[|-)country(\s|$|\]|-)"}, "then": {"source": "derived:country"}},
    {"id": "country-label",
     "when": {"kind": LOCY, "label": r"^\s*\*?\s*country\b|country of (residence|origin)|nationality|which country do you (live|reside)"},
     "then": {"source": "derived:country"}},

    # -- links --
    {"id": "linkedin-label", "when": {"kind": TEXTY, "label": r"linked ?in"}, "then": {"source": "link:linkedin"}},
    {"id": "linkedin-attr", "when": {"kind": TEXTY, "attr": r"linked ?in"}, "then": {"source": "link:linkedin"}},
    {"id": "portfolio-attr", "when": {"kind": TEXTY, "attr": r"portfolio|personal ?site|personal ?web"}, "then": {"source": "link:portfolio"}},
    {"id": "portfolio-label", "when": {"kind": TEXTY, "label": r"portfolio|personal (web ?site|page|url)"}, "then": {"source": "link:portfolio"}},
    {"id": "github-label", "when": {"kind": TEXTY, "label": r"git ?hub"}, "then": {"source": "link:github"}},
    {"id": "github-attr", "when": {"kind": TEXTY, "attr": r"git ?hub"}, "then": {"source": "link:github"}},
    {"id": "social-none-held",
     "when": {"label": r"twitter|^\s*x \(|facebook|instagram|tiktok|dribbble|behance|stack ?overflow|^medium$|youtube|\bwechat\b|telegram"},
     "then": {"source": "leave_blank"}},
    {"id": "social-none-held-attr", "when": {"attr": r"twitter|facebook|instagram|tiktok|dribbble|behance"}, "then": {"source": "leave_blank"}},
    {"id": "other-url-blank", "when": {"label": r"^\s*other (web ?site|url|link|profile)|^\s*other\s*$"}, "then": {"source": "leave_blank"}},
    {"id": "other-url-blank-attr", "when": {"attr": r"urls?\[other\]"}, "then": {"source": "leave_blank"}},
    {"id": "website-label", "when": {"kind": TEXTY, "label": r"^\s*\*?\s*web ?site|homepage|personal url|^\s*blog\b|^\s*url\s*$"},
     "then": {"source": "link:portfolio"}},
    {"id": "website-attr", "when": {"kind": TEXTY, "attr": r"website|homepage"}, "then": {"source": "link:portfolio"}},

    # -- file uploads --
    {"id": "file-cover-letter", "when": {"kind": ["file", "dragdrop_zone", "other"], "label": r"cover.?letter"}, "then": {"source": "leave_blank"}},
    {"id": "file-cover-letter-attr", "when": {"kind": ["file", "dragdrop_zone", "other"], "attr": r"cover.?letter"}, "then": {"source": "leave_blank"}},
    {"id": "file-extra-documents",
     "when": {"kind": ["file", "dragdrop_zone", "other"], "label": r"additional|other (file|document)|transcript|portfolio|photo|picture|certificat|reference letter|writing sample"},
     "then": {"source": "leave_blank"}},
    {"id": "file-extra-documents-attr",
     "when": {"kind": ["file", "dragdrop_zone", "other"], "attr": r"additional|other[_-]?file|transcript|photo|avatar"},
     "then": {"source": "leave_blank"}},
    {"id": "file-resume", "when": {"kind": ["file", "dragdrop_zone", "other"], "label": r"resume|\bcv\b|curriculum vitae"}, "then": {"source": "upload:resume"}},
    {"id": "file-resume-attr", "when": {"kind": ["file", "dragdrop_zone", "other"], "attr": r"resume|\bcv\b|attachment"}, "then": {"source": "upload:resume"}},
    {"id": "file-required-unlabelled", "when": {"kind": ["file", "dragdrop_zone"], "required": True}, "then": {"source": "upload:resume"}},
    {"id": "file-optional-unlabelled", "when": {"kind": ["file", "dragdrop_zone"]}, "then": {"source": "report"}},

    # -- cover-letter prose --
    {"id": "cover-letter-text",
     "when": {"kind": ["textarea", "text"], "label": r"cover.?letter|motivation|why do you want to (work|join)|why are you interested|tell us about yourself|what interests you"},
     "then": {"source": "report", "requires": ("pref:cover letters",)}},
    {"id": "cover-letter-text-attr", "when": {"kind": ["textarea", "text"], "attr": r"cover.?letter"},
     "then": {"source": "report", "requires": ("pref:cover letters",)}},

    # -- knockouts: work authorization --
    {"id": "work-auth-status-freetext",
     "when": {"kind": ["textarea", "text"], "label": r"(work|employment) authori[sz]ation status|authori[sz]ation status|visa status|immigration status|work permit status"},
     "then": {"source": "profile:work authorization"}},
    {"id": "auth-home-yes",
     "when": {"label": r"(authori[sz]ed|authori[sz]ation|eligible|eligibility|permitted|legally able|legally entitled|right to work|work permit|lawfully).{0,100}\b{home_country}\b"},
     "then": {"source": "literal:Yes", "option_prefs": YES_PREFS, "requires": ("profile:work authorization",)}},
    {"id": "auth-named-foreign-checkbox",
     "when": {"kind": ["checkbox"],
              "label": r"(authori[sz]ed|authori[sz]ation|eligible|eligibility|permitted|legally able|legally entitled|right to work|work permit|lawfully).{0,120}(united states|\bu\.s\.a?\b|\busa\b|\bin the us\b|\bthe us\b|america|canada|united kingdom|\buk\b|ireland|germany|france|netherlands|australia|singapore|\buae\b|dubai|european union|\beu\b|h-?1b|green card)",
              "label_not": r"(requir|need)\w*.{0,40}(sponsor|immigration)|\b{home_country}\b"},
     "then": {"source": "leave_blank"}},
    {"id": "auth-named-foreign",
     "when": {"label": r"(authori[sz]ed|authori[sz]ation|eligible|eligibility|permitted|legally able|legally entitled|right to work|work permit|lawfully).{0,120}(united states|\bu\.s\.a?\b|\busa\b|\bin the us\b|\bthe us\b|america|canada|united kingdom|\buk\b|ireland|germany|france|netherlands|australia|singapore|\buae\b|dubai|european union|\beu\b|h-?1b|green card)",
              "label_not": r"(requir|need)\w*.{0,40}(sponsor|immigration)|\b{home_country}\b"},
     "then": {"source": "literal:No", "option_prefs": NO_PREFS, "requires": ("profile:work authorization",)}},
    {"id": "auth-country-unnamed",
     "when": {"label": r"authori[sz]ed to work|authori[sz]ed for employment|work authori[sz]ation|right to work|legally (able|entitled|eligible) to work|eligible to work",
              "label_not": r"(requir|need)\w*.{0,40}(sponsor|immigration)"},
     "then": {"source": "report"}},

    # -- knockouts: sponsorship --
    {"id": "sponsorship-home-no",
     "when": {"label": r"(sponsor|immigration|work permit|visa).{0,100}\b{home_country}\b"},
     "then": {"source": "literal:No", "option_prefs": NO_PREFS, "requires": ("profile:work authorization",)}},
    {"id": "sponsorship-named-foreign-checkbox",
     "when": {"kind": ["checkbox"],
              "label": r"(requir|need|seek)\w*.{0,40}(sponsor|immigration|visa|work permit).{0,120}(united states|\busa\b|\bthe us\b|america|canada|united kingdom|\buk\b|australia|singapore|european union|\beu\b|h-?1b|green card)",
              "label_not": r"\b{home_country}\b"},
     "then": {"source": "literal:Yes", "requires": ("profile:work authorization",)}},
    {"id": "sponsorship-named-foreign",
     "when": {"label": r"(requir|need|seek)\w*.{0,40}(sponsor|immigration|visa|work permit).{0,120}(united states|\busa\b|\bthe us\b|america|canada|united kingdom|\buk\b|australia|singapore|european union|\beu\b|h-?1b|green card)",
              "label_not": r"\b{home_country}\b"},
     "then": {"source": "literal:Yes", "option_prefs": YES_PREFS + [r"^other$"], "requires": ("profile:work authorization",)}},
    {"id": "sponsorship-country-unnamed",
     "when": {"label": r"sponsor|immigration (support|assistance|case|status)|visa status|require .{0,20}visa|work permit"},
     "then": {"source": "report"}},

    # -- citizenship --
    {"id": "citizenship-text",
     "when": {"kind": ["text", "textarea"], "label": r"^\s*\*?\s*citizenship\b|citizenship status|country of citizenship|what is your citizenship"},
     "then": {"source": "derived:citizenship"}},
    {"id": "citizenship-status-list",
     "when": {"kind": ["select_native", "radio_group", "combobox", "checkbox_group", "other"], "label": r"citizenship status|^\s*citizenship\b|immigration status"},
     "then": {"source": "derived:citizenship",
              "option_prefs": [r"(^|\W){home_country}", r"^\(f\)\s*other", r"^\(?[a-f]\)?\.?\s*other", r"^other$", r"none of (the|these)", r"not a (u\.?s\.?|united states) (citizen|person)"]}},

    # -- prior employment / referral honesty --
    {"id": "worked-here-before-checkbox",
     "when": {"kind": ["checkbox"], "label": r"(ever|previously|before|in the past).{0,40}(employed|worked|work for)|(employed|worked) (by|for|at) (us|our|this|the) (company|organi|firm|team)|former (employee|contractor|intern)|(are|were) you a (former|current|previous) employee|previous employment with"},
     "then": {"source": "leave_blank"}},
    {"id": "worked-here-before",
     "when": {"label": r"(ever|previously|before|in the past).{0,40}(employed|worked|work for)|(employed|worked) (by|for|at) (us|our|this|the) (company|organi|firm|team)|former (employee|contractor|intern)|(are|were) you a (former|current|previous) employee|previous employment with"},
     "then": {"source": "literal:No", "option_prefs": NO_PREFS + [r"^never", r"have not"], "requires": ("profile:relatives and prior employment",)}},
    {"id": "relatives-at-company-checkbox",
     "when": {"kind": ["checkbox"], "label": r"relative|family member|immediate family|friends or family"}, "then": {"source": "leave_blank"}},
    {"id": "relatives-at-company",
     "when": {"label": r"relative|family member|immediate family|friends or family"},
     "then": {"source": "literal:No", "option_prefs": NO_PREFS, "requires": ("profile:relatives and prior employment",)}},

    # -- how did you hear --
    {"id": "how-did-you-hear",
     "when": {"label": r"how did you (first )?(hear|find|learn|come across)|where did you (hear|find|learn|see)|how were you (referred|introduced)|referral source|source of (your )?application|what brought you to|how do you know about"},
     "then": {"source": "literal:LinkedIn", "requires": ("pref:how heard about the job",),
              "option_prefs": [r"^linkedin$", r"^linkedin\b(?!.*(referral|referred|recruiter|message|inmail|employee))", r"job board", r"job (site|posting|search)", r"^indeed$", r"^other$", r"^other\b"]}},
    {"id": "how-did-you-hear-attr",
     "when": {"attr": r"how.?did.?you.?hear|source_?of_?(application|hire)|referral_?source"},
     "then": {"source": "literal:LinkedIn", "requires": ("pref:how heard about the job",),
              "option_prefs": [r"^linkedin$", r"^linkedin\b(?!.*(referral|referred|recruiter|message|inmail|employee))", r"job board", r"job (site|posting|search)", r"^indeed$", r"^other$", r"^other\b"]}},
    {"id": "how-did-you-hear-detail",
     "when": {"label": r"which specific (channel|event|medium)|specify .{0,30}(channel|event|medium|where you heard)|please (specify|tell us) (where|how) you (heard|found)"},
     "then": {"source": "literal:LinkedIn job posting", "requires": ("pref:how heard about the job",)}},
    {"id": "referrer-name", "when": {"label": r"(name|who).{0,30}refer|referrer|referred you.{0,20}name"}, "then": {"source": "leave_blank"}},
    {"id": "were-you-referred",
     "when": {"label": r"refer(red)? (you|by)|did (someone|anyone).{0,40}refer|were you referred|are you being referred"},
     "then": {"source": "literal:No", "option_prefs": NO_PREFS, "requires": ("pref:how heard about the job",)}},

    # -- current employment --
    {"id": "current-company",
     "when": {"kind": TEXTY, "label": r"current (or most recent )?(company|employer)|current ?/ ?(last|most recent) company|most recent (company|employer)|^\s*\*?\s*company\b|current employer|present employer|employer name"},
     "then": {"source": "derived:current_company"}},
    {"id": "current-company-attr",
     "when": {"kind": TEXTY, "attr": r"(^|\s|\[)org(\s|$|\])|current_?(company|employer)|\bemployer\b"},
     "then": {"source": "derived:current_company"}},
    {"id": "current-title",
     "when": {"kind": TEXTY, "label": r"current (or most recent )?(job )?title|current ?/ ?(last|most recent) title|most recent (job )?title|^\s*\*?\s*(job )?title\b|current (role|position)|present position"},
     "then": {"source": "derived:current_title"}},
    {"id": "currently-employed",
     "when": {"label": r"are you (currently )?employed|current(ly)? employment status|employment status|are you working"},
     "then": {"source": "literal:Yes", "option_prefs": YES_PREFS + [r"self.?employed", r"^employed"], "requires": ("profile:current role",)}},

    # -- experience / salary --
    {"id": "years-total",
     "when": {"label": r"years of (professional |total |overall |work |industry |relevant )?experience( do you have)?\s*[\?\.\*✱: ]*$|(total|overall) (number of )?years|how many years.{0,25}(software|engineering|programming|development|technical) experience|years of (software|engineering|programming|development) experience"},
     "then": {"source": "derived:years_total", "option_prefs": [r"^9\b", r"8-10|7-10|5-10|6-10", r"5\+|7\+|8\+", r"more than (5|7)"]}},
    {"id": "current-salary-decline",
     "when": {"label": r"current (salary|compensation|ctc|pay|rate|base)|present salary|last drawn|salary history|most recent (salary|compensation)|existing salary|current annual"},
     "then": {"source": "report"}},
    {"id": "salary-currency-attr", "when": {"attr": r"currency"},
     "then": {"source": "literal:USD", "requires": ("profile:salary expectation",), "option_prefs": [r"^us dollar", r"^usd\b", r"united states dollar", r"\(usd\)"]}},
    {"id": "salary-currency-label", "when": {"label": r"^\s*currency\b|salary currency|pay currency"},
     "then": {"source": "literal:USD", "requires": ("profile:salary expectation",), "option_prefs": [r"^us dollar", r"^usd\b", r"united states dollar", r"\(usd\)"]}},
    {"id": "salary-period",
     "when": {"kind": ["select_native", "radio_group", "combobox"], "label": r"salary|compensation|desired pay|pay rate"},
     "then": {"source": "literal:Yearly", "requires": ("profile:salary expectation",), "option_prefs": [r"^yearly$", r"^annual(ly)?$", r"^per year$", r"^year$", r"^annual salary$"]}},
    {"id": "salary-local-unit", "when": {"label": r"\binr\b|₹|\blpa\b|lakh|\bctc\b|rupee"}, "then": {"source": "derived:salary_year_inr"}},
    {"id": "salary-monthly",
     "when": {"label": r"monthly (salary|compensation|pay|rate|expectation)|salary.{0,20}per month|per month.{0,20}salary|month(ly)? (expectation|expected)"},
     "then": {"source": "derived:salary_month_usd"}},
    {"id": "salary-expectation",
     "when": {"kind": ["text", "textarea", "number", "combobox"], "label": r"salary|compensation|desired pay|expected (pay|remuneration)|pay expectation|remuneration|what are your (salary|pay)"},
     "then": {"source": "derived:salary_year_usd"}},

    # -- availability --
    {"id": "notice-period-number", "when": {"kind": ["number"], "label": r"notice"}, "then": {"source": "derived:notice_days"}},
    {"id": "notice-period",
     "when": {"label": r"notice period|period of notice|how much notice|notice you (need|must) (give|provide)|current notice"},
     "then": {"source": "derived:notice_days", "option_prefs": [r"immediat", r"^\s*0\b", r"^none\b", r"^asap", r"less than (a|1|one) week", r"^1 week|^one week"]}},
    {"id": "start-availability",
     "when": {"label": r"when (can|could|are|would) you (be able to )?(start|join|begin|availab)|earliest.{0,25}(start|join|availab)|available to start|availability to start|how soon can you (start|join)|start availability|(desired|preferred|anticipated|target) start date|^\s*\*?\s*start date",
              "label_not": r"(month|year)\b|school|degree|university|education|internship"},
     "then": {"source": "derived:start_when", "option_prefs": [r"immediat", r"^asap", r"^\s*0\b", r"less than (a|1|one) week", r"^1 week|^two weeks|^2 weeks"]}},

    # -- over-18 (evidence-derived, never a blind tick) --
    {"id": "age-18-checkbox",
     "when": {"kind": ["checkbox"], "label": r"(at least|over|aged?) (18|eighteen)|(18|eighteen) (years )?(of age )?or (older|above|over)|legal working age", "label_not": r"under (18|eighteen)"},
     "then": {"source": "derived:adult"}},
    {"id": "age-18-list",
     "when": {"label": r"(at least|over|aged?) (18|eighteen)|(18|eighteen) (years )?(of age )?or (older|above|over)|legal working age", "label_not": r"under (18|eighteen)"},
     "then": {"source": "derived:adult", "option_prefs": YES_PREFS}},

    # -- consents (marketing declined before the data-consent catch-all) --
    {"id": "marketing-consent-attr", "when": {"attr": r"marketing|newsletter|subscribe|promo|sms.?consent|smsconsent|text.?message"}, "then": {"source": "leave_blank"}},
    {"id": "marketing-consent",
     "when": {"label": r"future (job )?(opportunit|opening|role|position)|marketing (email|communication|consent|purpose|material)|consent.{0,40}marketing|receive.{0,40}(marketing|promotional|sms|text message)|newsletter|talent (pool|community|network)|other (roles|positions|opportunities)|keep me (informed|updated|posted)|email me about|subscribe|contact me about (future|other)"},
     "then": {"source": "leave_blank"}},
    {"id": "data-consent-checkbox",
     "when": {"kind": ["checkbox"], "label": r"consent|privacy (notice|policy|statement)|terms|process(ing)? (of )?my|retain my data|data (processing|retention|protection)|\bgdpr\b|i (agree|have read|acknowledge|confirm|understand|certify)",
              "label_not": r"authoriz|sponsor|visa|citizen|felony|criminal|drug|non.?compete|18|veteran|disab|gender|race"},
     "then": {"source": "consent", "requires": ("pref:consents",)}},
    {"id": "data-consent-checkbox-attr", "when": {"kind": ["checkbox"], "attr": r"\bgdpr\b|consent|privacy|terms|policy"},
     "then": {"source": "consent", "requires": ("pref:consents",)}},
    {"id": "data-consent-list",
     "when": {"label": r"consent to (the )?(process|retain|storage|use)|privacy (notice|policy)|do you (agree|consent)|data protection"},
     "then": {"source": "consent", "option_prefs": YES_PREFS + [r"^i (agree|consent)", r"^accept"], "requires": ("pref:consents",)}},

    # -- terminal honesty (last) --
    {"id": "conditional-explain-other",
     "when": {"label": r"^\s*if\b.{0,40}(please )?(explain|specify|describe|provide)|if (yes|no|other)[,:]? (please )?(explain|specify|describe)"},
     "then": {"source": "report"}},
    {"id": "date-placeholder-only",
     "when": {"kind": ["datepicker_custom", "text"], "label": r"^\s*(pick|select|choose|enter)\s*(a\s*)?date\.*\s*$|^\s*(mm|dd|yyyy)\s*[\/\-]"},
     "then": {"source": "report"}},
    {"id": "describe-prose",
     "when": {"kind": ["text", "textarea"], "label": r"(briefly )?describe|tell us (about|why|how)|in your own words|what (motivates|excites|interests) you|share (an )?example|walk us through"},
     "then": {"source": "report"}},
    {"id": "bare-response-box",
     "when": {"kind": ["text", "textarea"], "label": r"^\s*(type your response|your (answer|response)|please (answer|respond|specify|explain)|response|answer)\s*[\.:\*✱]*\s*$"},
     "then": {"source": "report"}},
    {"id": "free-text-fallthrough", "when": {"kind": ["textarea"]}, "then": {"source": "report"}},
    {"id": "label-stem-missing",
     "when": {"kind": ["radio_group", "checkbox_group", "select_native", "checkbox", "combobox"], "label_not": r"\byou\b|\byour\b|\byourself\b"},
     "then": {"source": "report"}},
]
