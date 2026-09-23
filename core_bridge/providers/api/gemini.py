"""Proveedor Google AI Studio (Gemini API) via REST + httpx.

Expone los modelos ``gemini-2.5-flash`` (alias ``gemini-flash`` y
``gemini-1.5-flash``) y ``gemini-2.5-pro`` (alias ``gemini-pro``). Cada
familia se registra como un proveedor independiente para que el despacho
por modelo de ``core_bridge.server`` funcione sin tocar el contrato de
``BaseProvider``:

- ``gemini-flash`` -> ``gemini-2.5-flash``, ``gemini-flash``, ``gemini-1.5-flash``
- ``gemini-pro``   -> ``gemini-2.5-pro``, ``gemini-pro``

Solo se auto-registran en :func:`build_default_registry` cuando hay
``GEMINI_API_KEY`` (entorno o ``.env``); si no, el puente sigue operando con
el backend web y ``/v1/models`` no lista Gemini.

La llamada REST es::

    POST {base}/models/{model}:generateContent?key={GEMINI_API_KEY}

con el formato nativo de Gemini (``contents``/``systemInstruction``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import httpx

from ..base import ProviderError
from .base import BaseApiProvider, iter_messages, read_env_value

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_FLASH = "gemini-2.5-flash"
GEMINI_PRO = "gemini-2.5-pro"
GEMINI_ALIASES: dict[str, str] = {
    "gemini-flash": GEMINI_FLASH,
    "gemini-1.5-flash": GEMINI_FLASH,
    "gemini-pro": GEMINI_PRO,
}
GEMINI_FAMILIES: dict[str, tuple[str, ...]] = {
    GEMINI_FLASH: (GEMINI_FLASH, "gemini-flash", "gemini-1.5-flash"),
    GEMINI_PRO: (GEMINI_PRO, "gemini-pro"),
}
GEMINI_HINTS: dict[int, str] = {
    400: "peticion invalida para la API de Gemini",
    401: "GEMINI_API_KEY invalida, revocada o ausente",
    403: "acceso denegado (API de Gemini no habilitada para la clave)",
    404: "modelo no disponible en la API de Gemini",
    429: "cuota agotada; reintenta mas tarde",
    500: "error interno de Gemini",
    503: "servicio de Gemini no disponible",
}


def normalize_model(model: str | None) -> str:
    """Id real de Gemini para *model* (resuelve alias, tolera vacios)."""
    text = str(model or "").strip().lower()
    if not text:
        return GEMINI_FLASH
    return GEMINI_ALIASES.get(text, text)


def gemini_models_for(model: str | None) -> tuple[str, ...]:
    """Ids (reales y alias) que declara la familia de *model*."""
    return GEMINI_FAMILIES.get(normalize_model(model), (normalize_model(model),))


def extract_text(data: Mapping[str, Any]) -> str:
    """Texto de la primera respuesta valida del payload ``generateContent``."""
    parts: list[str] = []
    for candidate in data.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("content")
        if not isinstance(content, Mapping):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, Mapping):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
    text = "\n".join(parts).strip()
    if text:
        return text
    reasons: list[str] = []
    for candidate in data.get("candidates") or []:
        if isinstance(candidate, Mapping) and candidate.get("finishReason"):
            reasons.append(str(candidate["finishReason"]))
    feedback = data.get("promptFeedback")
    if isinstance(feedback, Mapping) and feedback.get("blockReason"):
        reasons.append(str(feedback["blockReason"]))
    detail = ", ".join(dict.fromkeys(reasons)) or "sin candidatos"
    raise ProviderError(f"Gemini no devolvio texto ({detail})")


class GeminiProvider(BaseApiProvider):
    """Backend REST de Google AI Studio (Gemini) con httpx directo."""

    name = "gemini-flash"
    kind = "api"
    api_key_env = "GEMINI_API_KEY"
    base_url = GEMINI_API_BASE
    owned_by = "google"
    default_model = GEMINI_FLASH

    def __init__(
        self,
        model: str | None = None,
        *,
        api_key: str | None = None,
        timeout_s: float | None = None,
        transport: httpx.BaseTransport | None = None,
        dotenv_paths: Iterable[Path] | None = None,
    ) -> None:
        if api_key is None:
            api_key = read_env_value(self.api_key_env, dotenv_paths)
        resolved = normalize_model(model or self.default_model)
        super().__init__(api_key, model=resolved, timeout_s=timeout_s, transport=transport)
        self.name = "gemini-pro" if resolved == GEMINI_PRO else "gemini-flash"
        self.models = gemini_models_for(resolved)
        self.last_usage: dict[str, Any] | None = None

    # -- contrato BaseProvider ---------------------------------------------
    @property
    def api_model(self) -> str:
        """Id real enviado a ``generativelanguage.googleapis.com``."""
        return normalize_model(self.default_model)

    def list_models(self) -> list[str]:
        return list(self.models)

    def ask(
        self,
        query: str,
        attachments: Sequence[str] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Envia *query* (con adjuntos como contexto) y devuelve el texto."""
        text = (query or "").strip()
        if not text:
            raise ValueError("query vacia")
        if not self.has_credentials:
            raise ProviderError(
                f"{self.api_key_env} no configurada "
                "(define la variable de entorno o el archivo .env)"
            )
        context = self.read_attachments(attachments)
        prompt = f"{context}\n\n{text}" if context else text
        return self._generate(
            [{"role": "user", "parts": [{"text": prompt}]}],
            system_instruction="",
            timeout_s=timeout_s,
        )

    # -- chat completions ---------------------------------------------------
    def translate_messages(
        self, messages: Iterable[Any]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Historial OpenAI -> ``(systemInstruction, contents)`` de Gemini."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []
        for role, text in iter_messages(messages):
            if role == "system":
                system_parts.append(text)
                continue
            if role == "tool":
                text = f"[herramienta]\n{text}"
            gemini_role = "model" if role == "assistant" else "user"
            if contents and contents[-1]["role"] == gemini_role:
                contents[-1]["parts"][0]["text"] += f"\n\n{text}"
            else:
                contents.append({"role": gemini_role, "parts": [{"text": text}]})
        return "\n\n".join(system_parts).strip(), contents

    def chat(
        self,
        messages: Iterable[Any],
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Traduce el historial de ``/v1/chat/completions`` a Gemini."""
        if not self.has_credentials:
            raise ProviderError(f"{self.api_key_env} no configurada")
        system_instruction, contents = self.translate_messages(messages)
        if not contents:
            raise ValueError("sin contenido aprovechable en 'messages'")
        return self._generate(
            contents, system_instruction=system_instruction, timeout_s=timeout_s
        )

    # -- HTTP ---------------------------------------------------------------
    def endpoint(self) -> str:
        return f"{self.base_url}/models/{self.api_model}:generateContent"

    def error_hint(self, status_code: int) -> str:
        return GEMINI_HINTS.get(status_code, "")

    def _generate(
        self,
        contents: list[dict[str, Any]],
        *,
        system_instruction: str = "",
        timeout_s: float | None = None,
    ) -> str:
        payload: dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        data = self.post_json(
            self.endpoint(),
            params={"key": self.api_key},
            json_body=payload,
            timeout_s=timeout_s,
        )
        usage = data.get("usageMetadata")
        self.last_usage = dict(usage) if isinstance(usage, Mapping) else None
        return extract_text(data)


def build_gemini_providers(
    *,
    api_key: str | None = None,
    dotenv_paths: Iterable[Path] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float | None = None,
) -> list[GeminiProvider]:
    """Un proveedor por familia (flash/pro); vacio si no hay credencial."""
    if api_key is None:
        api_key = read_env_value("GEMINI_API_KEY", dotenv_paths)
    key = str(api_key or "").strip()
    if not key:
        return []
    return [
        GeminiProvider(
            GEMINI_FLASH,
            api_key=key,
            timeout_s=timeout_s,
            transport=transport,
        ),
        GeminiProvider(
            GEMINI_PRO,
            api_key=key,
            timeout_s=timeout_s,
            transport=transport,
        ),
    ]


def register_gemini_providers(
    registry: Any,
    *,
    api_key: str | None = None,
    dotenv_paths: Iterable[Path] | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float | None = None,
) -> list[str]:
    """Registra los modelos de Gemini en *registry* si hay ``GEMINI_API_KEY``."""
    registered: list[str] = []
    for provider in build_gemini_providers(
        api_key=api_key,
        dotenv_paths=dotenv_paths,
        transport=transport,
        timeout_s=timeout_s,
    ):
        registered.extend(
            registry.register(provider, provider.list_models(), replace=True)
        )
    return registered


__all__ = [
    "GEMINI_ALIASES",
    "GEMINI_API_BASE",
    "GEMINI_FAMILIES",
    "GEMINI_FLASH",
    "GEMINI_HINTS",
    "GEMINI_PRO",
    "GeminiProvider",
    "build_gemini_providers",
    "extract_text",
    "gemini_models_for",
    "normalize_model",
    "register_gemini_providers",
]
