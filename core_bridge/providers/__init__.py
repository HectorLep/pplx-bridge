"""Proveedores del puente: contrato, registro y backends (web/api).

La API del paquete es:

- :class:`BaseProvider`: contrato abstracto (``ask``, ``list_models``).
- :class:`ProviderRegistry` / :func:`get_registry`: mapa modelo -> proveedor.
- :func:`get_provider`: resuelve el proveedor por el campo ``model``.
- :class:`BaseWebProvider` / :class:`PerplexityProvider`: backend web
  (Playwright) con perfiles y Circuit Breaker.

Uso::

    from core_bridge.providers import get_provider

    provider = get_provider("perplexity-web")
    print(provider.ask("Hola"))
"""

from .base import BaseProvider, ProviderError
from .registry import (
    DEFAULT_MODEL,
    ProviderRegistry,
    UnknownModelError,
    available_models,
    build_default_registry,
    get_provider,
    get_registry,
    register_provider,
    set_registry,
)
from .web import PERPLEXITY_MODEL, BaseWebProvider, PerplexityProvider

__all__ = [
    "DEFAULT_MODEL",
    "PERPLEXITY_MODEL",
    "BaseProvider",
    "BaseWebProvider",
    "PerplexityProvider",
    "ProviderError",
    "ProviderRegistry",
    "UnknownModelError",
    "available_models",
    "build_default_registry",
    "get_provider",
    "get_registry",
    "register_provider",
    "set_registry",
]
