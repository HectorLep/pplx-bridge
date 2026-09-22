"""Fixtures compartidas: carga el diccionario real."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.engine.anagram import AnagramSolver
from src.engine.indexer import NGramIndexer
from src.engine.trie import RadixTree, Trie

DATA = Path(__file__).resolve().parent.parent / "data" / "dictionary_es.txt"


def load_words() -> list[str]:
    """Lee el diccionario descartando comentarios y vacías."""
    return [
        line.strip()
        for line in DATA.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


@pytest.fixture(scope="session")
def words() -> list[str]:
    """Vocabulario completo del diccionario."""
    return load_words()


@pytest.fixture(scope="session")
def trie(words: list[str]) -> Trie:
    """Trie precargado con el diccionario."""
    t = Trie()
    for w in words:
        t.insert(w)
    return t


@pytest.fixture(scope="session")
def radix(words: list[str]) -> RadixTree:
    """Radix precargado con el diccionario."""
    r = RadixTree()
    for w in words:
        r.insert(w)
    return r


@pytest.fixture(scope="session")
def indexer(words: list[str]) -> NGramIndexer:
    """Índice N-gramas precargado."""
    idx = NGramIndexer()
    idx.build(words)
    return idx


@pytest.fixture(scope="session")
def solver(words: list[str]) -> AnagramSolver:
    """Solucionador de anagramas precargado."""
    s = AnagramSolver()
    s.build(words)
    return s
