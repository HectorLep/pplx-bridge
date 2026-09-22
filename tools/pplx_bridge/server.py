"""Shim de compatibilidad: la implementacion vive en ``core_bridge.server``.

Mantiene ``python tools/pplx_bridge/server.py`` y
``uvicorn tools.pplx_bridge.server:app`` funcionando (el runner de
auditorias y el Dockerfile usan esa ruta).
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core_bridge.server import (  # noqa: E402,F401
    DEFAULT_MODEL,
    ChatCompletionRequest,
    ChatMessage,
    EvaluateRequest,
    app,
    evaluate,
    chat_completions,
    health,
    list_models,
    messages_to_prompt,
)
from core_bridge.browser import CircuitBreakerOpenError  # noqa: E402,F401

__all__ = [
    "DEFAULT_MODEL",
    "ChatCompletionRequest",
    "ChatMessage",
    "CircuitBreakerOpenError",
    "EvaluateRequest",
    "app",
]


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("PPLX_HOST", "127.0.0.1"),
        port=int(os.environ.get("PPLX_PORT", "8000")),
    )
