"""finds-you-jobs Tailorer module (silo, Track M1 — docs/ROADMAP.md §5)."""

from .tailorer import tailor
from .types import TailorError, TailorResult, Usage

__all__ = ["TailorError", "TailorResult", "Usage", "tailor"]
