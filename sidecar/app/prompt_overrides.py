"""User-editable LLM prompts: file overrides in the app-data dir.

Each module's skill markdown is editable in Settings (architecture section 11);
a passed `skill_md`/`system_prompt` replaces the default. The app owns storage:
`<data_dir>/prompts/<kind>.md`, deleted by Reset. Each kind's default is the
module's own `load_skill()` (or the profiler's `SYSTEM_PROMPT`)."""

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
    # The `LLM_KINDS` operation this prompt runs as. It differs from `kind` for
    # exactly one prompt: `networker_draft` runs as the `draft` operation, and
    # looking its engine up by the prompt name found nothing, which is why the
    # referral model had no picker anywhere (url-less-jobs.md section 11.5).
    op_kind: str
    default_md: str
    override_md: str | None


@dataclass(frozen=True)
class PromptKind:
    """One editable prompt: its stable id, human title, and default-text loader.

    Every prompt routes through the engine registry; `op_kind` names which
    `LLM_KINDS` operation it runs as."""

    kind: str
    title: str
    default_loader: Callable[[], str]
    # Defaults to `kind`; set only where the prompt's name and its operation
    # kind differ (see PromptRow.op_kind).
    op_kind: str = ""

    def operation_kind(self) -> str:
        return self.op_kind or self.kind


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


# The registry. Order used to be the Settings display order; since the 2026-10
# split each prompt is rendered by the feature category that owns it, so this
# backend list no longer dictates a frontend layout.
PROMPT_KINDS: dict[str, PromptKind] = {
    p.kind: p
    for p in (
        # "AI scoring", not "Job scoring": 3 of the 4 scoring methods never see
        # this file. Keyword scoring is Python rules, Fast scoring sends typed
        # questions with their own rubric bands, and On-device has no prompt
        # either. Naming it after the job rather than the method told the user
        # it governed all 4 (maintainer, 2026-10-07).
        PromptKind("score", "AI scoring", _score_default),
        PromptKind("tailor", "Resume tailoring", _tailor_default),
        PromptKind("cover", "Cover letter", _cover_default),
        PromptKind("extract", "Application profile extraction", _extract_default),
        PromptKind("networker_draft", "Referral message drafting",
                   _networker_draft_default, op_kind="draft"),
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
                op_kind=kind.operation_kind(),
                default_md=kind.default_loader(),
                override_md=get_override(kind.kind, data_dir),
            )
        )
    return rows
