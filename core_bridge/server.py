"""Servidor FastAPI del puente: API generica OpenAI-compatible.

Endpoints:

- ``GET /health``: estado del proveedor por defecto y del circuit breaker.
- ``GET /v1/models``: modelos activos registrados (formato OpenAI ``list``).
- ``POST /v1/chat/completions``: formato estandar de mensajes OpenAI::

      {"model": "perplexity-web",
       "messages": [{"role": "user", "content": "Hola"}]}

  El campo ``model`` se resuelve contra el registro de proveedores
  (``core_bridge.providers``); ``perplexity-web`` es el modelo por defecto.

- ``POST /v1/evaluate``: consulta generica con adjuntos nativos::

      {"query": "<prompt/instrucciones>",
       "attachments": ["src/engine/trie.py"],
       "timeout_s": 180}

  Se aceptan los alias legacy ``prompt`` y ``files`` para no romper los
  scripts existentes; la respuesta incluye ``response`` (generico) y
  ``answer`` (alias legacy).

Arranque::

    python -m core_bridge.cli start --host 127.0.0.1 --port 8000
    uvicorn core_bridge.server:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

try:
    from core_bridge.browser import ASK_TIMEOUT_S, CircuitBreakerOpenError
    from core_bridge.providers import (
        DEFAULT_MODEL,
        UnknownModelError,
        get_provider,
        get_registry,
    )
except ImportError:  # pragma: no cover - ejecucion directa dentro de core_bridge/
    from browser import ASK_TIMEOUT_S, CircuitBreakerOpenError  # type: ignore[no-redef]
    from providers import (  # type: ignore[no-redef]
        DEFAULT_MODEL,
        UnknownModelError,
        get_provider,
        get_registry,
    )

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("core_bridge.server")

app = FastAPI(title="core-bridge", version="1.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Modelos (formato estandar OpenAI + consulta generica con adjuntos)
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] = "user"
    content: Any = ""


class ChatCompletionRequest(BaseModel):
    model: str = DEFAULT_MODEL
    messages: list[ChatMessage] = Field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False


class EvaluateRequest(BaseModel):
    """Payload generico de ``POST /v1/evaluate``.

    - ``query``: prompt/instrucciones (no volcar aqui el codigo fuente).
    - ``attachments``: rutas de archivos (relativas al CWD del servidor o
      absolutas) que se adjuntan de forma nativa en el navegador.
    - ``timeout_s``: timeout global opcional (por defecto 180s).
    - ``model``: proveedor a usar (se resuelve en el registro); por defecto
      ``perplexity-web``.
    - ``prompt`` / ``files``: alias legacy de ``query`` / ``attachments``.
    """

    query: str = ""
    attachments: list[str] = Field(default_factory=list)
    prompt: str = ""
    files: list[str] = Field(default_factory=list)
    timeout_s: float | None = None
    model: str = DEFAULT_MODEL

    def resolved_query(self) -> str:
        return (self.query or self.prompt or "").strip()

    def resolved_attachments(self) -> list[str]:
        return list(self.attachments or self.files)


def messages_to_prompt(messages: list[ChatMessage]) -> str:
    """Convierte mensajes OpenAI a un unico prompt para el textarea web."""
    parts: list[str] = []
    for msg in messages:
        content = msg.content
        if isinstance(content, list):  # content parts [{type, text, ...}]
            texts = [
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") in ("text", "input_text")
            ]
            content = "\n".join(t for t in texts if t)
        content = str(content or "").strip()
        if not content:
            continue
        if msg.role == "system":
            parts.append(f"[instrucciones del sistema]\n{content}")
        elif msg.role == "assistant":
            parts.append(f"asistente: {content}")
        elif msg.role == "tool":
            parts.append(f"[herramienta]\n{content}")
        else:
            parts.append(content)
    prompt = "\n\n".join(parts).strip()
    if not prompt:
        raise ValueError("sin contenido aprovechable en 'messages'")
    return prompt


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _resolve_model(requested: str | None) -> str:
    """Modelo efectivo: el pedido o, si viene vacio, el del registro."""
    text = (requested or "").strip()
    if text:
        return text
    return get_registry().default_model() or DEFAULT_MODEL


def _resolve_evaluate_files(files: list[str]) -> list[str]:
    """Resuelve rutas de ``/v1/evaluate`` y valida existencia (422 si falla).

    Incluye un fallback para el bridge en Docker: si llega una ruta absoluta
    del host (p. ej. ``C:\\repo\\src\\engine\\trie.py``) que no existe dentro
    del contenedor, se busca el mismo sufijo relativo bajo el proyecto
    (``PPLX_PROJECT_ROOT`` o el CWD).
    """
    root = Path(os.environ.get("PPLX_PROJECT_ROOT", str(Path.cwd())))
    resolved: list[str] = []
    for f in files:
        p = Path(f).expanduser()
        candidates: list[Path] = [p if p.is_absolute() else Path.cwd() / p]
        posix_parts = Path(str(f).replace("\\", "/")).parts
        for i in range(len(posix_parts)):
            candidates.append(root / Path(*posix_parts[i:]))
        final: Path | None = None
        for candidate in candidates:
            if candidate.is_file():
                final = candidate.resolve()
                break
        if final is None:
            raise HTTPException(
                status_code=422, detail=f"archivo no encontrado o no valido: {f}"
            )
        resolved.append(str(final))
    return resolved


def _bridge_failure(exc: Exception, *, where: str) -> HTTPException:
    """Mapea fallos del adaptador a codigos HTTP explicitos."""
    if isinstance(exc, CircuitBreakerOpenError):
        logger.error("circuit breaker abierto (%s): %s", where, exc)
        return HTTPException(status_code=503, detail=f"circuit breaker: {exc}")
    if isinstance(exc, (TimeoutError, RuntimeError, ValueError)):
        logger.error("fallo del puente (%s): %s", where, exc)
        return HTTPException(status_code=502, detail=f"perplexity bridge: {exc}")
    logger.exception("error inesperado del puente (%s)", where)
    return HTTPException(status_code=500, detail=f"error interno: {exc}")


@app.get("/health")
def health() -> dict[str, Any]:
    """Estado del proveedor por defecto, navegador y circuit breaker."""
    registry = get_registry()
    try:
        provider = registry.provider(None)
    except UnknownModelError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    data = provider.health()
    data.setdefault("default_model", registry.default_model())
    data["models"] = registry.models()
    return data


@app.get("/v1/models")
def list_models() -> dict[str, Any]:
    """Modelos activos del registro de proveedores (formato OpenAI)."""
    return {"object": "list", "data": get_registry().model_cards()}


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest) -> JSONResponse:
    if req.stream:
        raise HTTPException(
            status_code=400,
            detail="stream=true no soportado por el puente; usa stream=false",
        )
    try:
        prompt = messages_to_prompt(req.messages)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    model = _resolve_model(req.model)
    try:
        provider = get_provider(model)
    except UnknownModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    logger.info(
        "chat.completions provider=%s model=%s prompt_chars=%d",
        provider.name, model, len(prompt),
    )
    chat = getattr(provider, "chat", None)
    try:
        if callable(chat):
            # Proveedores API (p. ej. Gemini) traducen el historial completo.
            answer = await run_in_threadpool(
                chat, req.messages, timeout_s=ASK_TIMEOUT_S
            )
        else:
            answer = await run_in_threadpool(
                provider.ask, prompt, timeout_s=ASK_TIMEOUT_S
            )
    except Exception as exc:  # noqa: BLE001 - mapeado a HTTP explicito
        raise _bridge_failure(exc, where="chat.completions") from exc

    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    prompt_tokens = _estimate_tokens(prompt)
    completion_tokens = _estimate_tokens(answer)
    return JSONResponse(
        {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "provider": provider.name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )


@app.post("/v1/evaluate")
async def evaluate(req: EvaluateRequest) -> JSONResponse:
    """Consulta generica con adjuntos (query + attachments).

    Acepta los alias legacy ``prompt`` / ``files`` y devuelve la respuesta
    en ``response`` (generico) y ``answer`` (alias retrocompatible).
    """
    query = req.resolved_query()
    if not query:
        raise HTTPException(status_code=422, detail="campo 'query' vacio")
    raw_attachments = req.resolved_attachments()
    if not raw_attachments:
        raise HTTPException(
            status_code=422,
            detail="campo 'attachments' vacio: indica al menos un archivo",
        )
    attachments = _resolve_evaluate_files(raw_attachments)
    timeout = ASK_TIMEOUT_S if req.timeout_s is None else float(req.timeout_s)

    model = _resolve_model(req.model)
    try:
        provider = get_provider(model)
    except UnknownModelError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    logger.info(
        "evaluate provider=%s model=%s query_chars=%d attachments=%d timeout=%.0f",
        provider.name, model, len(query), len(attachments), timeout,
    )
    try:
        answer = await run_in_threadpool(
            provider.ask, query, attachments=attachments, timeout_s=timeout
        )
    except Exception as exc:  # noqa: BLE001 - mapeado a HTTP explicito
        raise _bridge_failure(exc, where="evaluate") from exc

    created = int(time.time())
    evaluation_id = f"eval-{uuid.uuid4().hex[:12]}"
    return JSONResponse(
        {
            "id": evaluation_id,
            "object": "evaluation",
            "created": created,
            "model": model,
            "provider": provider.name,
            "query": query,
            "query_chars": len(query),
            "attachments": attachments,
            "response": answer,
            "response_chars": len(answer),
            # Alias retrocompatibles con la estructura anterior.
            "files": attachments,
            "answer": answer,
            "answer_chars": len(answer),
            "usage": {
                "prompt_tokens": _estimate_tokens(query),
                "completion_tokens": _estimate_tokens(answer),
                "total_tokens": _estimate_tokens(query) + _estimate_tokens(answer),
            },
        }
    )


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("PPLX_HOST", "127.0.0.1")
    port = int(os.environ.get("PPLX_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
