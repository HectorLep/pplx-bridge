"""Adaptadores del puente web.

``BaseWebAdapter`` define el contrato abstracto y agnostico del backend:

- ``ask(query, attachments=...) -> response``
- ``health() -> dict``

``PerplexityAdapter`` es la implementacion concreta sobre Playwright. El
vocabulario es generico (query / prompt / attachments / response): ningun
caso de uso (auditoria, notas, rubricas...) vive en este paquete.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any

from .browser import PerplexityBrowser, browser_health, get_browser


class BaseWebAdapter(ABC):
    """Interfaz abstracta de un backend web conversacional."""

    name = "web"

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
    def health(self) -> dict[str, Any]:
        """Estado del adaptador sin lanzar trabajo (serializable)."""

    def evaluate(
        self,
        query: str,
        attachments: list[str] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> str:
        """Alias generico de :meth:`ask` (compatibilidad con /v1/evaluate)."""
        return self.ask(query, attachments=attachments, timeout_s=timeout_s)


class PerplexityAdapter(BaseWebAdapter):
    """Adaptador concreto: Perplexity web via Playwright (core_bridge.browser)."""

    name = "perplexity"

    def __init__(self, browser: PerplexityBrowser | None = None) -> None:
        self._browser = browser

    @property
    def browser(self) -> PerplexityBrowser:
        """Navegador singleton (se arranca en la primera consulta)."""
        if self._browser is None:
            self._browser = get_browser()
        return self._browser

    def ask(
        self,
        query: str,
        attachments: list[str] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> str:
        text = (query or "").strip()
        if not text:
            raise ValueError("query vacia")
        return self.browser.ask(
            text, timeout_s=timeout_s, attachments=list(attachments or [])
        )

    def health(self) -> dict[str, Any]:
        browser = self._browser
        data = browser_health() if browser is None else browser.health()
        return {"adapter": self.name, **data}


_default_adapter: BaseWebAdapter | None = None
_default_lock = threading.Lock()


def get_adapter() -> BaseWebAdapter:
    """Adaptador por defecto del proceso (PerplexityAdapter)."""
    global _default_adapter
    with _default_lock:
        if _default_adapter is None:
            _default_adapter = PerplexityAdapter()
        return _default_adapter


def set_adapter(adapter: BaseWebAdapter | None) -> None:
    """Sustituye el adaptador por defecto (util para tests o backends alternos)."""
    global _default_adapter
    with _default_lock:
        _default_adapter = adapter


__all__ = [
    "BaseWebAdapter",
    "PerplexityAdapter",
    "get_adapter",
    "set_adapter",
]
