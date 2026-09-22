"""Shim de compatibilidad: la implementacion vive en ``examples/auditor/evaluate.py``.

Mantiene ``python skills/pplx_auditor/evaluate.py ...`` funcionando.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from examples.auditor.evaluate import (  # noqa: E402,F401
    DEFAULT_OUT,
    DEFAULT_URL,
    load_rubric,
    main,
    run_evaluate,
)

__all__ = ["DEFAULT_OUT", "DEFAULT_URL", "load_rubric", "main", "run_evaluate"]


if __name__ == "__main__":
    sys.exit(main())
