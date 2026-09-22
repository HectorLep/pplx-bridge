"""Servidor FastAPI del motor léxico español.

Endpoints:
    GET /health                      → estado + nº de palabras
    GET /autocomplete?prefix=&limit= → autocompletado Trie (insensible a tildes)
    GET /search?word=                → existencia exacta (insensible a tildes)
    GET /anagram?word=&include_self= → anagramas exactos
    GET /subanagram?letters=&min_len=&limit=
    GET /contains?substring=&limit=  → subcadena vía índice N-gramas O(1)
    GET /stats                       → estadísticas de estructuras

El diccionario se carga una vez en el startup dentro de un
:class:`DictionaryService` atómico y protegido por candado: la recarga
construye las tres estructuras en local y las intercambia bajo lock, por lo
que los lectores concurrentes nunca ven un estado a medio construir y no
existe estado global mutable desprotegido.
"""

from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from .engine.anagram import AnagramSolver
from .engine.indexer import NGramIndexer
from .engine.loader import load_word_list
from .engine.trie import Trie, normalize

DEFAULT_DICT = Path(__file__).resolve().parent.parent / "data" / "dictionary_es.txt"

__all__ = [
    "DEFAULT_DICT",
    "DictionaryService",
    "get_service",
    "load_dictionary",
    "app",
    "HealthOut",
    "WordsOut",
    "SearchOut",
    "StatsOut",
]


class DictionaryService:
    """Contenedor atómico y thread-safe de las estructuras del diccionario.

    Toda la mutación ocurre en :meth:`load`, que construye objetos nuevos en
    local y los publica bajo un ``RLock`` en una sección crítica corta. Las
    consultas capturan una instantánea de referencias bajo el mismo candado y
    luego consultan sin retenerlo (las estructuras son de solo-lectura tras
    publicarse, por lo que la lectura concurrente es segura).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trie: Trie = Trie()
        self._indexer: NGramIndexer = NGramIndexer()
        self._anagrams: AnagramSolver = AnagramSolver()
        self._word_count: int = 0
        self._load_ms: float = 0.0

    def load(self, path: str | Path = DEFAULT_DICT) -> int:
        """Carga el diccionario de forma atómica.

        Args:
            path: Ruta al .txt (una palabra por línea, UTF-8).

        Returns:
            Número de entradas cargadas.

        Raises:
            FileNotFoundError: Si no existe el fichero. En ese caso el
                estado anterior se conserva intacto.
        """
        t0: float = time.perf_counter()
        words: list[str] = load_word_list(path)
        new_trie = Trie()
        new_indexer = NGramIndexer()
        new_solver = AnagramSolver()
        for w in words:
            new_trie.insert(w)
            new_indexer.add_word(w)
            new_solver.add_word(w)
        load_ms: float = (time.perf_counter() - t0) * 1000.0
        with self._lock:
            self._trie = new_trie
            self._indexer = new_indexer
            self._anagrams = new_solver
            self._word_count = len(words)
            self._load_ms = load_ms
            return self._word_count

    def _snapshot(self) -> tuple[Trie, NGramIndexer, AnagramSolver, int, float]:
        """Captura atómica de referencias y contadores."""
        with self._lock:
            return (
                self._trie,
                self._indexer,
                self._anagrams,
                self._word_count,
                self._load_ms,
            )

    @property
    def word_count(self) -> int:
        """Número de entradas del diccionario cargado."""
        with self._lock:
            return self._word_count

    @property
    def load_ms(self) -> float:
        """Milisegundos de la última carga."""
        with self._lock:
            return self._load_ms

    @property
    def trie(self) -> Trie:
        """Trie actual (solo lectura; recargar vía :meth:`load`)."""
        with self._lock:
            return self._trie

    @property
    def indexer(self) -> NGramIndexer:
        """Índice N-gramas actual (solo lectura)."""
        with self._lock:
            return self._indexer

    @property
    def anagrams(self) -> AnagramSolver:
        """Solucionador de anagramas actual (solo lectura)."""
        with self._lock:
            return self._anagrams

    def autocomplete(self, prefix: str, limit: int = 10) -> list[str]:
        """Autocompleta un prefijo con el Trie."""
        trie, _, _, _, _ = self._snapshot()
        return trie.autocomplete(prefix, limit=limit)

    def search(self, word: str) -> bool:
        """Búsqueda exacta insensible a tildes."""
        trie, _, _, _, _ = self._snapshot()
        return trie.search(word)

    def forms_of(self, word: str) -> list[str]:
        """Formas originales registradas para una clave."""
        trie, _, _, _, _ = self._snapshot()
        return trie.forms_of(word)

    def anagrams_of(self, word: str, include_self: bool = False) -> list[str]:
        """Anagramas exactos de una palabra."""
        _, _, solver, _, _ = self._snapshot()
        return solver.anagrams_of(word, include_self=include_self)

    def sub_anagrams(self, letters: str, min_len: int = 2, limit: int = 50) -> list[str]:
        """Sub-anagramas formables con las letras dadas."""
        _, _, solver, _, _ = self._snapshot()
        return solver.sub_anagrams(letters, min_len=min_len, limit=limit)

    def contains(self, substring: str, limit: int = 20) -> list[str]:
        """Búsqueda por subcadena vía índice N-gramas."""
        _, indexer, _, _, _ = self._snapshot()
        return indexer.contains(substring, limit=limit)

    def stats_data(self) -> dict[str, float | int]:
        """Contadores coherentes capturados en una instantánea."""
        trie, indexer, solver, count, load_ms = self._snapshot()
        return {
            "words": count,
            "trie_nodes": trie.node_count,
            "trie_keys": trie.key_count,
            "ngrams": indexer.ngram_count,
            "anagram_signatures": solver.group_count,
            "load_ms": load_ms,
        }


_service = DictionaryService()


def get_service() -> DictionaryService:
    """Devuelve el servicio singleton (punto de inyección para tests)."""
    return _service


def load_dictionary(path: str | Path = DEFAULT_DICT) -> int:
    """Compat: carga el diccionario en el servicio singleton.

    Args:
        path: Ruta al .txt (una palabra por línea, UTF-8).

    Returns:
        Número de entradas cargadas.

    Raises:
        FileNotFoundError: Si no existe el fichero.
    """
    return _service.load(path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Carga el diccionario al arrancar (no falla si falta en tests)."""
    try:
        load_dictionary()
    except FileNotFoundError:
        pass
    yield


app = FastAPI(title="Motor Léxico ES", version="1.0.0", lifespan=lifespan)


# --- Modelos de respuesta ----------------------------------------------------


class HealthOut(BaseModel):
    """Estado del servicio."""

    status: str = Field(default="ok")
    words: int = Field(default=0)
    load_ms: float = Field(default=0.0)


class WordsOut(BaseModel):
    """Lista de palabras con latencia de consulta."""

    query: str
    results: list[str]
    count: int
    latency_ms: float


class SearchOut(BaseModel):
    """Resultado de búsqueda exacta."""

    word: str
    normalized: str
    found: bool
    forms: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0


class StatsOut(BaseModel):
    """Estadísticas de las estructuras."""

    words: int
    trie_nodes: int
    trie_keys: int
    ngrams: int
    anagram_signatures: int
    load_ms: float


def _timed() -> float:
    return time.perf_counter()


def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    """Estado del servicio y tamaño del diccionario."""
    _, _, _, count, load_ms = _service._snapshot()
    return HealthOut(status="ok", words=count, load_ms=round(load_ms, 3))


@app.get("/autocomplete", response_model=WordsOut)
def autocomplete(
    prefix: str = Query(default="", description="Prefijo (insensible a tildes)"),
    limit: int = Query(default=10, ge=1, le=100),
) -> WordsOut:
    """Autocompleta un prefijo con el Trie. O(p + k)."""
    t0: float = _timed()
    results: list[str] = _service.autocomplete(prefix, limit=limit)
    return WordsOut(
        query=prefix, results=results, count=len(results), latency_ms=round(_elapsed_ms(t0), 3)
    )


@app.get("/search", response_model=SearchOut)
def search(word: str = Query(description="Palabra a buscar")) -> SearchOut:
    """Búsqueda exacta insensible a tildes/ñ-distinguible."""
    t0: float = _timed()
    found: bool = _service.search(word)
    forms: list[str] = _service.forms_of(word) if found else []
    return SearchOut(
        word=word,
        normalized=normalize(word),
        found=found,
        forms=forms,
        latency_ms=round(_elapsed_ms(t0), 3),
    )


@app.get("/anagram", response_model=WordsOut)
def anagram(
    word: str = Query(description="Palabra base"),
    include_self: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> WordsOut:
    """Anagramas exactos en O(1) por firma."""
    t0: float = _timed()
    results: list[str] = _service.anagrams_of(word, include_self=include_self)[:limit]
    return WordsOut(
        query=word, results=results, count=len(results), latency_ms=round(_elapsed_ms(t0), 3)
    )


@app.get("/subanagram", response_model=WordsOut)
def subanagram(
    letters: str = Query(description="Letras disponibles"),
    min_len: int = Query(default=2, ge=1, le=30),
    limit: int = Query(default=50, ge=1, le=200),
) -> WordsOut:
    """Sub-anagramas (palabras formables con las letras dadas)."""
    t0: float = _timed()
    results: list[str] = _service.sub_anagrams(letters, min_len=min_len, limit=limit)
    return WordsOut(
        query=letters, results=results, count=len(results), latency_ms=round(_elapsed_ms(t0), 3)
    )


@app.get("/contains", response_model=WordsOut)
def contains(
    substring: str = Query(description="Subcadena a buscar"),
    limit: int = Query(default=20, ge=1, le=100),
) -> WordsOut:
    """Búsqueda por subcadena vía índice invertido de N-gramas."""
    t0: float = _timed()
    results: list[str] = _service.contains(substring, limit=limit)
    return WordsOut(
        query=substring, results=results, count=len(results), latency_ms=round(_elapsed_ms(t0), 3)
    )


@app.get("/stats", response_model=StatsOut)
def stats() -> StatsOut:
    """Estadísticas de memoria/estructuras."""
    data = _service.stats_data()
    return StatsOut(
        words=int(data["words"]),
        trie_nodes=int(data["trie_nodes"]),
        trie_keys=int(data["trie_keys"]),
        ngrams=int(data["ngrams"]),
        anagram_signatures=int(data["anagram_signatures"]),
        load_ms=round(float(data["load_ms"]), 3),
    )
