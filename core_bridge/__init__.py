"""core_bridge: puente web generico (providers + navegador + API + CLI).

El paquete no conoce ningun caso de uso concreto (auditoria, rubricas...):
expone un contrato generico ``query`` / ``attachments`` / ``response``.

Arquitectura multi-provider (web/api): ``core_bridge.providers`` mapea ids de
modelo a proveedores; ``perplexity-web`` (web) es el modelo por defecto.

Uso rapido::

    from core_bridge import PerplexityAdapter
    adapter = PerplexityAdapter()
    print(adapter.ask("Hola, quien eres?"))

    from core_bridge.providers import get_provider
    print(get_provider("perplexity-web").ask("Hola"))
"""

from .adapter import BaseWebAdapter, PerplexityAdapter, get_adapter, set_adapter
from .browser import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    PerplexityBrowser,
    ask_perplexity,
    get_browser,
)
from .providers import (
    DEFAULT_MODEL,
    PERPLEXITY_MODEL,
    BaseProvider,
    BaseWebProvider,
    PerplexityProvider,
    ProviderError,
    ProviderRegistry,
    UnknownModelError,
    available_models,
    get_provider,
    get_registry,
    register_provider,
    set_registry,
)

__all__ = [
    "BaseProvider",
    "BaseWebAdapter",
    "BaseWebProvider",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "DEFAULT_MODEL",
    "PERPLEXITY_MODEL",
    "PerplexityAdapter",
    "PerplexityBrowser",
    "PerplexityProvider",
    "ProviderError",
    "ProviderRegistry",
    "UnknownModelError",
    "ask_perplexity",
    "available_models",
    "get_adapter",
    "get_browser",
    "get_provider",
    "get_registry",
    "register_provider",
    "set_adapter",
    "set_registry",
]
