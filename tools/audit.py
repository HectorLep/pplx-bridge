"""Shim de compatibilidad en ``tools/`` para la auditoria LexiEngine.

Reexporta la implementacion de ``examples/auditor/audit.py`` para que
``python tools/audit.py`` y ``from tools.audit import ...`` sigan
funcionando.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from examples.auditor.audit import (  # noqa: E402,F401
    BRIDGE_URL,
    MAX_PROMPT_CHARS,
    TARGET_FILES,
    build_prompt,
    count_tests,
    file_api_summary,
    get_code,
    main,
    measure_latency,
    run_audit,
)
from examples.auditor.prompts import extract_score  # noqa: E402,F401

__all__ = [
    "BRIDGE_URL",
    "MAX_PROMPT_CHARS",
    "TARGET_FILES",
    "build_prompt",
    "count_tests",
    "extract_score",
    "file_api_summary",
    "get_code",
    "main",
    "measure_latency",
    "run_audit",
]


if __name__ == "__main__":
    raise SystemExit(main())
