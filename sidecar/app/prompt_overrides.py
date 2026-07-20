"""User-editable LLM prompts — file-based overrides in the app-data dir.

Every module's "skill" markdown (the system prompt) is exposed in Settings,
editable, and persisted across sessions (architecture §11). The module seam is
a `skill_md` / `system_prompt` parameter on each bounded op (§5): when the app
passes an override, it replaces the module's on-disk default; absent, behavior
is exactly as before.

**The app layer owns storage** (§4.0/§5 — modules never read app storage). An
override is a plain file at `<data_dir>/prompts/<kind>.md`; Reset deletes it. No
DB migration. Each kind's *default* text is the module's own `load_skill()` (or
the profiler's `SYSTEM_PROMPT`), so the wire's `default_md` and Reset both reflect
whatever the shipped skill file currently says.

The prior repository also registers `prep` (retired in this rebuild —
`docs/internal/applier.md` §2) and `networker_draft` (returns with the Referral
Outreach commits).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from .db.database import resolve_data_dir


class PromptRow(TypedDict):
    """The wire shape one editable prompt serializes to (→ dto.PromptDTO)."""

    kind: str
    title: str
    routed: bool
    default_md: str
    override_md: str | None


@dataclass(frozen=True)
class PromptKind:
    """One editable prompt: its stable id, human title, and default-text loader.

    `routed` marks the kinds that also carry an engine/model selector — the
    `LLM_KINDS` routed through the engine registry."""

    kind: str
    title: str
    default_loader: Callable[[], str]
    routed: bool = True


def _tailor_default() -> str:
    from sidecar.modules.tailorer.prompt import load_skill

    return load_skill()


def _score_default() -> str:
    from sidecar.modules.scorer.prompt import load_skill

    return load_skill()


def _cover_default() -> str:
    from sidecar.modules.coverletterer.prompt import load_skill

    return load_skill()


def _extract_default() -> str:
    from sidecar.modules.profiler import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _networker_draft_default() -> str:
    from sidecar.modules.networker.prompt import load_skill

    return load_skill()


# The registry — order is the Settings display order. `networker_draft` is
# prompt-only (not a routed LLM kind — `routed=False`).
PROMPT_KINDS: dict[str, PromptKind] = {
    p.kind: p
    for p in (
        PromptKind("score", "Job scoring", _score_default),
        PromptKind("tailor", "Resume tailoring", _tailor_default),
        PromptKind("cover", "Cover letter", _cover_default),
        PromptKind("extract", "Application profile extraction", _extract_default),
        PromptKind("networker_draft", "Referral message drafting",
                   _networker_draft_default, routed=False),
    )
}


class UnknownPromptKind(KeyError):
    """Asked for a prompt kind that isn't registered."""


def default_md(kind: str) -> str:
    """The shipped default text for `kind` (what Reset restores)."""
    spec = PROMPT_KINDS.get(kind)
    if spec is None:
        raise UnknownPromptKind(kind)
    return spec.default_loader()


def _override_path(kind: str, data_dir: Path | None = None) -> Path:
    return resolve_data_dir(data_dir) / "prompts" / f"{kind}.md"


def get_override(kind: str, data_dir: Path | None = None) -> str | None:
    """The saved override markdown for `kind`, or None when unset (→ default).

    The entrypoints call this and pass the result straight to the module seam:
    None means "use the module default", so the no-override path is byte-for-byte
    the pre-feature behavior."""
    path = _override_path(kind, data_dir)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def set_override(kind: str, markdown: str, data_dir: Path | None = None) -> None:
    path = _override_path(kind, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def reset(kind: str, data_dir: Path | None = None) -> None:
    """Drop the override for `kind` — the module default applies again."""
    path = _override_path(kind, data_dir)
    path.unlink(missing_ok=True)


def list_prompts(data_dir: Path | None = None) -> list[PromptRow]:
    """Every editable prompt with its default + current override (the Settings
    prompts panel payload)."""
    rows: list[PromptRow] = []
    for kind in PROMPT_KINDS.values():
        rows.append(
            PromptRow(
                kind=kind.kind,
                title=kind.title,
                routed=kind.routed,
                default_md=kind.default_loader(),
                override_md=get_override(kind.kind, data_dir),
            )
        )
    return rows
