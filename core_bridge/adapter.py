"""Shims retrocompatibles del adaptador web.

La implementacion vive ahora en ``core_bridge.providers``:

- ``BaseWebAdapter`` -> :class:`core_bridge.providers.web.base.BaseWebProvider`
- ``PerplexityAdapter`` -> :class:`core_bridge.providers.web.perplexity.PerplexityProvider`
- ``get_adapter`` / ``set_adapter`` -> proveedor por defecto del registro

Se conservan estos nombres para que scripts, ejemplos y clientes historicos
sigan funcionando sin cambios.
"""

from __future__ import annotations

from typing import Any

from .providers.registry import (
    DEFAULT_MODEL,
    UnknownModelError,
    get_registry,
)
from .providers.web.base import BaseWebProvider
from .providers.web.perplexity import PerplexityProvider

# Nombres legacy (alias exactos de las clases nuevas).
BaseWebAdapter = BaseWebProvider
PerplexityAdapter = PerplexityProvider


def get_adapter() -> Any:
    """Adaptador/proveedor por defecto del proceso (compatibilidad)."""
    return get_registry().provider(None)


def set_adapter(adapter: Any | None) -> None:
    """Sustituye el proveedor por defecto (tests o backends alternos).

    Con ``None`` se restaura el :class:`PerplexityProvider` por defecto.
    El proveedor entrante se registra tambien bajo ``perplexity-web`` para
    que el despacho del servidor siga funcionando como antes.
    """
    registry = get_registry()
    try:
        current = registry.provider(None)
    except UnknownModelError:
        current = None
    if current is not None and current is not adapter:
        registry.unregister(current)

    if adapter is None:
        registry.register(PerplexityProvider(), [DEFAULT_MODEL], default=True, replace=True)
        return

    models = [DEFAULT_MODEL]
    lister = getattr(adapter, "list_models", None)
    if callable(lister):
        for model in lister():
            text = str(model).strip()
            if text and text not in models:
                models.append(text)
    registry.register(adapter, models, default=True, replace=True)


__all__ = [
    "BaseWebAdapter",
    "DEFAULT_MODEL",
    "PerplexityAdapter",
    "get_adapter",
    "set_adapter",
]
