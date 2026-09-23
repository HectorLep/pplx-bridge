"""Tests de la arquitectura multi-provider y el despacho dinamico.

Cubren:
- contrato ``BaseProvider`` / ``ProviderRegistry`` y resolucion por modelo;
- ``GET /v1/models`` dinamico en formato OpenAI;
- despacho de ``/v1/chat/completions`` y ``/v1/evaluate`` segun ``model``;
- shims de compatibilidad de ``core_bridge.adapter``.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core_bridge import server
from core_bridge.providers import (
    DEFAULT_MODEL,
    BaseProvider,
    ProviderRegistry,
    UnknownModelError,
    build_default_registry,
    get_provider,
    get_registry,
    set_registry,
)
from core_bridge.providers.web.perplexity import PERPLEXITY_MODEL, PerplexityProvider


class DummyProvider(BaseProvider):
    """Proveedor de prueba: eco determinista, sin Playwright."""

    name = "dummy"
    default_model = "dummy-1"

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, list[str], float | None]] = []

    def ask(
        self,
        query: str,
        attachments: list[str] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> str:
        self.calls.append((query, list(attachments or []), timeout_s))
        return f"eco:{query}"

    def list_models(self) -> list[str]:
        return ["dummy-1", "dummy-2"]


@pytest.fixture()
def dummy() -> DummyProvider:
    """Aisla el registro global con un DummyProvider por defecto."""
    previous = get_registry()
    provider = DummyProvider()
    registry = ProviderRegistry()
    registry.register(provider, default=True)
    set_registry(registry)
    try:
        yield provider
    finally:
        set_registry(previous)


@pytest.fixture()
def client(dummy: DummyProvider) -> TestClient:
    return TestClient(server.app)


def test_registro_por_defecto_incluye_perplexity() -> None:
    """El registro de fabrica expone perplexity-web como unico modelo."""
    registry = build_default_registry()
    assert registry.models() == [PERPLEXITY_MODEL]
    provider = registry.provider(None)
    assert isinstance(provider, PerplexityProvider)
    assert provider is registry.provider(PERPLEXITY_MODEL)
    assert registry.default_model() == PERPLEXITY_MODEL
    assert DEFAULT_MODEL == PERPLEXITY_MODEL


def test_registry_resuelve_por_modelo_y_valida() -> None:
    """Resolucion por id, por defecto y error explicito si no existe."""
    registry = ProviderRegistry()
    provider = DummyProvider()
    registry.register(provider, default=True)
    assert registry.provider("dummy-2") is provider
    assert registry.provider() is provider
    assert registry.models() == ["dummy-1", "dummy-2"]
    with pytest.raises(UnknownModelError) as err:
        registry.provider("gpt-inexistente")
    assert "gpt-inexistente" in str(err.value)


def test_registry_conflictos_y_replace() -> None:
    """Repetir un proveedor exige replace=True; los modelos no se pisan solos."""
    registry = ProviderRegistry()
    registry.register(DummyProvider(), default=True)
    with pytest.raises(ValueError):
        registry.register(DummyProvider())
    assert registry.register(DummyProvider(), replace=True) == ["dummy-1", "dummy-2"]
    assert registry.models() == ["dummy-1", "dummy-2"]


def test_v1_models_dinamico(client: TestClient) -> None:
    """GET /v1/models devuelve los modelos activos en formato OpenAI."""
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == ["dummy-1", "dummy-2"]
    for card in body["data"]:
        assert card["object"] == "model"
        assert card["owned_by"] == "core-bridge"
        assert card["created"] > 0


def test_chat_dispatch_al_provider_del_modelo(
    client: TestClient, dummy: DummyProvider
) -> None:
    """chat/completions enruta al proveedor dueno del modelo solicitado."""
    r = client.post(
        "/v1/chat/completions",
        json={"model": "dummy-2", "messages": [{"role": "user", "content": "hola"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "eco:hola"
    assert body["model"] == "dummy-2"
    assert body["provider"] == "dummy"
    assert dummy.calls and dummy.calls[0][0] == "hola"


def test_chat_modelo_desconocido_404(client: TestClient) -> None:
    """Un modelo no registrado no se responde con otro proveedor."""
    r = client.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "hola"}]},
    )
    assert r.status_code == 404
    assert "nope" in r.json()["detail"]


def test_evaluate_dispatch_con_adjuntos(
    client: TestClient, dummy: DummyProvider, tmp_path: Path
) -> None:
    """evaluate usa el mismo registro y conserva los alias legacy."""
    f = tmp_path / "nota.txt"
    f.write_text("contenido", encoding="utf-8")
    r = client.post(
        "/v1/evaluate",
        json={"model": "dummy-1", "query": "resume", "attachments": [str(f)]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "eco:resume"
    assert body["response"] == "eco:resume"
    assert body["provider"] == "dummy"
    assert body["model"] == "dummy-1"
    assert body["attachments"] == [str(f)]
    assert dummy.calls and dummy.calls[0][1] == [str(f)]


def test_error_del_provider_se_mapea_a_502(dummy: DummyProvider) -> None:
    """Un fallo controlado del proveedor responde 502, no 500."""

    class BrokenProvider(DummyProvider):
        name = "broken"

        def ask(
            self,
            query: str,
            attachments: list[str] | None = None,
            *,
            timeout_s: float | None = None,
        ) -> str:
            raise RuntimeError("fallo simulado")

    get_registry().register(BrokenProvider(), ["broken-1"], replace=True)
    c = TestClient(server.app)
    r = c.post(
        "/v1/chat/completions",
        json={"model": "broken-1", "messages": [{"role": "user", "content": "x"}]},
    )
    assert r.status_code == 502
    assert "fallo simulado" in r.json()["detail"]


def test_adapter_shim_mantiene_compatibilidad(dummy: DummyProvider) -> None:
    """Los nombres legacy de adapter.py siguen operativos y respetan el swap."""
    from core_bridge import adapter as legacy
    from core_bridge.providers.web.base import BaseWebProvider

    assert legacy.BaseWebAdapter is BaseWebProvider
    assert legacy.PerplexityAdapter is PerplexityProvider
    assert isinstance(legacy.get_adapter(), DummyProvider)

    legacy.set_adapter(None)
    assert isinstance(legacy.get_adapter(), PerplexityProvider)
    assert get_registry().models() == [PERPLEXITY_MODEL]
    assert get_provider(PERPLEXITY_MODEL).name == "perplexity"


def test_health_expone_provider_y_modelos(client: TestClient) -> None:
    """/health informa el proveedor por defecto y los modelos activos."""
    body = client.get("/health").json()
    assert body["provider"] == "dummy"
    assert body["models"] == ["dummy-1", "dummy-2"]
    assert body["default_model"] == "dummy-1"
