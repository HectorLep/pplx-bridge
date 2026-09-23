"""Contrato abstracto de proveedores del puente (web o API).

``BaseProvider`` define la interfaz comun que consume ``core_bridge.server``:

- ``ask(query, attachments=..., timeout_s=...) -> str``
- ``list_models() -> list[str]``
- ``health() -> dict``
- ``model_info(model) -> dict`` (ficha tipo OpenAI)

Los proveedores concretos viven en ``core_bridge.providers.web`` (Playwright)
o en el subpaquete que se anada para APIs HTTP.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any


class ProviderError(RuntimeError):
    """Fallo controlado de un proveedor (el servidor lo mapea a HTTP 502)."""


class BaseProvider(ABC):
    """Interfaz abstracta de un backend conversacional (web o API)."""

    name = "base"
    kind = "generic"
    default_model = ""
    owned_by = "core-bridge"
    created_at: int = 0

    def __init__(self) -> None:
        self.created_at = int(time.time())

    @abstractmethod
    def ask(
        self,
        query: str,
        attachments: list[str] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Envia *query* (con adjuntos opcionales) y devuelve la respuesta."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Ids de modelo activos expuestos por este proveedor."""

    def health(self) -> dict[str, Any]:
        """Estado serializable sin lanzar trabajo."""
        return {
            "provider": self.name,
            "status": "ok",
            "models": self.list_models(),
        }

    def model_info(self, model: str | None = None) -> dict[str, Any]:
        """Ficha del modelo en formato OpenAI (``object='model'``)."""
        model_id = (model or self.default_model or "").strip()
        return {
            "id": model_id,
            "object": "model",
            "created": self.created_at or int(time.time()),
            "owned_by": self.owned_by,
        }

    def evaluate(
        self,
        query: str,
        attachments: list[str] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Alias generico de :meth:`ask` (compatibilidad con /v1/evaluate)."""
        return self.ask(query, attachments=attachments, timeout_s=timeout_s)


__all__ = ["BaseProvider", "ProviderError"]
