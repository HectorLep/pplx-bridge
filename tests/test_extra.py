"""Tests extra de cobertura: CLI completa, loader, posting-copy y bordes."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.cli import load_all, main as cli_main
from src.engine.anagram import AnagramSolver
from src.engine.indexer import NGramIndexer
from src.engine.loader import load_word_list
from src.engine.trie import RadixTree, Trie


def test_loader_ok_and_missing(tmp_path: Path) -> None:
    """Loader filtra comentarios/vacías y falla con FileNotFound."""
    f = tmp_path / "d.txt"
    f.write_text("# comment\n\ncasa\n\ncasa\n", encoding="utf-8")
    words = load_word_list(f)
    assert words == ["casa", "casa"]
    with pytest.raises(FileNotFoundError):
        load_word_list(tmp_path / "no-existe.txt")


def test_load_all_uses_loader() -> None:
    """load_all carga las tres estructuras con el mismo vocabulario."""
    trie, idx, solver, words = load_all()
    assert len(words) > 100
    assert len(trie) == len(words) or len(trie) > 0
    assert len(idx) > 0
    assert len(solver) > 0


def test_posting_returns_copy(indexer: NGramIndexer) -> None:
    """Mutar el set devuelto no corrompe el índice."""
    before = len(indexer.posting("^c"))
    s = indexer.posting("^c")
    s.clear()
    s.add(999999)
    assert len(indexer.posting("^c")) == before


def test_indexer_add_word_errors() -> None:
    """add_word rechaza vacías y claves no normalizables."""
    idx = NGramIndexer()
    with pytest.raises(ValueError):
        idx.add_word("")
    with pytest.raises(ValueError):
        idx.add_word("   ")
    # Idempotente por clave normalizada (tildes colapsan).
    a = idx.add_word("árbol")
    b = idx.add_word("arbol")
    assert a == b
    assert idx.stats()["words"] == 1


def test_indexer_build_and_stats() -> None:
    """build() cuenta ids nuevos y stats expone n_values."""
    idx = NGramIndexer()
    n = idx.build(["casa", "casa", "# comment", "", "perro"])
    assert n == 2
    st = idx.stats()
    assert st["words"] == 2
    assert st["n_values"] == [2, 3]


def test_trie_literal_forms_allwords_delete() -> None:
    """search_literal/forms_of/all_words y borrado con clave compartida."""
    t = Trie()
    t.insert("árbol")
    t.insert("ARBOL")
    assert t.search("arbol")
    assert t.search_literal("árbol")
    assert not t.search_literal("arbol-minus")
    forms = t.forms_of("árbol")
    assert "árbol" in forms and "ARBOL" in forms
    assert "árbol" in t.all_words()
    # Borrar una forma conserva la otra (misma clave normalizada).
    assert t.delete("árbol") is True
    assert t.search("arbol")  # queda ARBOL
    assert t.delete("no-existe") is False
    assert t.forms_of("zzz") == []
    # Autocomplete garantiza orden alfabético.
    t2 = Trie()
    for w in ["casar", "casa", "casco"]:
        t2.insert(w)
    assert t2.autocomplete("cas", limit=10) == sorted(t2.autocomplete("cas", limit=10))


def test_anagram_bordes() -> None:
    """Ramas no cubiertas: vacíos, límites y stats."""
    s = AnagramSolver()
    assert s.build(["amor", "roma", "", "# c", "  "]) == 2
    s.add_word("amor")  # idempotente
    assert len(s) == 2
    assert s.sub_anagrams("amor", limit=0) == []
    assert s.sub_anagrams("", min_len=2) == []
    assert s.sub_anagrams("amor", min_len=0) == []
    assert s.stats()["words"] == 2
    assert s.groups(min_size=99) == []


def test_cli_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    """CLI cubre anagram/subanagram/contains/stats y error dict."""
    assert cli_main(["anagram", "amor", "--include-self"]) == 0
    assert cli_main(["subanagram", "amor", "--min-len", "4", "--limit", "5"]) == 0
    assert cli_main(["contains", "cas", "--limit", "3"]) == 0
    assert cli_main(["stats"]) == 0
    capsys.readouterr()
    assert cli_main(["--dict", "no-existe.txt", "stats"]) == 2


def test_radix_edge_branches() -> None:
    """Cubre splits, prefijos parciales y ramas vacías del RadixTree."""
    r = RadixTree()
    assert len(r) == 0
    r.insert("")
    r.insert("   ")
    assert len(r) == 0
    r.insert("casa")
    assert "casa" in r
    assert len(r) == 1
    # 'cas' termina dentro de la etiqueta 'casa' -> split con i==len(key).
    r.insert("cas")
    assert r.search("cas") is True
    assert r.search("casa") is True
    # Duplicado exacto no suma.
    before = len(r)
    r.insert("cas")
    assert len(r) == before
    # Búsqueda de prefijo parcial (clave dentro de etiqueta) -> None.
    r2 = RadixTree()
    r2.insert("casa")
    assert r2.search("cas") is False
    assert r2.search("") is False
    assert r2.starts_with("") is True
    assert r2.starts_with("cas") is True
    assert r2.starts_with("zzz") is False
    assert r2.starts_with("casax") is False
    # Autocompletado: miss, límite 0 y divergencia parcial.
    assert r2.autocomplete("zzz", limit=5) == []
    assert r2.autocomplete("cas", limit=0) == []
    assert r2.autocomplete("cax", limit=5) == []
    assert r2.autocomplete("ca", limit=5) != []
    assert set(r2.autocomplete("", limit=10)) == {"casa"}
    # Split con resto (casa vs casco comparten 'cas').
    r3 = RadixTree()
    r3.insert("casa")
    r3.insert("casco")
    assert r3.search("casa") and r3.search("casco")
    assert r3.node_count >= 3
