"""Session-wide test isolation.

Redirects the app-data + log directories to a throwaway tmp dir *at import
time* (before any test or spawned subprocess reads them) so tests never touch
the real platform app-data location. `setdefault` lets an explicit env override
still win. Applies to the boot subprocess too, which inherits this environment.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="fyj-tests-"))
os.environ.setdefault("FYJ_DATA_DIR", str(_ROOT / "data"))
os.environ.setdefault("FYJ_LOG_DIR", str(_ROOT / "logs"))
