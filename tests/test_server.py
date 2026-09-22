"""Tests completos de los endpoints FastAPI (TestClient) y DictionaryService."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src import server
from src.engine.trie import normalize
from src.server import DictionaryService, get_service


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Cliente con el diccionario real cargado (restaura al final)."""
    server.load_dictionary()
    return TestClient(server.app)


def test_service_singleton() -> None:
    """get_service devuelve siempre el mismo objeto."""
    assert get_service() is server.get_service()
    assert isinstance(get_service(), DictionaryService)


def test_no_globales_mutables_desprotegidos() -> None:
    """El servicio expone el estado; no hay Trie global reasignable sin lock."""
    svc = get_service()
    assert isinstance(svc.trie, object)
    assert isinstance(svc.indexer, object)
    assert isinstance(svc.anagrams, object)
    # Los proxies legacy __getattr__/__dir__ se eliminaron: el módulo no
    # expone trie/indexer/anagrams ni contadores como atributos. La única
    # vía es el servicio (get_service() / load_dictionary()).
    for legacy in ("trie", "indexer", "anagrams", "_word_count", "_load_ms"):
        assert not hasattr(server, legacy), legacy
        assert legacy not in dir(server), legacy
    assert "__getattr__" not in vars(server)
    assert "__dir__" not in vars(server)


def test_health(client: TestClient) -> None:
    """GET /health responde ok con conteo y latencia."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["words"] > 100
    assert body["load_ms"] >= 0.0


def test_autocomplete_endpoint(client: TestClient) -> None:
    """GET /autocomplete: resultados, conteo y latencia coherentes."""
    r = client.get("/autocomplete", params={"prefix": "cas", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "cas"
    assert body["count"] == len(body["results"])
    assert body["count"] > 0
    assert body["count"] <= 5
    assert body["latency_ms"] >= 0.0
    assert any(normalize(w).startswith("cas") for w in body["results"])


def test_autocomplete_tilde_insensitive(client: TestClient) -> None:
    """'arbol' encuentra 'árbol' vía endpoint."""
    body = client.get("/autocomplete", params={"prefix": "arbol", "limit": 10}).json()
    assert any("rbol" in normalize(w) for w in body["results"])


def test_autocomplete_validacion_y_vacios(client: TestClient) -> None:
    """Límites fuera de rango → 422; prefijo sin resultados → vacío."""
    assert client.get("/autocomplete", params={"prefix": "cas", "limit": 0}).status_code == 422
    assert client.get("/autocomplete", params={"prefix": "cas", "limit": 101}).status_code == 422
    body = client.get("/autocomplete", params={"prefix": "zzz-sin", "limit": 5}).json()
    assert body["results"] == [] and body["count"] == 0


def test_search_endpoint(client: TestClient) -> None:
    """GET /search: encontrada, formas y normalizado."""
    body = client.get("/search", params={"word": "árbol"}).json()
    assert body["found"] is True
    assert body["normalized"] == normalize("árbol")
    assert "árbol" in body["forms"] or "arbol" in body["forms"]
    assert body["latency_ms"] >= 0.0


def test_search_enie(client: TestClient) -> None:
    """ñ != n también en el endpoint."""
    assert client.get("/search", params={"word": "niño"}).json()["found"] is True
    assert client.get("/search", params={"word": "nino"}).json()["found"] is False


def test_search_missing(client: TestClient) -> None:
    """Palabra inexistente: found False y forms vacío."""
    body = client.get("/search", params={"word": "zzz-no-existe"}).json()
    assert body["found"] is False
    assert body["forms"] == []


def test_search_requiere_word(client: TestClient) -> None:
    """Sin ?word= la API responde 422."""
    assert client.get("/search").status_code == 422


def test_anagram_endpoint(client: TestClient) -> None:
    """GET /anagram encuentra el grupo amor/roma."""
    body = client.get("/anagram", params={"word": "amor"}).json()
    assert "roma" in body["results"]
    assert body["count"] == len(body["results"])
    assert body["query"] == "amor"


def test_anagram_include_self(client: TestClient) -> None:
    """include_self=true incluye la propia palabra."""
    sin = client.get("/anagram", params={"word": "amor"}).json()["results"]
    con = client.get("/anagram", params={"word": "amor", "include_self": True}).json()["results"]
    assert "roma" in sin and "roma" in con
    assert "amor" in con


def test_anagram_missing_y_limite(client: TestClient) -> None:
    """Sin anagramas → vacío; limit recorta."""
    assert client.get("/anagram", params={"word": "zzzqqq"}).json()["results"] == []
    body = client.get("/anagram", params={"word": "amor", "limit": 1}).json()
    assert body["count"] <= 1


def test_subanagram_endpoint(client: TestClient) -> None:
    """GET /subanagram devuelve palabras formables."""
    body = client.get(
        "/subanagram", params={"letters": "amor", "min_len": 4, "limit": 50}
    ).json()
    assert {"amor", "roma", "mora", "ramo"} <= set(body["results"])
    assert body["count"] == len(body["results"])


def test_contains_endpoint(client: TestClient) -> None:
    """GET /contains usa el índice N-gramas."""
    body = client.get("/contains", params={"substring": "ño", "limit": 20}).json()
    assert body["count"] > 0
    assert body["query"] == "ño"
    assert body["count"] == len(body["results"])


def test_contains_tilde_endpoint(client: TestClient) -> None:
    """'arbol' encuentra 'árbol' por subcadena."""
    body = client.get("/contains", params={"substring": "arbol", "limit": 20}).json()
    assert any("arbol" in normalize(w) for w in body["results"])


def test_contains_missing(client: TestClient) -> None:
    """Subcadena inexistente → vacío."""
    body = client.get("/contains", params={"substring": "zzzqqq", "limit": 10}).json()
    assert body["results"] == [] and body["count"] == 0


def test_stats_endpoint(client: TestClient) -> None:
    """GET /stats expone contadores coherentes."""
    body = client.get("/stats").json()
    assert body["words"] > 100
    assert body["trie_nodes"] > body["words"]
    assert body["trie_keys"] > 0
    assert body["ngrams"] > 0
    assert body["anagram_signatures"] > 0
    assert body["load_ms"] >= 0.0


def test_stats_coherente_con_health(client: TestClient) -> None:
    """words de /stats y /health coinciden."""
    assert client.get("/stats").json()["words"] == client.get("/health").json()["words"]


def test_load_dictionary_ok_and_missing(tmp_path: Path) -> None:
    """load_dictionary carga y falla con FileNotFound sin corromper estado."""
    svc = DictionaryService()
    f = tmp_path / "mini.txt"
    f.write_text("casa\nperro\n", encoding="utf-8")
    assert svc.load(f) == 2
    assert svc.word_count == 2
    assert svc.search("casa") is True
    before = svc.word_count
    with pytest.raises(FileNotFoundError):
        svc.load(tmp_path / "no-existe.txt")
    # El estado anterior se conserva intacto tras el fallo.
    assert svc.word_count == before
    assert svc.search("casa") is True


def test_load_dictionary_singleton_recarga_atomica(tmp_path: Path) -> None:
    """Recargar el singleton cambia /health y se puede restaurar."""
    real_words = get_service().word_count
    f = tmp_path / "mini.txt"
    f.write_text("casa\nperro\n", encoding="utf-8")
    try:
        assert server.load_dictionary(f) == 2
        c = TestClient(server.app)
        assert c.get("/health").json()["words"] == 2
        assert c.get("/search", params={"word": "casa"}).json()["found"] is True
        assert c.get("/search", params={"word": "árbol"}).json()["found"] is False
    finally:
        server.load_dictionary()  # restaura diccionario real
    assert get_service().word_count == real_words


def test_service_stats_data(tmp_path: Path) -> None:
    """stats_data captura una instantánea coherente."""
    svc = DictionaryService()
    f = tmp_path / "mini.txt"
    f.write_text("casa\ncasar\namor\nroma\n", encoding="utf-8")
    svc.load(f)
    data = svc.stats_data()
    assert data["words"] == 4
    assert data["trie_nodes"] >= data["trie_keys"]
    assert data["ngrams"] > 0
    assert data["anagram_signatures"] >= 1
    assert svc.autocomplete("cas", limit=10) != []
    assert svc.contains("cas", limit=10) != []
    assert "roma" in svc.anagrams_of("amor")


def test_service_concurrent_reads_no_crash(tmp_path: Path) -> None:
    """Lecturas concurrentes durante una recarga no rompen invariantes."""
    svc = DictionaryService()
    f = tmp_path / "mini.txt"
    f.write_text("casa\nperro\ngato\n", encoding="utf-8")
    svc.load(f)
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            for _ in range(50):
                svc.autocomplete("ca", limit=5)
                svc.search("casa")
                svc.contains("as", limit=5)
                svc.stats_data()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def writer() -> None:
        try:
            for _ in range(5):
                svc.load(f)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
