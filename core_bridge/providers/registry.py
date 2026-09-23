"""Registro central de proveedores y resolucion por modelo.

El campo ``model`` de ``/v1/chat/completions`` y ``/v1/evaluate`` se
resuelve aqui: cada id registrado apunta al proveedor que debe atender la
peticion. ``DEFAULT_MODEL`` (``perplexity-web``) se usa cuando la peticion
no indica modelo o lo deja vacio.

Uso::

    from core_bridge.providers import get_provider, get_registry

    provider = get_provider("perplexity-web")
    print(provider.ask("Hola"))

    registry = get_registry()
    print(registry.models())
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable

DEFAULT_MODEL = "perplexity-web"


class UnknownModelError(KeyError):
    """El modelo solicitado no esta registrado en el puente."""

    def __init__(self, model: str, available: Iterable[str] = ()) -> None:
        self.model = model
        self.available = list(available)
        super().__init__(model)

    def __str__(self) -> str:
        models = ", ".join(self.available) or "ninguno"
        return f"modelo no registrado: {self.model!r} (disponibles: {models})"


def _provider_name(provider: Any) -> str:
    """Nombre estable de un proveedor (tolerante con objetos legacy)."""
    name = getattr(provider, "name", None) or type(provider).__name__.lower()
    return str(name).strip() or type(provider).__name__.lower()


def _provider_models(
    provider: Any, models: Iterable[str] | None
) -> list[str]:
    """Modelos declarados por un proveedor (o por el llamante)."""
    if models is None:
        lister = getattr(provider, "list_models", None)
        if callable(lister):
            models = lister()
        else:  # compat: objetos legacy sin list_models()
            fallback = getattr(provider, "default_model", None) or DEFAULT_MODEL
            models = [fallback]
    resolved: list[str] = []
    for model in models:
        text = str(model).strip()
        if text and text not in resolved:
            resolved.append(text)
    return resolved


class ProviderRegistry:
    """Mapa id de modelo -> proveedor, con proveedor por defecto.

    - ``register(provider, models=None, default=True)`` anade un proveedor.
    - ``provider(model)`` resuelve el proveedor que atiende *model*
      (``None``/vacio => proveedor por defecto); lanza
      :class:`UnknownModelError` si el modelo no existe.
    - ``models()`` devuelve los ids activos en orden de registro.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._providers: dict[str, Any] = {}
        self._models: dict[str, str] = {}
        self._order: list[str] = []
        self._default: str | None = None

    # -- registro ----------------------------------------------------------
    def register(
        self,
        provider: Any,
        models: Iterable[str] | None = None,
        *,
        default: bool = False,
        replace: bool = False,
    ) -> list[str]:
        """Registra *provider* y sus modelos; devuelve los ids registrados."""
        name = _provider_name(provider)
        model_ids = _provider_models(provider, models)
        if not model_ids:
            raise ValueError(f"el proveedor {name!r} no declara modelos")
        with self._lock:
            if name in self._providers and not replace:
                raise ValueError(
                    f"proveedor ya registrado: {name!r} (usa replace=True)"
                )
            if name in self._providers:
                self._remove_locked(name)
            conflicts = sorted(m for m in model_ids if m in self._models)
            if conflicts and not replace:
                raise ValueError(
                    f"modelos ya registrados por otro proveedor: {conflicts}"
                )
            for model in conflicts:
                self._remove_locked(self._models[model])
            self._providers[name] = provider
            for model in model_ids:
                self._models[model] = name
            self._order.append(name)
            if default or self._default is None:
                self._default = name
            return list(model_ids)

    def unregister(self, provider: Any, *, missing_ok: bool = True) -> None:
        """Retira un proveedor (por nombre u objeto) y sus modelos."""
        name = provider if isinstance(provider, str) else _provider_name(provider)
        with self._lock:
            if name not in self._providers:
                if missing_ok:
                    return
                raise KeyError(name)
            self._remove_locked(name)

    def _remove_locked(self, name: str) -> None:
        self._providers.pop(name, None)
        for model in [m for m, owner in self._models.items() if owner == name]:
            self._models.pop(model, None)
        self._order = [n for n in self._order if n != name]
        if self._default == name:
            self._default = self._order[0] if self._order else None

    def clear(self) -> None:
        """Vacia el registro (util en tests)."""
        with self._lock:
            self._providers.clear()
            self._models.clear()
            self._order.clear()
            self._default = None

    # -- resolucion --------------------------------------------------------
    def provider(self, model: str | None = None) -> Any:
        """Proveedor que atiende *model* (o el de por defecto si es vacio)."""
        with self._lock:
            wanted = (model or "").strip()
            if not wanted:
                if self._default is None:
                    raise UnknownModelError(DEFAULT_MODEL, self.models())
                return self._providers[self._default]
            owner = self._models.get(wanted)
            if owner is None and wanted in self._providers:
                owner = wanted
            if owner is None:
                raise UnknownModelError(wanted, self.models())
            return self._providers[owner]

    def has_model(self, model: str) -> bool:
        with self._lock:
            return (model or "").strip() in self._models

    def models(self) -> list[str]:
        """Ids de modelo activos, en orden de registro."""
        with self._lock:
            return list(self._models)

    def default_model(self) -> str | None:
        """Id preferido del proveedor por defecto (o el primero suyo)."""
        with self._lock:
            if self._default is None:
                return None
            provider = self._providers[self._default]
            declared = str(getattr(provider, "default_model", "") or "").strip()
            if declared and self._models.get(declared) == self._default:
                return declared
            for model, owner in self._models.items():
                if owner == self._default:
                    return model
            return None

    def model_cards(self) -> list[dict[str, Any]]:
        """Fichas de modelos en formato OpenAI (``object='model'``)."""
        with self._lock:
            cards: list[dict[str, Any]] = []
            for model, owner in self._models.items():
                provider = self._providers[owner]
                info = getattr(provider, "model_info", None)
                if callable(info):
                    cards.append(info(model))
                else:  # compat con objetos legacy
                    cards.append(
                        {
                            "id": model,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": owner,
                        }
                    )
            return cards

    def snapshot(self) -> dict[str, Any]:
        """Estado serializable del registro (diagnostico / tests)."""
        with self._lock:
            return {
                "default": self._default,
                "models": self.models(),
                "providers": [
                    {
                        "name": name,
                        "models": [
                            m for m, owner in self._models.items() if owner == name
                        ],
                        "default": name == self._default,
                    }
                    for name in self._order
                ],
            }


# ---------------------------------------------------------------------------
# Registro global (singleton por proceso)
# ---------------------------------------------------------------------------

_default_registry: ProviderRegistry | None = None
_registry_lock = threading.Lock()


def build_default_registry() -> ProviderRegistry:
    """Registro con los proveedores activos por defecto (web/perplexity)."""
    from .web.perplexity import PerplexityProvider  # perezoso: evita ciclos

    registry = ProviderRegistry()
    registry.register(PerplexityProvider(), default=True)
    return registry


def get_registry() -> ProviderRegistry:
    """Registro global del proceso (se crea en la primera llamada)."""
    global _default_registry
    with _registry_lock:
        if _default_registry is None:
            _default_registry = build_default_registry()
        return _default_registry


def set_registry(registry: ProviderRegistry | None) -> None:
    """Sustituye el registro global (tests o despliegues alternos).

    Con ``None`` se restaura el registro por defecto en la proxima llamada.
    """
    global _default_registry
    with _registry_lock:
        _default_registry = registry


def register_provider(
    provider: Any,
    models: Iterable[str] | None = None,
    *,
    default: bool = False,
    replace: bool = True,
) -> list[str]:
    """Atajo: registra un proveedor en el registro global."""
    return get_registry().register(
        provider, models, default=default, replace=replace
    )


def get_provider(model: str | None = None) -> Any:
    """Proveedor global que atiende *model* (o el de por defecto)."""
    return get_registry().provider(model)


def available_models() -> list[str]:
    """Ids de modelo activos en el registro global."""
    return get_registry().models()


__all__ = [
    "DEFAULT_MODEL",
    "ProviderRegistry",
    "UnknownModelError",
    "available_models",
    "build_default_registry",
    "get_provider",
    "get_registry",
    "register_provider",
    "set_registry",
]
