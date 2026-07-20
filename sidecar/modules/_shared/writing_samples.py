"""Writing-samples loader shared by module silos (extracted from the Tailorer at
the second consumer, the CoverLetterer, per the M1 playbook)."""

from __future__ import annotations

from pathlib import Path


def load_writing_samples(samples_dir: Path | None) -> list[tuple[str, str]]:
    """Read writing samples, skipping README files (mirrors career-ops _shared.md)."""
    if samples_dir is None or not samples_dir.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for p in sorted(samples_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in {".md", ".txt"} and p.name.lower() != "readme.md":
            out.append((p.name, p.read_text(encoding="utf-8")))
    return out
