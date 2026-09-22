"""core_bridge: puente web generico (adaptador + navegador + API + CLI).

El paquete no conoce ningun caso de uso concreto (auditoria, rubricas...):
expone un contrato generico ``query`` / ``attachments`` / ``response``.

Uso rapido::

    from core_bridge import PerplexityAdapter
    adapter = PerplexityAdapter()
    print(adapter.ask("Hola, quien eres?"))
    print(adapter.health())
"""

from .adapter import BaseWebAdapter, PerplexityAdapter, get_adapter, set_adapter
from .browser import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    PerplexityBrowser,
    ask_perplexity,
    get_browser,
)

__all__ = [
    "BaseWebAdapter",
    "CircuitBreaker",
    "CircuitBreakerOpenError",
    "PerplexityAdapter",
    "PerplexityBrowser",
    "ask_perplexity",
    "get_adapter",
    "get_browser",
    "set_adapter",
]
