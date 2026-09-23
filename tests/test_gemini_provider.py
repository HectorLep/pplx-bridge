"""Tests unitarios de ``BaseApiProvider`` / ``GeminiProvider`` con httpx mockeado.

Cubren el contrato completo sin tocar la red:

- payload REST de Gemini (``generateContent``), extraccion del texto y adjuntos;
- traduccion del historial de ``/v1/chat/completions`` (system/user/assistant);
- alias de modelos y auto-registro condicionado a ``GEMINI_API_KEY``;
- errores HTTP 401/429/500, timeout, respuestas sin candidatos y falta de key;
- despacho real del servidor FastAPI contra un ``httpx.MockTransport``.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from core_bridge import server
from core_bridge.providers import (
    ProviderError,
    ProviderRegistry,
    build_default_registry,
    get_registry,
    set_registry,
)
from core_bridge.providers.api.base import read_env_value
from core_bridge.providers.api.gemini import (
    GEMINI_FLASH,
    GEMINI_PRO,
    GeminiProvider,
    extract_text,
    normalize_model,
    register_gemini_providers,
)
from core_bridge.providers.web.perplexity import PERPLEXITY_MODEL


def gemini_ok(text: str = "respuesta de gemini", *, seen: dict | None = None):
    """Handler MockTransport que simula un 200 valido de Gemini."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["request"] = request
            seen["json"] = json.loads(request.content.decode("utf-8"))
            seen["calls"] = seen.get("calls", 0) + 1
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"role": "model", "parts": [{"text": text}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 3,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 5,
                },
            },
        )

    return handler


def make_provider(handler, model: str | None = None, **kwargs) -> GeminiProvider:
    return GeminiProvider(
        model or GEMINI_FLASH,
        api_key="test-key",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def _prompt_text(seen: dict) -> str:
    return seen["json"]["contents"][0]["parts"][0]["text"]


def test_ask_envia_formato_gemini_y_extrae_texto() -> None:
    """ask() hace POST a generateContent con key y extrae candidates[].parts[]."""
    seen: dict = {}
    provider = make_provider(gemini_ok("hola mundo", seen=seen))
    assert provider.ask("Hola") == "hola mundo"

    request = seen["request"]
    assert request.method == "POST"
    assert str(request.url).startswith(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_FLASH}:generateContent"
    )
    assert request.url.params["key"] == "test-key"
    assert seen["json"]["contents"] == [
        {"role": "user", "parts": [{"text": "Hola"}]}
    ]
    assert provider.last_usage == {
        "promptTokenCount": 3,
        "candidatesTokenCount": 2,
        "totalTokenCount": 5,
    }


def test_ask_con_adjuntos_los_incluye_como_bloques(tmp_path: Path) -> None:
    """Los adjuntos de texto se leen y se anteponen a la consulta."""
    seen: dict = {}
    nota = tmp_path / "nota.txt"
    nota.write_text("contenido unico del adjunto", encoding="utf-8")
    provider = make_provider(gemini_ok(seen=seen))
    provider.ask("resume esto", attachments=[str(nota)])

    prompt = _prompt_text(seen)
    assert f"===== ADJUNTO: {nota} =====" in prompt
    assert "contenido unico del adjunto" in prompt
    assert "===== FIN ADJUNTO =====" in prompt
    assert prompt.endswith("resume esto")


def test_ask_con_adjunto_inexistente_falla(tmp_path: Path) -> None:
    """Una ruta ilegible se reporta como ProviderError, sin llamar a la API."""
    seen: dict = {}
    provider = make_provider(gemini_ok(seen=seen))
    with pytest.raises(ProviderError) as err:
        provider.ask("x", attachments=[str(tmp_path / "no-existe.txt")])
    assert "no legible" in str(err.value)
    assert "calls" not in seen


def test_ask_query_vacia_es_value_error() -> None:
    provider = make_provider(gemini_ok())
    with pytest.raises(ValueError):
        provider.ask("   ")


def test_chat_traduce_historial_a_gemini() -> None:
    """system -> systemInstruction; assistant -> role model; user -> role user."""
    seen: dict = {}
    provider = make_provider(gemini_ok("eco", seen=seen))
    answer = provider.chat(
        [
            {"role": "system", "content": "eres un auditor"},
            {"role": "user", "content": "primer turno"},
            {"role": "assistant", "content": "respuesta previa"},
            {"role": "user", "content": "segundo turno"},
        ]
    )
    assert answer == "eco"
    body = seen["json"]
    assert body["systemInstruction"] == {"parts": [{"text": "eres un auditor"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["contents"][0]["parts"][0]["text"] == "primer turno"
    assert body["contents"][1]["parts"][0]["text"] == "respuesta previa"
    assert body["contents"][2]["parts"][0]["text"] == "segundo turno"


def test_chat_fusiona_turnos_consecutivos_y_partes() -> None:
    """Turnos seguidos del mismo rol se fusionan; content en partes funciona."""
    seen: dict = {}
    provider = make_provider(gemini_ok(seen=seen))
    provider.chat(
        [
            {"role": "user", "content": [{"type": "text", "text": "uno"}]},
            {"role": "user", "content": "dos"},
            {"role": "tool", "content": "resultado"},
        ]
    )
    contents = seen["json"]["contents"]
    assert len(contents) == 1
    assert contents[0]["role"] == "user"
    text = contents[0]["parts"][0]["text"]
    assert text.startswith("uno\n\ndos")
    assert "[herramienta]" in text and "resultado" in text


def test_chat_sin_mensajes_utiles_es_value_error() -> None:
    provider = make_provider(gemini_ok())
    with pytest.raises(ValueError):
        provider.chat([{"role": "user", "content": "   "}])


@pytest.mark.parametrize(
    "status,hint",
    [(401, "GEMINI_API_KEY"), (429, "cuota"), (500, "error interno")],
)
def test_errores_http_se_mapean_a_provider_error(status: int, hint: str) -> None:
    """401/429/500 se convierten en ProviderError con codigo, detalle y pista."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"code": status, "message": "detalle simulado"}},
        )

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as err:
        provider.ask("hola")
    message = str(err.value)
    assert f"HTTP {status}" in message
    assert "detalle simulado" in message
    assert hint in message


def test_timeout_se_mapea_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout simulado", request=request)

    provider = make_provider(handler, timeout_s=5)
    with pytest.raises(ProviderError) as err:
        provider.ask("hola")
    assert "timeout" in str(err.value).lower()


def test_respuesta_bloqueada_sin_candidatos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})

    provider = make_provider(handler)
    with pytest.raises(ProviderError) as err:
        provider.ask("hola")
    assert "SAFETY" in str(err.value)


def test_extract_text_une_partes_y_valida() -> None:
    data = {
        "candidates": [
            {"content": {"parts": [{"text": "uno"}, {"text": "dos"}]}},
            {"content": {"parts": [{"text": "tres"}]}},
        ]
    }
    assert extract_text(data) == "uno\ndos\ntres"
    with pytest.raises(ProviderError):
        extract_text({"candidates": []})


def test_sin_key_no_registra_y_ask_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback: sin GEMINI_API_KEY no hay modelos Gemini ni peticiones."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    registry = ProviderRegistry()
    assert register_gemini_providers(registry, dotenv_paths=()) == []
    assert registry.models() == []

    provider = GeminiProvider(api_key="", dotenv_paths=())
    with pytest.raises(ProviderError) as err:
        provider.ask("hola")
    assert "GEMINI_API_KEY" in str(err.value)
    assert provider.health()["status"] == "missing_api_key"


def test_registro_con_key_anade_modelos_y_alias() -> None:
    """Con key se registran las dos familias y los alias resuelven igual."""
    registry = ProviderRegistry()
    models = register_gemini_providers(
        registry, api_key="test-key", transport=httpx.MockTransport(gemini_ok())
    )
    assert models == [
        GEMINI_FLASH,
        "gemini-flash",
        "gemini-1.5-flash",
        GEMINI_PRO,
        "gemini-pro",
    ]

    flash = registry.provider(GEMINI_FLASH)
    pro = registry.provider(GEMINI_PRO)
    assert flash is registry.provider("gemini-flash")
    assert flash is registry.provider("gemini-1.5-flash")
    assert pro is registry.provider("gemini-pro")
    assert flash is not pro
    assert pro.api_model == GEMINI_PRO
    assert pro.list_models() == [GEMINI_PRO, "gemini-pro"]


def test_normalize_model_resuelve_alias() -> None:
    assert normalize_model("gemini-1.5-flash") == GEMINI_FLASH
    assert normalize_model("GEMINI-FLASH") == GEMINI_FLASH
    assert normalize_model("gemini-pro") == GEMINI_PRO
    assert normalize_model(None) == GEMINI_FLASH
    assert normalize_model("gemini-3-ultra") == "gemini-3-ultra"


def test_build_default_registry_incluye_gemini_solo_con_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La fabrica no lista Gemini sin key y si la lista con key (sin red)."""
    sin_key = build_default_registry()
    assert [m["id"] for m in sin_key.model_cards()] == [PERPLEXITY_MODEL]
    assert sin_key.default_model() == PERPLEXITY_MODEL

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    con_key = build_default_registry()
    cards = {card["id"]: card for card in con_key.model_cards()}
    assert PERPLEXITY_MODEL in cards
    assert GEMINI_FLASH in cards and GEMINI_PRO in cards
    assert cards[GEMINI_PRO]["owned_by"] == "google"
    assert con_key.default_model() == PERPLEXITY_MODEL


def test_read_env_value_prioriza_entorno_y_lee_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comentario\n"
        "export GEMINI_API_KEY='desde-archivo'\n"
        "OTRA=1\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert read_env_value("GEMINI_API_KEY", [env_file]) == "desde-archivo"

    monkeypatch.setenv("GEMINI_API_KEY", "desde-entorno")
    assert read_env_value("GEMINI_API_KEY", [env_file]) == "desde-entorno"
    assert read_env_value("NO_EXISTE", [env_file]) == ""


def test_provider_lee_key_desde_dotenv(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("GEMINI_API_KEY=file-key\n", encoding="utf-8")
    provider = GeminiProvider(dotenv_paths=[env_file])
    assert provider.api_key == "file-key"
    assert provider.has_credentials is True


def test_health_reporta_credenciales_y_modelos() -> None:
    provider = make_provider(gemini_ok())
    health = provider.health()
    assert health["status"] == "ok"
    assert health["api_key_set"] is True
    assert health["kind"] == "api"
    assert health["models"] == [GEMINI_FLASH, "gemini-flash", "gemini-1.5-flash"]


def test_apost_json_async_reutiliza_mismo_mapeo() -> None:
    """La ruta async (httpx.AsyncClient) funciona con el mismo MockTransport."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    provider = make_provider(handler)
    try:
        data = asyncio.run(
            provider.apost_json("https://example.test/v1", json_body={"x": 1})
        )
    finally:
        asyncio.run(provider.aclose())
    assert data == {"ok": True}


@pytest.fixture()
def gemini_env(monkeypatch: pytest.MonkeyPatch):
    """Servidor FastAPI con solo modelos Gemini (transporte mockeado)."""
    seen: dict = {}
    registry = ProviderRegistry()
    register_gemini_providers(
        registry,
        api_key="test-key",
        transport=httpx.MockTransport(gemini_ok("eco-gemini", seen=seen)),
    )
    previous = get_registry()
    set_registry(registry)
    try:
        yield TestClient(server.app), seen
    finally:
        set_registry(previous)


def test_v1_models_lista_gemini(gemini_env) -> None:
    client, _ = gemini_env
    body = client.get("/v1/models").json()
    ids = [card["id"] for card in body["data"]]
    assert ids == [
        GEMINI_FLASH,
        "gemini-flash",
        "gemini-1.5-flash",
        GEMINI_PRO,
        "gemini-pro",
    ]


def test_chat_endpoint_usa_historial_completo(gemini_env) -> None:
    client, seen = gemini_env
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "gemini-2.5-pro",
            "messages": [
                {"role": "system", "content": "eres un auditor"},
                {"role": "user", "content": "primer turno"},
                {"role": "assistant", "content": "respuesta previa"},
                {"role": "user", "content": "segundo turno"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "eco-gemini"
    assert body["provider"] == "gemini-pro"
    assert body["model"] == "gemini-2.5-pro"

    sent = seen["json"]
    assert sent["systemInstruction"]["parts"][0]["text"] == "eres un auditor"
    assert [c["role"] for c in sent["contents"]] == ["user", "model", "user"]
    assert sent["contents"][-1]["parts"][0]["text"] == "segundo turno"
    assert "gemini-2.5-pro:generateContent" in str(seen["request"].url)


def test_evaluate_endpoint_con_adjunto(gemini_env, tmp_path: Path) -> None:
    client, seen = gemini_env
    nota = tmp_path / "nota.txt"
    nota.write_text("contenido del adjunto via API", encoding="utf-8")
    response = client.post(
        "/v1/evaluate",
        json={
            "model": "gemini-flash",
            "query": "resume el archivo",
            "attachments": [str(nota)],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "eco-gemini"
    assert body["provider"] == "gemini-flash"
    assert body["attachments"] == [str(nota)]

    prompt = _prompt_text(seen)
    assert "contenido del adjunto via API" in prompt
    assert prompt.endswith("resume el archivo")


def test_error_del_provider_gemini_se_mapea_a_502(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, json={"error": {"code": 429, "message": "cuota agotada"}}
        )

    registry = ProviderRegistry()
    register_gemini_providers(
        registry, api_key="test-key", transport=httpx.MockTransport(handler)
    )
    previous = get_registry()
    set_registry(registry)
    try:
        response = TestClient(server.app).post(
            "/v1/chat/completions",
            json={
                "model": GEMINI_FLASH,
                "messages": [{"role": "user", "content": "hola"}],
            },
        )
    finally:
        set_registry(previous)
    assert response.status_code == 502
    assert "429" in response.json()["detail"]
