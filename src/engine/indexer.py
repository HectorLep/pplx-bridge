"""Índice invertido de N-gramas para búsqueda por subcadena en español.

Diseño:
    - Cada palabra normalizada (insensible a tildes, ñ preservada) se
      descompone en N-gramas (por defecto bigramas y trigramas con
      centinelas ``^`` y ``$``).
    - El índice es ``dict[ngram, set[id]]``: la búsqueda de cada N-grama
      es O(1) promedio; la intersección de posting lists resuelve la
      consulta por subcadena sin escanear el diccionario.
    - Se guarda ``id -> forma original`` y ``id -> clave normalizada``
      para verificación exacta del candidato (evita falsos positivos
      del filtrado por N-gramas).

Ejemplo:
    "casa" -> bigramas {^c, ca, as, sa, a$} ; trigramas {^ca, cas, asa, sa$}
"""

from __future__ import annotations

from .trie import normalize

__all__ = ["NGramIndexer"]


class NGramIndexer:
    """Índice invertido de N-gramas con lookup O(1) por N-grama.

    Args:
        n_values: Tamaños de N-grama a indexar. Por defecto (2, 3).
    """

    __slots__ = ("n_values", "_index", "_words", "_keys", "_key_to_id")

    def __init__(self, n_values: tuple[int, ...] = (2, 3)) -> None:
        if not n_values:
            raise ValueError("n_values no puede estar vacío")
        cleaned: list[int] = sorted(set(n_values))
        for n in cleaned:
            if not isinstance(n, int) or n <= 0:
                raise ValueError(f"n_values debe contener enteros > 0, recibido: {n_values!r}")
        self.n_values: tuple[int, ...] = tuple(cleaned)
        # ngram -> set de ids de palabra. Cada acceso es O(1) promedio.
        self._index: dict[str, set[int]] = {}
        # id -> lista tipada de formas originales que comparten la misma
        # clave normalizada (p. ej. ["árbol", "ARBOL"]). Sin concatenaciones
        # frágiles con separadores: cada variante es un elemento propio.
        self._words: list[list[str]] = []
        self._keys: list[str] = []  # id -> clave normalizada
        self._key_to_id: dict[str, int] = {}

    def __len__(self) -> int:
        """Número de claves normalizadas distintas (ids)."""
        return len(self._words)

    @property
    def form_count(self) -> int:
        """Número total de formas originales (incluye variantes)."""
        return sum(len(forms) for forms in self._words)

    def forms_of_id(self, wid: int) -> list[str]:
        """Formas originales de un id (copia para evitar mutación externa)."""
        return list(self._words[wid])

    def all_forms(self) -> list[str]:
        """Todas las formas originales ordenadas (solo depuración/tests)."""
        out: list[str] = [form for forms in self._words for form in forms]
        out.sort()
        return out

    @property
    def ngram_count(self) -> int:
        """Número de N-gramas distintos en el índice."""
        return len(self._index)

    @staticmethod
    def _pad(key: str) -> str:
        """Añade centinelas para anclar inicio/fin de palabra."""
        return f"^{key}$"

    @classmethod
    def _ngrams_of(cls, padded: str, n: int) -> set[str]:
        """N-gramas de tamaño n de una cadena ya acolchada."""
        if len(padded) < n:
            return {padded}
        return {padded[i : i + n] for i in range(len(padded) - n + 1)}

    def _ngrams_for_key(self, key: str) -> set[str]:
        """Todos los N-gramas (para cada n en n_values) de una clave."""
        padded: str = self._pad(key)
        out: set[str] = set()
        for n in self.n_values:
            out.update(self._ngrams_of(padded, n))
        return out

    def query_ngrams(self, query: str) -> set[str]:
        """N-gramas de consulta para búsqueda por subcadena.

        La consulta se tokeniza SIN centinelas ``^$``: los centinelas solo
        anclan los extremos en la indexación. Usarlos en la consulta
        exigiría coincidencia de palabra completa (p. ej. ``cas`` con
        ``as$`` no matchearía ``casa``). Con N-gramas crudos, ``cas`` ->
        {ca, as} sí intersecta con los N-gramas de ``^casa$``.

        Returns:
            Conjunto vacío si la consulta es más corta que el menor n
            (el llamante debe hacer fallback de escaneo verificado).
        """
        key: str = normalize(query.strip())
        if not key:
            return set()
        n: int | None = self._select_n(len(key))
        if n is None:
            return set()
        return {key[i : i + n] for i in range(len(key) - n + 1)}

    def _select_n(self, key_len: int) -> int | None:
        """Elige el mayor n viable (n <= longitud cruda de la consulta).

        Consultas cortas usan bigramas; largas, el mayor n disponible
        (posting lists más pequeñas = más selectivo). Devuelve None si
        la consulta es más corta que todos los n (fallback lineal).
        """
        best: int | None = None
        for n in self.n_values:
            if n <= key_len:
                best = n
        return best

    def add_word(self, word: str) -> int:
        """Indexa una palabra. Devuelve su id (idempotente por clave).

        Si la clave normalizada ya existe, añade la forma original como
        variante sin duplicar el id.
        """
        if not word or not word.strip():
            raise ValueError("word no puede estar vacía")
        original: str = word.strip()
        key: str = normalize(original)
        if not key:
            raise ValueError("word no normaliza a una clave válida")
        existing: int | None = self._key_to_id.get(key)
        if existing is not None:
            if original not in self._words[existing]:
                # Variante adicional de la misma clave: no reindexar
                # (los N-gramas de la clave ya están en el índice).
                self._words[existing].append(original)
            return existing
        wid: int = len(self._words)
        self._words.append([original])
        self._keys.append(key)
        self._key_to_id[key] = wid
        for ng in self._ngrams_for_key(key):
            posting: set[int] | None = self._index.get(ng)
            if posting is None:
                self._index[ng] = {wid}
            else:
                posting.add(wid)
        return wid

    def build(self, words: list[str]) -> int:
        """Indexa una lista de palabras. Devuelve nº de ids creados."""
        count: int = 0
        for w in words:
            if w and w.strip() and not w.strip().startswith("#"):
                before: int = len(self._words)
                self.add_word(w)
                if len(self._words) > before:
                    count += 1
        return count

    def posting(self, ngram: str) -> set[int]:
        """Posting list de un N-grama. Lookup O(1) promedio.

        Devuelve una copia para evitar mutación externa del índice.
        """
        existing: set[int] | None = self._index.get(ngram)
        return set(existing) if existing is not None else set()

    def contains(self, substring: str, limit: int = 20) -> list[str]:
        """Búsqueda por subcadena insensible a tildes. O(1) por N-grama.

        1. Tokeniza la consulta en N-gramas crudos (sin centinelas).
        2. Intersecciona posting lists empezando por la más pequeña.
        3. Verifica la subcadena normalizada sobre candidatos.
        Consultas más cortas que el menor n usan escaneo verificado O(N).

        Args:
            substring: Subcadena a buscar.
            limit: Máximo de resultados.

        Returns:
            Formas originales ordenadas (límite aplicado).
        """
        if limit <= 0:
            return []
        key: str = normalize(substring.strip())
        if not key:
            return []
        ngrams: set[str] = self.query_ngrams(substring)
        if not ngrams:
            # Consulta sub-N-grama (p. ej. 1 letra): escaneo verificado.
            out: list[str] = []
            for wid in range(len(self._words)):
                if key in self._keys[wid]:
                    out.extend(self._words[wid])
                    if len(out) >= limit:
                        break
            out.sort()
            return out[:limit]
        # Ordenar N-gramas por posting más pequeña primero (selectividad).
        ordered: list[str] = sorted(ngrams, key=lambda g: len(self._index.get(g, set())))
        first: set[int] | None = self._index.get(ordered[0])
        if not first:
            return []
        candidates: set[int] = set(first)
        for g in ordered[1:]:
            posting: set[int] | None = self._index.get(g)
            if not posting:
                return []
            candidates.intersection_update(posting)
            if not candidates:
                return []
        # Verificación exacta sobre la clave normalizada.
        out: list[str] = []
        for wid in candidates:
            if key in self._keys[wid]:
                out.extend(self._words[wid])
        out.sort()
        return out[:limit]

    def stats(self) -> dict[str, int | list[int]]:
        """Estadísticas del índice (para /stats y CLI)."""
        return {
            "words": len(self._words),
            "forms": sum(len(forms) for forms in self._words),
            "ngrams": len(self._index),
            "n_values": list(self.n_values),
        }
