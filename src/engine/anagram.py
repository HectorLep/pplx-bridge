"""Solucionador de anagramas para español (insensible a tildes).

Estrategia:
    - Firma canónica = letras normalizadas ordenadas
      (``normalize`` preserva ñ, elimina tildes). La firma es la
      representación canónica del vector de frecuencia.
    - ``dict[firma, list[forma]]``: anagramas exactos en O(1) promedio.
    - Sub-anagramas: enumeración de los sub-multiconjuntos distintos de
      las letras disponibles y resolución de cada firma en O(1) sobre el
      índice por vector de frecuencia. Coste O(C) con
      C = prod(multiplicidad + 1), independiente de N (tamaño del
      vocabulario). Partición auxiliar por longitud para acotar el
      fallback de consultas patológicas.
    - Límite de escala documentado: si C > ``_MAX_SUBSETS`` (combinatoria
      de la consulta, no del diccionario) se usa un fallback acotado por
      longitud que solo recorre firmas con len en [min_len, len(letras)].
      Para el alfabeto español y fichas típicas (<=10 letras) C es de
      cientos o pocos miles de lookups, muy por debajo del p95 < 2 ms.
"""

from __future__ import annotations

from collections import Counter
from itertools import product

from .trie import normalize

__all__ = ["AnagramSolver"]

# Tope de enumeración de sub-multiconjuntos distintos de la consulta.
# Por encima se usa el fallback acotado por longitud (ver docstring).
_MAX_SUBSETS = 200_000


def signature(word: str) -> str:
    """Firma canónica de una palabra (letras ordenadas, sin espacios).

    Args:
        word: Palabra de entrada.

    Returns:
        Firma ordenada. ``""`` si no hay letras.
    """
    key: str = normalize(word.strip()).replace(" ", "")
    return "".join(sorted(key))


def _estimate_subsets(pool: Counter[str]) -> int:
    """Estima prod(m_i + 1) con cortocircuito sobre ``_MAX_SUBSETS``."""
    total: int = 1
    for c in pool.values():
        total *= c + 1
        if total > _MAX_SUBSETS:
            break
    return total


class AnagramSolver:
    """Índice de anagramas exactos y sub-anagramas."""

    def __init__(self) -> None:
        # firma -> formas originales (índice por vector de frecuencia).
        self._index: dict[str, list[str]] = {}
        # longitud de firma -> firmas (partición por longitud).
        self._sigs_by_length: dict[int, list[str]] = {}
        # firma -> Counter cacheado (para el fallback acotado).
        self._sig_counters: dict[str, Counter[str]] = {}
        self._seen: set[str] = set()  # formas exactas ya añadidas
        self._size: int = 0

    def __len__(self) -> int:
        """Número de formas indexadas."""
        return self._size

    @property
    def group_count(self) -> int:
        """Número de firmas distintas."""
        return len(self._index)

    def add_word(self, word: str) -> None:
        """Añade una palabra al índice (idempotente por forma exacta)."""
        if not word or not word.strip():
            return
        original: str = word.strip()
        if original in self._seen:
            return
        sig: str = signature(original)
        if not sig:
            return
        self._seen.add(original)
        bucket: list[str] | None = self._index.get(sig)
        if bucket is None:
            self._index[sig] = [original]
            self._sigs_by_length.setdefault(len(sig), []).append(sig)
            self._sig_counters[sig] = Counter(sig)
        elif original not in bucket:
            bucket.append(original)
        self._size += 1

    def build(self, words: list[str]) -> int:
        """Indexa una lista. Devuelve nº de formas añadidas."""
        before: int = self._size
        for w in words:
            if w and w.strip() and not w.strip().startswith("#"):
                self.add_word(w)
        return self._size - before

    def anagrams_of(self, word: str, include_self: bool = False) -> list[str]:
        """Anagramas exactos de una palabra en O(1) promedio.

        Insensible a tildes/mayúsculas: ``oído`` encuentra ``odio``.

        Args:
            word: Palabra consulta.
            include_self: Si True, incluye la propia palabra consultada
                (comparación por firma normalizada, no por identidad).

        Returns:
            Lista ordenada de anagramas.
        """
        sig: str = signature(word)
        if not sig:
            return []
        bucket: list[str] = self._index.get(sig, [])
        if include_self:
            return sorted(bucket)
        norm_query: str = normalize(word.strip())
        return sorted(f for f in bucket if normalize(f) != norm_query)

    def _sub_anagrams_fallback(
        self, pool: Counter[str], key_len: int, min_len: int, limit: int
    ) -> list[str]:
        """Fallback acotado por longitud (solo consultas patológicas).

        Recorre únicamente las firmas con longitud en
        [min_len, len(letras)] en vez de todo el vocabulario.
        """
        out: list[str] = []
        pool_get = pool.get
        for length in range(min_len, key_len + 1):
            for sig in self._sigs_by_length.get(length, ()):
                counter: Counter[str] = self._sig_counters[sig]
                ok: bool = True
                for ch, count in counter.items():
                    if count > pool_get(ch, 0):
                        ok = False
                        break
                if ok:
                    out.extend(self._index[sig])
        out.sort(key=lambda w: (-len(normalize(w).replace(" ", "")), w))
        return out[:limit]

    def sub_anagrams(
        self, letters: str, min_len: int = 2, limit: int = 50
    ) -> list[str]:
        """Palabras formables con un subconjunto de las letras dadas.

        Resuelve cada sub-multiconjunto distinto de ``letters`` en O(1)
        sobre el índice por vector de frecuencia: coste O(C) con
        C = prod(m_i + 1), independiente del tamaño del vocabulario.

        Args:
            letters: Letras disponibles (p. ej. fichas).
            min_len: Longitud mínima de la palabra.
            limit: Máximo de resultados.

        Returns:
            Palabras ordenadas por longitud desc. y alfabeto.
        """
        if limit <= 0:
            return []
        key: str = normalize(letters.strip()).replace(" ", "")
        if not key or min_len < 1:
            return []
        if len(key) < min_len:
            return []
        pool: Counter[str] = Counter(key)
        if _estimate_subsets(pool) > _MAX_SUBSETS:
            return self._sub_anagrams_fallback(pool, len(key), min_len, limit)
        # Caracteres en orden para que la firma generada ya salga ordenada
        # (coincide con signature(): "".join(sorted(key))).
        items: list[tuple[str, int]] = sorted(pool.items())
        chars: list[str] = [ch for ch, _ in items]
        ranges: list[range] = [range(count + 1) for _, count in items]
        out: list[str] = []
        for combo in product(*ranges):
            total: int = sum(combo)
            if total < min_len or total == 0:
                continue
            sig: str = "".join(ch * n for ch, n in zip(chars, combo))
            bucket: list[str] | None = self._index.get(sig)
            if bucket:
                out.extend(bucket)
        out.sort(key=lambda w: (-len(normalize(w).replace(" ", "")), w))
        return out[:limit]

    def groups(self, min_size: int = 2) -> list[list[str]]:
        """Grupos de anagramas con al menos min_size miembros."""
        return sorted(
            (sorted(v) for v in self._index.values() if len(v) >= min_size),
            key=lambda g: (-len(g), g),
        )

    def stats(self) -> dict[str, int]:
        """Estadísticas del índice."""
        return {"words": self._size, "signatures": len(self._index)}
