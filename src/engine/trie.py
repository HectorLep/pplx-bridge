"""Trie y Radix tree para el motor léxico español.

Memoria eficiente: nodos con ``__slots__`` (sin ``__dict__`` por nodo).
Búsqueda por prefijo en O(p + k) donde p = len(prefijo), k = resultados.

Manejo de tildes/ñ:
    - Búsqueda insensible a tildes y mayúsculas: ``árbol`` == ``ARBOL``.
    - La ``ñ`` NUNCA se normaliza a ``n`` (``niño`` != ``nino``).
    - Se conserva la forma original para mostrar al usuario.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

__all__ = [
    "normalize",
    "TrieNode",
    "Trie",
    "RadixNode",
    "RadixTree",
]

# Marcador privado para proteger la ñ durante la descomposición NFD.
# En NFD, 'ñ' -> 'n' + U+0303 (combining tilde); si se eliminan las
# marcas Mn sin proteger, 'niño' colapsaría a 'nino'. Lo evitamos.
_ENIE_SENTINEL = "\x00"


def normalize(text: str) -> str:
    """Normaliza texto español para indexación insensible a tildes.

    - Aplica ``casefold`` (insensible a mayúsculas).
    - Elimina diacríticos (á->a, é->e, í->i, ó->o, ú->u, ü->u).
    - Preserva ``ñ`` y ``ç``-like intactos (ñ != n).

    Args:
        text: Cadena de entrada.

    Returns:
        Cadena normalizada.
    """
    if not text:
        return ""
    lowered: str = text.casefold()
    # Proteger eñes antes de NFD.
    lowered = lowered.replace("ñ", _ENIE_SENTINEL)
    decomposed: str = unicodedata.normalize("NFD", lowered)
    stripped: str = "".join(
        ch for ch in decomposed if unicodedata.category(ch) != "Mn"
    )
    return stripped.replace(_ENIE_SENTINEL, "ñ")


class TrieNode:
    """Nodo de Trie con memoria compacta."""

    __slots__ = ("children", "is_end", "forms")

    def __init__(self) -> None:
        self.children: dict[str, TrieNode] = {}
        self.is_end: bool = False
        # Formas originales que terminan aquí (p. ej. "árbol" y "ARBOL"
        # colapsan a la misma clave pero se guardan ambas formas).
        self.forms: list[str] = []


class Trie:
    """Trie estándar con claves normalizadas y formas originales.

    Complejidades:
        - ``insert``: O(m), m = longitud de la palabra.
        - ``search``: O(m).
        - ``autocomplete``: O(p + k·L) donde p = prefijo, k = resultados.
    """

    def __init__(self) -> None:
        self.root: TrieNode = TrieNode()
        self._size: int = 0  # nº de formas insertadas (incluye duplicados norm.)
        self._keys: int = 0  # nº de claves normalizadas terminales
        self._nodes: int = 1  # incluye raíz

    def __len__(self) -> int:
        """Número de formas insertadas."""
        return self._size

    def __contains__(self, word: str) -> bool:
        return self.search(word)

    @property
    def node_count(self) -> int:
        """Número de nodos (proxy de memoria)."""
        return self._nodes

    @property
    def key_count(self) -> int:
        """Número de claves normalizadas distintas."""
        return self._keys

    def insert(self, word: str) -> None:
        """Inserta una palabra conservando su forma original.

        Args:
            word: Palabra a insertar. Se ignora si es vacía.
        """
        if not word or not word.strip():
            return
        key: str = normalize(word.strip())
        if not key:
            return
        node: TrieNode = self.root
        for ch in key:
            nxt: TrieNode | None = node.children.get(ch)
            if nxt is None:
                nxt = TrieNode()
                node.children[ch] = nxt
                self._nodes += 1
            node = nxt
        if word not in node.forms:
            node.forms.append(word)
            self._size += 1
            if not node.is_end:
                node.is_end = True
                self._keys += 1

    def _find_node(self, key: str) -> TrieNode | None:
        """Devuelve el nodo terminal del prefijo/clave o None."""
        node: TrieNode | None = self.root
        for ch in key:
            if node is None:
                return None
            node = node.children.get(ch)
        return node

    def search(self, word: str) -> bool:
        """Búsqueda exacta insensible a tildes/mayúsculas. O(m)."""
        if not word:
            return False
        node: TrieNode | None = self._find_node(normalize(word.strip()))
        return bool(node is not None and node.is_end)

    def search_literal(self, word: str) -> bool:
        """Búsqueda exacta sensible a tildes (forma original registrada)."""
        if not word:
            return False
        node: TrieNode | None = self._find_node(normalize(word.strip()))
        return bool(node is not None and word in node.forms)

    def starts_with(self, prefix: str) -> bool:
        """Indica si existe alguna palabra con el prefijo dado. O(p)."""
        if prefix == "":
            return True
        return self._find_node(normalize(prefix)) is not None

    def forms_of(self, word: str) -> list[str]:
        """Devuelve las formas originales registradas para una clave."""
        node: TrieNode | None = self._find_node(normalize(word.strip()))
        if node is None or not node.is_end:
            return []
        return list(node.forms)

    def autocomplete(self, prefix: str, limit: int = 10) -> list[str]:
        """Completa un prefijo (insensible a tildes). O(p + k·L).

        Args:
            prefix: Prefijo a completar.
            limit: Máximo de resultados (<=0 → vacío).

        Returns:
            Lista de formas originales ordenadas alfabéticamente.
        """
        if limit <= 0:
            return []
        start: TrieNode | None = self._find_node(normalize(prefix))
        if start is None:
            return []
        # DFS iterativo para evitar recursión profunda.
        out: list[str] = []
        stack: list[TrieNode] = [start]
        while stack and len(out) < limit:
            node = stack.pop()
            if node.is_end:
                # Extender en orden para resultado determinista.
                for form in sorted(node.forms):
                    if len(out) >= limit:
                        break
                    out.append(form)
            # Hijos en orden inverso para que pop() visite a-z.
            for ch in sorted(node.children.keys(), reverse=True):
                if len(out) >= limit:
                    break
                stack.append(node.children[ch])
        # Nota: el DFS con límite recorre en orden lexicográfico (hijos
        # apilados en orden inverso), por lo que los primeros k resultados
        # ya son los menores. Se ordena al final para garantizar el
        # contrato "orden alfabético" con coste O(k log k) despreciable.
        out.sort()
        return out

    def delete(self, word: str) -> bool:
        """Elimina una forma original. Devuelve True si existía.

        Hace poda de ramas huérfanas para liberar memoria.
        """

        def _rec(node: TrieNode, key: str, depth: int) -> bool:
            if depth == len(key):
                if word not in node.forms:
                    return False
                node.forms.remove(word)
                self._size -= 1
                if not node.forms:
                    node.is_end = False
                    self._keys -= 1
                # Podar si es hoja sin formas.
                return len(node.children) == 0 and not node.is_end
            ch: str = key[depth]
            child: TrieNode | None = node.children.get(ch)
            if child is None:
                return False
            prune: bool = _rec(child, key, depth + 1)
            if prune:
                del node.children[ch]
                self._nodes -= 1
                return len(node.children) == 0 and not node.is_end
            return False

        if not word:
            return False
        if not self.search_literal(word):
            # La clave normalizada puede existir con otra forma: no borrar.
            return False
        _rec(self.root, normalize(word.strip()), 0)
        return True

    def all_words(self) -> list[str]:
        """Todas las formas ordenadas (solo para depuración/tests)."""
        return self.autocomplete("", limit=self._size if self._size > 0 else 10**9)


class RadixNode:
    """Nodo de árbol radix (trie comprimido)."""

    __slots__ = ("label", "children", "is_end", "forms")

    def __init__(self, label: str = "") -> None:
        self.label: str = label
        self.children: dict[str, RadixNode] = {}
        self.is_end: bool = False
        self.forms: list[str] = []


@dataclass
class RadixTree:
    """Radix tree (Patricia trie) con claves normalizadas.

    Comprime cadenas de un solo hijo en una arista ``label``, reduciendo
    ~30-50% los nodos frente al Trie clásico con el mismo vocabulario.
    Misma semántica de normalización que :class:`Trie`.
    """

    root: RadixNode = field(default_factory=RadixNode)
    _size: int = 0
    _keys: int = 0
    _nodes: int = 1

    def __len__(self) -> int:
        return self._size

    def __contains__(self, word: str) -> bool:
        return self.search(word)

    @property
    def node_count(self) -> int:
        return self._nodes

    @staticmethod
    def _common_prefix(a: str, b: str) -> int:
        """Longitud del prefijo común entre a y b."""
        n: int = min(len(a), len(b))
        i: int = 0
        while i < n and a[i] == b[i]:
            i += 1
        return i

    def insert(self, word: str) -> None:
        """Inserta una palabra conservando su forma original."""
        if not word or not word.strip():
            return
        key: str = normalize(word.strip())
        if not key:
            return
        node: RadixNode = self.root
        i: int = 0
        while i < len(key):
            ch: str = key[i]
            child: RadixNode | None = node.children.get(ch)
            if child is None:
                leaf = RadixNode(key[i:])
                leaf.is_end = True
                leaf.forms.append(word)
                node.children[ch] = leaf
                self._nodes += 1
                self._size += 1
                self._keys += 1
                return
            # Prefijo común entre el resto de la clave y la etiqueta.
            common: int = self._common_prefix(key[i:], child.label)
            if common == len(child.label):
                i += common
                node = child
                continue
            # División (split) del nodo.
            split = RadixNode(child.label[:common])
            self._nodes += 1
            # Re-etiquetar hijo existente.
            child.label = child.label[common:]
            split.children[child.label[0]] = child
            node.children[ch] = split
            i += common
            if i == len(key):
                if word not in split.forms:
                    if not split.is_end:
                        split.is_end = True
                        self._keys += 1
                    split.forms.append(word)
                    self._size += 1
                return
            leaf2 = RadixNode(key[i:])
            leaf2.is_end = True
            leaf2.forms.append(word)
            split.children[key[i]] = leaf2
            self._nodes += 1
            self._size += 1
            self._keys += 1
            return
        # La clave ya existe como camino exacto.
        if word not in node.forms:
            if not node.is_end:
                node.is_end = True
                self._keys += 1
            node.forms.append(word)
            self._size += 1

    def _find(self, key: str) -> RadixNode | None:
        """Nodo terminal exacto de la clave o None."""
        node: RadixNode = self.root
        i: int = 0
        while i < len(key):
            child: RadixNode | None = node.children.get(key[i])
            if child is None:
                return None
            label: str = child.label
            if key[i : i + len(label)] != label:
                # La etiqueta diverge de la clave: no existe clave exacta.
                return None
            i += len(label)
            node = child
        return node

    def search(self, word: str) -> bool:
        """Búsqueda exacta insensible a tildes/mayúsculas."""
        if not word:
            return False
        node: RadixNode | None = self._find(normalize(word.strip()))
        return bool(node is not None and node.is_end)

    def starts_with(self, prefix: str) -> bool:
        """Indica si existe alguna palabra con el prefijo dado."""
        if prefix == "":
            return True
        key: str = normalize(prefix)
        node: RadixNode = self.root
        i: int = 0
        while i < len(key):
            child: RadixNode | None = node.children.get(key[i])
            if child is None:
                return False
            label: str = child.label
            rest: str = key[i:]
            if rest.startswith(label):
                i += len(label)
                node = child
                continue
            # El prefijo termina dentro de la etiqueta, o viceversa.
            return label.startswith(rest)
        return True

    def autocomplete(self, prefix: str, limit: int = 10) -> list[str]:
        """Completa un prefijo (insensible a tildes)."""
        if limit <= 0:
            return []
        key: str = normalize(prefix)
        node: RadixNode = self.root
        i: int = 0
        while i < len(key):
            child: RadixNode | None = node.children.get(key[i])
            if child is None:
                return []
            label: str = child.label
            rest: str = key[i:]
            if rest.startswith(label):
                i += len(label)
                node = child
            elif label.startswith(rest):
                node = child
                i = len(key)
                break
            else:
                return []
        out: list[str] = []
        stack: list[RadixNode] = [node]
        while stack and len(out) < limit:
            cur: RadixNode = stack.pop()
            if cur.is_end:
                for form in sorted(cur.forms):
                    if len(out) >= limit:
                        break
                    out.append(form)
            for ch in sorted(cur.children.keys(), reverse=True):
                stack.append(cur.children[ch])
        return out
