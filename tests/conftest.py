"""Shared pytest fixtures.

Adds the training-service and shared directories to sys.path so tests can
`from training_service.main import app` and `from shared.types import ...`
without an editable install. Mirror this in CI: install in editable mode
instead of relying on the path injection.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for sub in ("training-service", "runtime-engine", "."):
    # adds: training_service/, runtime_engine/, shared/
    p = _ROOT / sub
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))
