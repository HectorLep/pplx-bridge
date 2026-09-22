"""Tests del índice invertido de N-gramas."""
from __future__ import annotations

import time

from src.engine.indexer import NGramIndexer


def test_posting_lookup_is_o1(indexer: NGramIndexer) -> None:
    """Cada N-grama indexado devuelve su posting list (dict lookup)."""
    assert indexer.ngram_count > 0
    # Al menos un bigrama conocido de 'casa': '^c' con centinela.
    posting: set[int] = indexer.posting("^c")
    assert isinstance(posting, set)
    assert len(posting) > 0


def test_contains_substring(indexer: NGramIndexer) -> None:
    """Subcadenas reales devuelven resultados con la subcadena incluida."""
    results: list[str] = indexer.contains("cas", limit=20)
    assert len(results) > 0
    from src.engine.trie import normalize as _norm

    assert all("cas" in _norm(r) for r in results)


def test_contains_tilde_insensitive(indexer: NGramIndexer) -> None:
    """'arbol' encuentra 'árbol' (insensible a tildes)."""
    results: list[str] = indexer.contains("arbol", limit=20)
    assert "árbol" in results or "arbol" in results


def test_contains_enie(indexer: NGramIndexer) -> None:
    """'ño' encuentra 'niño' pero 'nino' (sin ñ) no colapsa igual."""
    with_enie: list[str] = indexer.contains("ño", limit=20)
    assert any("niño" in r or "niña" in r or "otoño" in r for r in with_enie)


def test_contains_no_false_negatives(indexer: NGramIndexer, words: list[str]) -> None:
    """Toda palabra con la subcadena normalizada aparece (recall exacto).

    Oráculo: escaneo lineal sobre el vocabulario. El índice no debe tener
    falsos negativos (recall) ni falsos positivos (precisión), por lo que los
    conjuntos normalizados deben ser exactamente iguales.
    """
    from src.engine.trie import normalize as _norm

    for key in ["cam", "cas", "arbol"]:
        expected: set[str] = {w for w in words if key in _norm(w)}
        got: set[str] = set(indexer.contains(key, limit=10**6))
        expected_norm: set[str] = {_norm(w) for w in expected}
        got_norm: set[str] = {_norm(w) for w in got}
        # Sin falsos negativos: todo lo esperado está en lo obtenido.
        assert expected_norm <= got_norm, f"falsos negativos para {key!r}"
        # Sin falsos positivos: nada extra fuera del oráculo.
        assert got_norm <= expected_norm, f"falsos positivos para {key!r}"
        assert expected_norm == got_norm


def test_contains_empty_and_missing(indexer: NGramIndexer) -> None:
    """Casos borde: vacío y subcadena inexistente."""
    assert indexer.contains("", limit=10) == []
    assert indexer.contains("zzzqqq", limit=10) == []
    assert indexer.contains("cas", limit=0) == []


def test_latency_under_2ms(indexer: NGramIndexer) -> None:
    """p95 de contains < 2 ms."""
    queries: list[str] = ["cas", "arbol", "ño", "cam", "ción", "a", "es"]
    for _ in range(5):
        for q in queries:
            indexer.contains(q, limit=20)
    lat: list[float] = []
    for q in queries * 25:
        t0: float = time.perf_counter()
        indexer.contains(q, limit=20)
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    p95: float = lat[int(len(lat) * 0.95) - 1]
    assert p95 < 2.0, f"p95={p95:.3f} ms excede 2 ms"
