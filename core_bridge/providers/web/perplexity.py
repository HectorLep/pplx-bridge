"""Proveedor Perplexity web (migrado desde ``core_bridge.adapter``).

Expone el modelo ``perplexity-web`` y conserva exactamente el
comportamiento del adaptador historico: Playwright con perfil persistente,
adjuntos nativos via FileChooser y Circuit Breaker de 3 fallos.
"""

from __future__ import annotations

from .base import BaseWebProvider

PERPLEXITY_MODEL = "perplexity-web"


class PerplexityProvider(BaseWebProvider):
    """Adaptador concreto: Perplexity web via Playwright."""

    name = "perplexity"
    kind = "web"
    default_model = PERPLEXITY_MODEL
    owned_by = "core-bridge"

    def list_models(self) -> list[str]:
        return [PERPLEXITY_MODEL]


__all__ = ["PERPLEXITY_MODEL", "PerplexityProvider"]
