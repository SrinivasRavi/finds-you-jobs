"""The Profiler black box: extract_profile(master_md) → ProfileResult.

One bounded operation (ROADMAP §4): one LLM call that reads the master resume
and returns the structured application-profile record the Applier fills forms
from (FR-APP-01, 2026-07-11). The grounding rule is absolute — every value
must appear in the resume; anything absent stays empty. The record is
user-editable in Settings after extraction, so precision beats recall here.
"""

from __future__ import annotations

import json
import re

from .engine import ClaudeCliEngine, Engine
from .types import ProfileError, ProfileResult

SYSTEM_PROMPT = """\
You extract job-application form-fill facts from a resume.

Return ONLY a JSON object (no prose, no code fences) with exactly these keys:
  name, first_name, last_name, email, phone, location, country,
  work_authorization, links, education

Rules — these are absolute:
- Every value must be copied or directly derived from the resume text. If the
  resume does not state it, use "" (or [] / {} for lists/objects). NEVER guess,
  infer from stereotypes, or invent.
- phone: verbatim as written (keep +country prefixes); "" if absent.
- location: the person's city/region line as written; country: the country
  name only, derived from the location if unambiguous, else "".
- work_authorization: only if the resume explicitly states a visa/work-auth
  status, verbatim; else "".
- links: an object mapping lowercase labels to URLs, e.g.
  {"linkedin": "...", "github": "...", "portfolio": "..."}. Only URLs present
  in the resume.
- education: an array, most recent first, of
  {"school": "", "degree": "", "discipline": "", "start_year": "", "end_year": ""}
  — years as 4-digit strings when stated, else "".
"""

_KEYS_STR = ("name", "first_name", "last_name", "email", "phone",
             "location", "country", "work_authorization")


def _first_json_object(text: str) -> dict:
    """Parse the first {...} object in `text` (tolerates fences/prose)."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = cleaned.find("{")
    if start == -1:
        raise ProfileError("parse", f"no JSON object in engine output: {text[:200]!r}")
    decoder = json.JSONDecoder()
    try:
        obj, _end = decoder.raw_decode(cleaned[start:])
    except json.JSONDecodeError as e:
        raise ProfileError("parse", f"invalid JSON from engine: {e}") from e
    if not isinstance(obj, dict):
        raise ProfileError("parse", "engine output is not a JSON object")
    return obj


def normalize_profile(raw: dict) -> dict:
    """Every key present, every type right — empty over invented (grounding)."""
    profile: dict = {k: str(raw.get(k) or "").strip() for k in _KEYS_STR}
    links = raw.get("links") or {}
    profile["links"] = {
        str(k).strip().lower(): str(v).strip()
        for k, v in (links.items() if isinstance(links, dict) else [])
        if str(v).strip()
    }
    education = raw.get("education") or []
    profile["education"] = [
        {
            "school": str(e.get("school") or "").strip(),
            "degree": str(e.get("degree") or "").strip(),
            "discipline": str(e.get("discipline") or "").strip(),
            "start_year": str(e.get("start_year") or "").strip(),
            "end_year": str(e.get("end_year") or "").strip(),
        }
        for e in (education if isinstance(education, list) else [])
        if isinstance(e, dict) and str(e.get("school") or "").strip()
    ]
    return profile


def extract_profile(
    master_md: str, engine: Engine | None = None, system_prompt: str | None = None
) -> ProfileResult:
    """Extract the structured application profile from the master resume.

    `system_prompt`, when provided, replaces the built-in `SYSTEM_PROMPT` (the
    app's user-editable-prompt override, §5). None → the default."""
    if not master_md.strip():
        raise ProfileError("input", "master resume is empty — nothing to extract")
    engine = engine or ClaudeCliEngine()
    raw, usage = engine.complete(
        system_prompt if system_prompt is not None else SYSTEM_PROMPT, master_md
    )
    profile = normalize_profile(_first_json_object(raw))
    return ProfileResult(profile=profile, usage=usage)
