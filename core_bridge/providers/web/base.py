"""Base de proveedores web: Playwright, perfiles y Circuit Breaker.

``BaseWebProvider`` encapsula el ciclo de vida del navegador persistente
(multi-navegador Brave/Chrome/Edge, perfil aislado por navegador y conexion
CDP opcional) y delega la politica anti-bucles en el ``CircuitBreaker`` de
``core_bridge.browser`` (3 fallos consecutivos => aborta y vuelca artefactos).

Las subclases concretas (p. ej. :class:`~core_bridge.providers.web.perplexity.PerplexityProvider`)
solo declaran sus modelos y, si acaso, la forma de construir el navegador.
"""

from __future__ import annotations

from typing import Any

from ...browser import PerplexityBrowser, browser_health, get_browser
from ..base import BaseProvider


class BaseWebProvider(BaseProvider):
    """Proveedor respaldado por un navegador Playwright persistente."""

    name = "web"
    kind = "web"

    def __init__(self, browser: PerplexityBrowser | None = None) -> None:
        super().__init__()
        self._browser = browser

    @property
    def browser(self) -> PerplexityBrowser:
        """Navegador singleton (se arranca en la primera consulta)."""
        if self._browser is None:
            self._browser = get_browser()
        return self._browser

    def list_models(self) -> list[str]:
        """Modelos declarados (las subclases suelen sobrescribirlo)."""
        model = (self.default_model or "").strip()
        return [model] if model else []

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
        """Estado del navegador y del circuit breaker (no arranca nada)."""
        browser = self._browser
        data = browser_health() if browser is None else browser.health()
        return {"provider": self.name, "adapter": self.name, **data}

    def reset_circuit_breaker(self) -> None:
        """Reinicia manualmente el contador de fallos consecutivos."""
        if self._browser is not None:
            self._browser.reset_circuit_breaker()


__all__ = ["BaseWebProvider"]
