"""Shim de compatibilidad: la logica vive en ``examples/auditor/audit.py``.

Este modulo mantiene la API historica de ``audit.py`` en la raiz para no
romper scripts existentes (``python audit.py``, ``from audit import ...``).
"""

from __future__ import annotations

from examples.auditor.audit import (  # noqa: F401
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
from examples.auditor.prompts import extract_score  # noqa: F401

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
