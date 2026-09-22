"""Tests del Trie/Radix: tildes, ñ, prefijos, borrado y latencia."""
from __future__ import annotations

import time

from src.engine.trie import RadixTree, Trie, normalize


def test_normalize_tildes() -> None:
    """Las tildes colapsan; la ñ se preserva."""
    assert normalize("árbol") == "arbol"
    assert normalize("CAMIÓN") == "camion"
    assert normalize("pingüino") == "pinguino"
    assert normalize("niño") == "niño"
    assert normalize("niño") != normalize("nino")
    assert normalize("España") == "españa"


def test_search_accent_insensitive(trie: Trie) -> None:
    """Búsqueda insensible a tildes/mayúsculas."""
    assert trie.search("arbol")
    assert trie.search("árbol")
    assert trie.search("ARBOL")
    assert trie.search("camion")
    assert trie.search("niño")
    assert not trie.search("nino")  # ñ != n
    assert not trie.search("xyz-no-existe")


def test_starts_with_and_autocomplete(trie: Trie) -> None:
    """Prefijos y autocompletado devuelven formas originales."""
    assert trie.starts_with("cas")
    assert trie.starts_with("niñ")
    assert not trie.starts_with("zzz")
    results: list[str] = trie.autocomplete("cas", limit=10)
    assert len(results) > 0
    assert any(normalize(r).startswith("cas") for r in results)
    assert "casa" in results or "casar" in results


def test_autocomplete_limit(trie: Trie) -> None:
    """El límite se respeta y limit<=0 devuelve vacío."""
    assert len(trie.autocomplete("ca", limit=3)) <= 3
    assert trie.autocomplete("ca", limit=0) == []
    assert trie.autocomplete("zzz-sin-resultados", limit=5) == []


def test_insert_delete_roundtrip() -> None:
    """Insertar y borrar una palabra temporal."""
    t = Trie()
    t.insert("prueba")
    assert t.search("prueba")
    assert t.delete("prueba") is True
    assert not t.search("prueba")
    assert t.delete("prueba") is False


def test_radix_parity(trie: Trie, radix: RadixTree) -> None:
    """Radix y Trie responden igual en búsquedas clave."""
    for word in ["árbol", "niño", "casa", "amor", "xyz-no-existe"]:
        assert radix.search(word) == trie.search(word), word
    for prefix in ["cas", "ar", "niñ", "am"]:
        assert radix.starts_with(prefix) == trie.starts_with(prefix), prefix
    # Autocompletados con el mismo conjunto (orden puede variar).
    assert set(radix.autocomplete("cas", limit=20)) == set(trie.autocomplete("cas", limit=20))


def test_radix_memory_saving(trie: Trie, radix: RadixTree) -> None:
    """El radix usa menos o igual nº de nodos que el trie clásico."""
    assert radix.node_count <= trie.node_count


def test_latency_under_2ms(trie: Trie) -> None:
    """p95 de autocomplete < 2 ms sobre el diccionario real."""
    queries: list[str] = ["cas", "cam", "ar", "niñ", "am", "c", "a", "es"]
    lat: list[float] = []
    for _ in range(5):  # calentamiento
        for q in queries:
            trie.autocomplete(q, limit=10)
    for q in queries * 25:  # 200 muestras
        t0: float = time.perf_counter()
        trie.autocomplete(q, limit=10)
        lat.append((time.perf_counter() - t0) * 1000.0)
    lat.sort()
    p95: float = lat[int(len(lat) * 0.95) - 1]
    assert p95 < 2.0, f"p95={p95:.3f} ms excede 2 ms"
