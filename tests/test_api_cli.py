"""Tests de la API FastAPI (TestClient) y de la CLI."""
from __future__ import annotations

from fastapi.testclient import TestClient

from src import server
from src.cli import main as cli_main


def _client() -> TestClient:
    server.load_dictionary()
    return TestClient(server.app)


def test_health() -> None:
    """GET /health responde ok con nº de palabras."""
    c: TestClient = _client()
    r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["words"] > 100


def test_autocomplete_endpoint() -> None:
    """GET /autocomplete devuelve resultados y latencia."""
    c: TestClient = _client()
    r = c.get("/autocomplete", params={"prefix": "cas", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] > 0
    assert body["latency_ms"] < 50.0  # incluye overhead HTTP en proceso


def test_search_endpoint_enie() -> None:
    """GET /search distingue ñ de n."""
    c: TestClient = _client()
    assert c.get("/search", params={"word": "niño"}).json()["found"] is True
    assert c.get("/search", params={"word": "nino"}).json()["found"] is False
    assert c.get("/search", params={"word": "árbol"}).json()["found"] is True


def test_anagram_endpoint() -> None:
    """GET /anagram encuentra el grupo amor/roma."""
    c: TestClient = _client()
    r = c.get("/anagram", params={"word": "amor"})
    assert r.status_code == 200
    assert "roma" in r.json()["results"]


def test_contains_endpoint() -> None:
    """GET /contains usa el índice N-gramas."""
    c: TestClient = _client()
    r = c.get("/contains", params={"substring": "ño", "limit": 20})
    assert r.status_code == 200
    assert r.json()["count"] > 0


def test_stats_endpoint() -> None:
    """GET /stats expone contadores coherentes."""
    c: TestClient = _client()
    body = c.get("/stats").json()
    assert body["words"] > 100
    assert body["trie_nodes"] > body["words"]
    assert body["ngrams"] > 0


def test_cli_autocomplete(capsys: object) -> None:
    """CLI autocomplete imprime resultados (exit 0)."""
    rc: int = cli_main(["autocomplete", "cas", "--limit", "3"])
    assert rc == 0


def test_cli_search_found_and_missing() -> None:
    """CLI search: 0 si existe, 1 si no."""
    assert cli_main(["search", "árbol"]) == 0
    assert cli_main(["search", "zzz-no-existe"]) == 1
